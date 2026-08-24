"""Ciclo de tool use: armado del contexto y acumulación de la respuesta."""

from types import SimpleNamespace

import pytest

from app.agente import loop


# --- prompt del sistema -----------------------------------------------------

def test_el_bloque_estatico_se_cachea_y_el_dinamico_no(settings_de_prueba):
    """El prefijo con instrucciones y conocimiento es idéntico en cada turno de
    cada conversación; el estado cambia siempre. TTL de una hora porque con
    decenas de conversaciones por día los huecos superan los 5 minutos."""
    bloques = loop.armar_sistema({"zona": "quito_y_valles"})
    assert len(bloques) == 2
    assert bloques[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "cache_control" not in bloques[1]


def test_el_conocimiento_va_en_el_prefijo(settings_de_prueba):
    """Sin esto el agente contesta desde lo que "sabe" de películas para vidrio,
    que para estos productos es falso."""
    estatico = loop.armar_sistema(None)[0]["text"]
    assert "anti motín" in estatico
    assert "no reduce el ruido" in estatico.lower() or "ruido" in estatico


def test_inyecta_lo_que_ya_sabe(settings_de_prueba):
    dinamico = loop.armar_sistema({"zona": "quito_y_valles", "metros_cuadrados": 20})[1]["text"]
    assert "zona: quito_y_valles" in dinamico
    assert "metros_cuadrados: 20" in dinamico
    assert "No vuelva a preguntar" in dinamico


def test_sin_datos_lo_dice(settings_de_prueba):
    assert "Todavia nada" in loop.armar_sistema(None)[1]["text"]


def test_inyecta_la_hora_de_ecuador(settings_de_prueba):
    """Sin esto el agente no puede saludar bien —no sabe si es la mañana o la
    tarde— ni sabe que está contestando fuera de horario."""
    dinamico = loop.armar_sistema(None)[1]["text"]
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
    assert historial[0]["role"] == "user"
    assert historial[0]["content"] == "buenas\nnecesito lamina\npara una oficina"


@pytest.mark.db
async def test_el_vendedor_cuenta_como_asistente(pool_en_transaccion):
    """Desde el lado del cliente vino del mismo número."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensaje(conexion, id_conv, "cliente", "hola")
    await _mensaje(conexion, id_conv, "vendedor", "buenas, le escribe Juan")
    await _mensaje(conexion, id_conv, "cliente", "perfecto")

    historial = await loop.armar_historial(id_conv)
    assert [m["role"] for m in historial] == ["user", "assistant", "user"]


@pytest.mark.db
async def test_no_termina_con_un_mensaje_del_asistente(pool_en_transaccion):
    """Si termina con el asistente, la API devuelve 400 y la conversación queda
    sin respuesta. Pasa si un vendedor escribió último."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensaje(conexion, id_conv, "cliente", "hola")
    await _mensaje(conexion, id_conv, "vendedor", "ya le respondo")

    historial = await loop.armar_historial(id_conv)
    assert historial[-1]["role"] == "user"


@pytest.mark.db
async def test_los_adjuntos_se_anuncian(pool_en_transaccion):
    """El cliente manda la foto de la ventana: el modelo tiene que saber que
    llegó algo, aunque todavía no lo lea."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensaje(conexion, id_conv, "cliente", "mide 1.20 x 2.40", tipo="imagen")

    historial = await loop.armar_historial(id_conv)
    assert "archivo de tipo imagen" in historial[0]["content"]
    assert "1.20 x 2.40" in historial[0]["content"]


# --- acumulación del texto --------------------------------------------------

def _bloque_texto(texto):
    return SimpleNamespace(type="text", text=texto)


def _bloque_herramienta(id_, nombre, entrada):
    return SimpleNamespace(type="tool_use", id=id_, name=nombre, input=entrada)


def _mensaje_api(bloques):
    return SimpleNamespace(
        content=bloques,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0),
    )


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

    respuestas = iter([
        _mensaje_api([
            _bloque_texto("No es antibalas, es anti motín."),
            _bloque_herramienta("t1", "guardar_dato", {"campo": "aplicacion", "valor": "local"}),
        ]),
        _mensaje_api([_bloque_texto("¿En qué ciudad está el local?")]),
    ])

    class _Mensajes:
        async def create(self, **kwargs):
            return next(respuestas)

    monkeypatch.setattr(
        loop, "_cliente", lambda: SimpleNamespace(messages=_Mensajes())
    )

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
