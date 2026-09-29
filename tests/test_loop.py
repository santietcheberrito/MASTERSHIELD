"""Ciclo de tool use: armado del contexto y acumulación de la respuesta."""

import pytest

from app import contexto, textos

from app.agente import herramientas, loop


@pytest.fixture(autouse=True)
def _sin_busqueda_en_la_base(monkeypatch):
    """El contexto del turno busca respuestas con embeddings de OpenAI. Aca se
    prueba el ciclo, no la busqueda."""
    async def _armar(*args, **kwargs):
        return ""
    monkeypatch.setattr(contexto, "armar", _armar)


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


def test_el_contexto_del_turno_va_en_lo_que_cambia(settings_de_prueba):
    """Las respuestas y la informacion salen de la base segun el caso: van
    despues del prefijo fijo para no romper el cache."""
    partes = loop.armar_sistema(None, contexto_del_turno="## pedir_pedido")
    assert "## pedir_pedido" in partes[1]
    assert "## pedir_pedido" not in partes[0]


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
async def _conversacion_con_precio(conexion, texto_del_agente):
    """Un turno donde el agente consulta el precio y después dice `texto`."""
    id_conv = await _conversacion(conexion)
    await conexion.execute(
        "UPDATE conversaciones SET datos = $2::jsonb WHERE id = $1", id_conv,
        {"nombre": "Ana", "linea": "arquitectonico", "objetivo": "control_solar",
         "superficie": "ventanas", "zona": "quito_y_valles", "metros_cuadrados": 25},
    )
    # La lista ya le llego: con la lista en el mismo turno, lo que el modelo
    # escriba con precios se saca entero y no se llega a verificar.
    precios = await herramientas.consultar_precio(id_conv)
    await _mensaje(conexion, id_conv, "agente", textos.lista_de_precios(precios))
    await _mensaje(conexion, id_conv, "cliente", "cuanto sale?")
    return id_conv, _ProveedorFalso([
        Salida(llamadas=[Llamada("t1", "consultar_precio", {})]),
        Salida(texto=texto_del_agente),
    ])


async def test_el_precio_por_metro_consultado_en_el_turno_si_se_envia(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    conexion = pool_en_transaccion
    id_conv, proveedor = await _conversacion_con_precio(
        conexion, "La de 10 años está en 37 dólares más IVA el metro, y la de 5 en 25."
    )
    monkeypatch.setattr(loop, "_cliente", lambda: proveedor)

    r = await loop.responder(id_conv)

    assert r.precio_bloqueado is False
    assert "37" in r.texto and "25" in r.texto


async def test_un_total_no_se_envia_aunque_la_cuenta_este_bien(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    """25 m² por 42 son 1050 y la cuenta es correcta, pero el cliente pidió que
    el agente no calcule: el número final sale de las medidas que toma el asesor
    en la visita. Un total no lo autoriza nadie."""
    conexion = pool_en_transaccion
    id_conv, proveedor = await _conversacion_con_precio(
        conexion, "Con 25 m² le queda en 1050 dólares más IVA."
    )
    monkeypatch.setattr(loop, "_cliente", lambda: proveedor)

    r = await loop.responder(id_conv)

    assert r.precio_bloqueado is True
    assert "1050" not in r.texto


def test_el_telefono_del_canal_va_marcado_como_sin_confirmar(settings_de_prueba):
    """Sembrado en `datos`, caía bajo "no vuelva a preguntar nada de esto" y el
    agente cerraba con un número que la persona nunca eligió."""
    dinamico = loop.armar_sistema(
        {"telefono": "+593999123456"}, identificador="+593999123456"
    )[1]

    assert "desde el que le escriben" in dinamico
    assert "se confirma antes de cerrar" in dinamico


def test_un_telefono_distinto_al_del_canal_no_se_repregunta(settings_de_prueba):
    """Si dio otro número, ya lo eligió: volver a ofrecerle la alternativa es
    hacerle contestar dos veces lo mismo."""
    dinamico = loop.armar_sistema(
        {"telefono": "+59321234567"}, identificador="+593999123456"
    )[1]

    assert "desde el que le escriben" not in dinamico


# --- después de que intervino un asesor -------------------------------------

@pytest.mark.db
async def test_el_agente_puede_decidir_no_contestar(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    """Un "gracias" después de que un asesor cerró el tema no pide respuesta.
    Sin esta herramienta, la única forma de callarse era devolver texto vacío,
    que es indistinguible de un turno que falló."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensaje(conexion, id_conv, "cliente", "listo, muchas gracias!")

    proveedor = _ProveedorFalso([
        Salida(texto="", llamadas=[Llamada("t1", "cerrar_sin_responder",
                                           {"motivo": "es un agradecimiento"})]),
    ])
    monkeypatch.setattr(loop, "_cliente", lambda: proveedor)

    r = await loop.responder(id_conv)

    assert r.texto == ""
    assert r.silencio_deliberado is True
    assert await conexion.fetchval(
        "SELECT count(*) FROM eventos WHERE conversacion_id = $1 "
        "AND tipo = 'silencio_deliberado'", id_conv,
    ) == 1


@pytest.mark.db
async def test_decidir_callarse_corta_el_turno(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    """Seguir el ciclo le daría la oportunidad de escribir algo después de haber
    dicho que no hace falta, y lo que se manda es lo último que dijo."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensaje(conexion, id_conv, "cliente", "perfecto")

    proveedor = _ProveedorFalso([
        Salida(texto="", llamadas=[Llamada("t1", "cerrar_sin_responder",
                                           {"motivo": "no pide respuesta"})]),
        Salida(texto="¡Quedo a las órdenes!"),  # no debería llegar a pedirla
    ])
    monkeypatch.setattr(loop, "_cliente", lambda: proveedor)

    r = await loop.responder(id_conv)

    assert r.texto == ""
    assert r.iteraciones == 1


@pytest.mark.db
async def test_avisa_que_hubo_un_asesor_en_la_conversacion(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    """Los mensajes del vendedor llegan al historial como si fueran del agente,
    porque desde el lado del cliente vinieron del mismo número. Sin este aviso
    el agente cree que los dijo él."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensaje(conexion, id_conv, "cliente", "hola")
    await _mensaje(conexion, id_conv, "vendedor", "buenas, soy Andrés de MasterShield")
    await _mensaje(conexion, id_conv, "cliente", "gracias!")

    vistos = {}

    class _Espia(_ProveedorFalso):
        def mensajes_iniciales(self, sistema, historial):
            vistos["sistema"] = "\n".join(sistema)
            return list(historial)

    proveedor = _Espia([Salida(texto="")])
    monkeypatch.setattr(loop, "_cliente", lambda: proveedor)

    await loop.responder(id_conv)

    assert "Un asesor MS estuvo en esta conversacion" in vistos["sistema"]
    assert "cerrar_sin_responder" in vistos["sistema"]


@pytest.mark.db
async def test_sin_asesor_no_se_inyecta_ese_contexto(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensaje(conexion, id_conv, "cliente", "hola")

    vistos = {}

    class _Espia(_ProveedorFalso):
        def mensajes_iniciales(self, sistema, historial):
            vistos["sistema"] = "\n".join(sistema)
            return list(historial)

    monkeypatch.setattr(loop, "_cliente", lambda: _Espia([Salida(texto="Buenas tardes")]))

    await loop.responder(id_conv)

    assert "Un asesor MS estuvo" not in vistos["sistema"]


@pytest.mark.db
async def test_si_se_agotan_las_iteraciones_igual_se_contesta(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    """Alguien que dice "es para mi oficina, estoy en Guayaquil" deja cinco
    datos de golpe, y cada uno consume una vuelta. Sin esto el turno se quedaba
    sin texto: la persona escribió y no le contestó nadie."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensaje(conexion, id_conv, "cliente", "es para mi oficina, estoy en Guayaquil")

    # Siempre llama herramientas, nunca escribe: agota el tope.
    guardando = [
        Salida(llamadas=[Llamada(f"t{n}", "guardar_dato",
                                 {"campo": "aplicacion", "valor": "oficina"})])
        for n in range(20)
    ]
    proveedor = _ProveedorFalso(guardando)
    sin_herramientas = {}

    completar_real = proveedor.completar

    async def _completar(mensajes, herramientas):
        if not herramientas:
            sin_herramientas["si"] = True
            return Salida(texto="En Guayaquil el metro está en 47 más IVA.")
        return await completar_real(mensajes, herramientas)

    monkeypatch.setattr(proveedor, "completar", _completar)
    monkeypatch.setattr(loop, "_cliente", lambda: proveedor)

    r = await loop.responder(id_conv)

    assert sin_herramientas.get("si"), "se pide la respuesta sin herramientas"
    assert "47" in r.texto, "la persona recibe algo"
