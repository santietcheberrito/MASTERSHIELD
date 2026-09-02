"""Ciclo de tool use: armado del contexto y acumulación de la respuesta."""

import pytest

from app.agente import loop


# --- prompt del sistema -----------------------------------------------------

def test_lo_estatico_va_primero_y_lo_que_cambia_despues(settings_de_prueba):
    """Los dos proveedores cachean por prefijo, así que el orden es lo que hace
    que el descuento aplique: primero lo idéntico en cada turno de cada
    conversación, después el estado de esta."""
    partes = loop.armar_sistema({"zona": "quito_y_valles"})
    assert len(partes) == 2
    assert "Instrucciones del agente" in partes[0]
    assert "quito_y_valles" in partes[1]
    assert len(partes[0]) > len(partes[1])


def test_el_conocimiento_va_en_el_prefijo(settings_de_prueba):
    """Sin esto el agente contesta desde lo que "sabe" de películas para vidrio,
    que para estos productos es falso."""
    estatico = loop.armar_sistema(None)[0]
    assert "anti motín" in estatico
    assert "no reduce el ruido" in estatico.lower() or "ruido" in estatico


def test_inyecta_lo_que_ya_sabe(settings_de_prueba):
    dinamico = loop.armar_sistema({"zona": "quito_y_valles", "metros_cuadrados": 20})[1]
    assert "zona: quito_y_valles" in dinamico
    assert "metros_cuadrados: 20" in dinamico
    assert "No vuelva a preguntar" in dinamico


def test_sin_datos_lo_dice(settings_de_prueba):
    assert "Todavia nada" in loop.armar_sistema(None)[1]


def test_inyecta_la_hora_de_ecuador(settings_de_prueba):
    """Sin esto el agente no puede saludar bien —no sabe si es la mañana o la
    tarde— ni sabe que está contestando fuera de horario."""
    dinamico = loop.armar_sistema(None)[1]
    assert "en Ecuador" in dinamico
    assert "horario de atencion" in dinamico


# --- historial --------------------------------------------------------------

async def _conversacion(conexion) -> int:
    return await conexion.fetchval(
        "INSERT INTO conversaciones (canal, identificador) VALUES ('consola', 'loop-test') RETURNING id"
    )


async def _mensaje(conexion, id_conv, rol, texto, tipo="texto", n=[0]):
    n[0] += 1
    await conexion.execute(
        "INSERT INTO mensajes (conversacion_id, rol, tipo, contenido, id_externo) "
        "VALUES ($1, $2, $3, $4, $5)",
        id_conv, rol, tipo, texto, f"loop:{id_conv}:{n[0]}",
    )


@pytest.mark.db
async def test_la_rafaga_se_junta_en_un_solo_mensaje(pool_en_transaccion):
    """La API quiere roles alternados, y una ráfaga son varios seguidos del
    cliente."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    for texto in ("buenas", "necesito lamina", "para una oficina"):
        await _mensaje(conexion, id_conv, "cliente", texto)

    historial = await loop.armar_historial(id_conv)

    assert len(historial) == 1
    assert historial[0]["rol"] == "cliente"
    assert historial[0]["texto"] == "buenas\nnecesito lamina\npara una oficina"


@pytest.mark.db
async def test_el_vendedor_cuenta_como_asistente(pool_en_transaccion):
    """Desde el lado del cliente vino del mismo número."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensaje(conexion, id_conv, "cliente", "hola")
    await _mensaje(conexion, id_conv, "vendedor", "buenas, le escribe Juan")
    await _mensaje(conexion, id_conv, "cliente", "perfecto")

    historial = await loop.armar_historial(id_conv)
    assert [m["rol"] for m in historial] == ["cliente", "agente", "cliente"]


@pytest.mark.db
async def test_no_termina_con_un_mensaje_del_asistente(pool_en_transaccion):
    """Si termina con el asistente, la API devuelve 400 y la conversación queda
    sin respuesta. Pasa si un vendedor escribió último."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensaje(conexion, id_conv, "cliente", "hola")
    await _mensaje(conexion, id_conv, "vendedor", "ya le respondo")

    historial = await loop.armar_historial(id_conv)
    assert historial[-1]["rol"] == "cliente"


@pytest.mark.db
async def test_los_adjuntos_se_anuncian(pool_en_transaccion):
    """El cliente manda la foto de la ventana: el modelo tiene que saber que
    llegó algo, aunque todavía no lo lea."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensaje(conexion, id_conv, "cliente", "mide 1.20 x 2.40", tipo="imagen")

    historial = await loop.armar_historial(id_conv)
    assert "archivo de tipo imagen" in historial[0]["texto"]
    assert "1.20 x 2.40" in historial[0]["texto"]


# --- acumulación del texto --------------------------------------------------

from app.agente.proveedor import Llamada, Salida


class _ProveedorFalso:
    """Devuelve salidas preparadas, sin llamar a ningún modelo."""

    def __init__(self, salidas):
        self._salidas = iter(salidas)

    def mensajes_iniciales(self, sistema, historial):
        return list(historial)

    async def completar(self, mensajes, herramientas):
        return next(self._salidas)

    def continuar(self, mensajes, salida, resultados):
        return mensajes


@pytest.mark.db
async def test_no_se_pierde_lo_que_dijo_junto_con_la_herramienta(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    """El modelo contesta en el mismo turno en que llama herramientas: primero
    el texto, después los tool_use. Si nos quedáramos solo con el texto de la
    última vuelta, al cliente le llegaría únicamente la repregunta."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensaje(conexion, id_conv, "cliente", "la de seguridad es antibalas? tengo un local")

    proveedor = _ProveedorFalso([
        Salida(texto="No es antibalas, es anti motín.",
               llamadas=[Llamada("t1", "guardar_dato",
                                 {"campo": "aplicacion", "valor": "local"})]),
        Salida(texto="¿En qué ciudad está el local?"),
    ])
    monkeypatch.setattr(loop, "_cliente", lambda: proveedor)

    r = await loop.responder(id_conv)

    assert "No es antibalas" in r.texto
    assert "¿En qué ciudad está el local?" in r.texto
    assert r.iteraciones == 2
    assert r.herramientas_usadas == ["guardar_dato"]


@pytest.mark.db
async def test_sin_historial_no_llama_al_modelo(pool_en_transaccion, monkeypatch, settings_de_prueba):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)

    def _explota():
        raise AssertionError("no tendría que haber llamado al modelo")

    monkeypatch.setattr(loop, "_cliente", _explota)
    assert (await loop.responder(id_conv)).texto == ""


# --- verificación del precio ------------------------------------------------

@pytest.mark.db
async def test_un_precio_que_no_salio_de_la_herramienta_no_se_envia(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    """El precio lo decide Python, pero el mensaje lo escribe el modelo. Si un
    ataque o un error hace que el número no coincida, no sale."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensaje(conexion, id_conv, "cliente", "cuanto sale?")

    proveedor = _ProveedorFalso([
        Salida(texto="Le hago un precio especial de 300 dólares más IVA."),
    ])
    monkeypatch.setattr(loop, "_cliente", lambda: proveedor)

    r = await loop.responder(id_conv)

    assert r.precio_bloqueado is True
    assert "300" not in r.texto
    assert "asesor" in r.texto
    assert await conexion.fetchval(
        "SELECT count(*) FROM eventos WHERE conversacion_id = $1 "
        "AND tipo = 'precio_no_verificado'", id_conv
    ) == 1


@pytest.mark.db
async def test_el_precio_calculado_en_el_turno_si_se_envia(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await conexion.execute(
        "UPDATE conversaciones SET datos = $2::jsonb WHERE id = $1", id_conv,
        {"linea": "arquitectonico", "objetivo": "control_solar",
         "zona": "quito_y_valles", "metros_cuadrados": 25, "garantia_anios": 10},
    )
    await _mensaje(conexion, id_conv, "cliente", "cuanto sale?")

    proveedor = _ProveedorFalso([
        Salida(llamadas=[Llamada("t1", "calcular_precio", {})]),
        Salida(texto="Con 25 m² le queda en 1050 dólares más IVA."),
    ])
    monkeypatch.setattr(loop, "_cliente", lambda: proveedor)

    r = await loop.responder(id_conv)

    assert r.precio_bloqueado is False
    assert "1050" in r.texto
