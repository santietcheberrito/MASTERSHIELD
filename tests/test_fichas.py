"""Las descripciones oficiales de producto y el video que las acompaña.

MasterShield pidio que cuando alguien pregunte por un producto reciba el bloque
de texto que escribio la empresa, entero. Lo que se verifica aca es que ese
texto no pase por el modelo ni por el partido de mensajes, que no se repita, y
que el video salga una sola vez y nunca con el vehicular.
"""

import re
from pathlib import Path

import pytest
import respx

from app import fichas, worker
from app.agente import herramientas, loop
from app import contexto


@pytest.fixture(autouse=True)
def _sin_busqueda_en_la_base(monkeypatch):
    """El contexto del turno busca respuestas con embeddings de OpenAI. Aca se
    prueban las fichas, no la busqueda."""
    async def _armar(*args, **kwargs):
        return ""
    monkeypatch.setattr(contexto, "armar", _armar)
from app.agente.loop import Respuesta
from app.agente.proveedor import Llamada, Salida
from app.canales import whatsapp
from app.precios import configuracion

PRIVACIDAD = "privacidad_arquitectonica"
SEGURIDAD = "seguridad_arquitectonica"
VEHICULAR = "seguridad_vehicular"


# --- los archivos ------------------------------------------------------------

def test_cada_producto_tiene_su_ficha():
    """Un producto sin ficha no se puede pedir, y el modelo no se entera: la
    herramienta directamente no lo ofrece."""
    ids = [p["id"] for p in configuracion()["productos"]]
    assert fichas.disponibles() == ids


@pytest.mark.parametrize("producto", [p["id"] for p in configuracion()["productos"]])
def test_las_fichas_tratan_de_usted(producto):
    """El agente nunca tutea, y la ficha sale con su voz. El texto original de
    privacidad decía "tus ventanas"."""
    texto = fichas.texto(producto)
    assert texto
    assert not re.search(r"\b(tú|tus?|te|contigo|tuyas?|tuyos?)\b", texto, re.IGNORECASE)


def test_el_video_entra_en_whatsapp():
    """Meta no acepta videos de más de 16 MB. El original pesaba 87."""
    video = fichas.VIDEO_PRODUCTOS.ruta
    assert video.exists(), "falta correr scripts/preparar_video.py"
    assert video.stat().st_size < 16 * 1024 * 1024


def test_el_vehicular_no_lleva_video():
    """El clip muestra los productos arquitectónicos."""
    assert fichas.lleva_video(PRIVACIDAD) is True
    assert fichas.lleva_video("control_solar_techos") is True
    assert fichas.lleva_video(VEHICULAR) is False


def test_el_video_lleva_el_mensaje_del_cliente():
    """Pedido del cliente: el video no llega solo. El texto es el del 11/9/2026:
    el video muestra los trabajos y las terminaciones, no el material."""
    video = fichas.VIDEO_PRODUCTOS
    assert video.leyenda == (
        "Aquí le dejo un video para que vea cómo son nuestros trabajos y la calidad "
        "de nuestras terminaciones 💪")
    # En el historial queda que se mandó y qué decía, para que el modelo no lo
    # vuelva a decir con sus palabras.
    assert video.contenido == f"{video.descripcion} {video.leyenda}"


# --- conversaciones ----------------------------------------------------------

async def _conversacion(conexion) -> int:
    id_conv = await conexion.fetchval(
        "INSERT INTO conversaciones (canal, identificador) "
        "VALUES ('consola', 'fichas-test') RETURNING id")
    await conexion.execute(
        "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo) "
        "VALUES ($1, 'agente', 'Hola, bienvenido', 'fichas-bienvenida')", id_conv)
    return id_conv


async def _del_cliente(conexion, id_conv, texto, externo) -> int:
    return await conexion.fetchval(
        "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo) "
        "VALUES ($1, 'cliente', $2, $3) RETURNING id", id_conv, texto, externo)


# --- armar el envio ----------------------------------------------------------

@pytest.mark.db
async def test_el_video_va_pegado_a_la_primera_ficha_arquitectonica(pool_en_transaccion):
    id_conv = await _conversacion(pool_en_transaccion)

    partes = await fichas.armar_envio(id_conv, [VEHICULAR, PRIVACIDAD, SEGURIDAD])

    assert partes == [
        fichas.texto(VEHICULAR),
        fichas.texto(PRIVACIDAD), fichas.VIDEO_PRODUCTOS,
        fichas.texto(SEGURIDAD),
    ]


@pytest.mark.db
async def test_el_video_sale_una_vez_por_conversacion(pool_en_transaccion):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await conexion.execute(
        "INSERT INTO mensajes (conversacion_id, rol, contenido, tipo, id_externo) "
        "VALUES ($1, 'agente', $2, 'video', 'fichas-video')",
        id_conv, fichas.VIDEO_PRODUCTOS.descripcion)

    assert await fichas.armar_envio(id_conv, [SEGURIDAD]) == [fichas.texto(SEGURIDAD)]


# --- la herramienta ----------------------------------------------------------

@pytest.mark.db
async def test_enviar_ficha_no_manda_nada_por_su_cuenta(pool_en_transaccion, settings_de_prueba):
    """La manda el worker. La herramienta valida y le dice al modelo que no la
    repita."""
    id_conv = await _conversacion(pool_en_transaccion)
    r = await herramientas.enviar_ficha(id_conv, PRIVACIDAD)
    assert r["se_envia"] is True
    assert "tal cual" in r["mensaje"]


@pytest.mark.db
async def test_una_ficha_ya_recibida_no_se_repite(pool_en_transaccion, settings_de_prueba):
    """Quien ya leyó el bloque y pregunta un detalle quiere el detalle."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await conexion.execute(
        "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo) "
        "VALUES ($1, 'agente', $2, 'fichas-ya')", id_conv, fichas.texto(PRIVACIDAD))

    r = await herramientas.enviar_ficha(id_conv, PRIVACIDAD)
    assert r["se_envia"] is False
    assert r["ya_la_recibio"] is True


@pytest.mark.db
async def test_un_producto_sin_ficha_se_rechaza(pool_en_transaccion, settings_de_prueba):
    id_conv = await _conversacion(pool_en_transaccion)
    r = await herramientas.enviar_ficha(id_conv, "polarizado")
    assert "error" in r
    assert PRIVACIDAD in r["productos_validos"]


# --- el loop -----------------------------------------------------------------

class _ProveedorFalso:
    def __init__(self, salidas):
        self._salidas = iter(salidas)

    def mensajes_iniciales(self, sistema, historial):
        return list(historial)

    async def completar(self, mensajes, herramientas):
        return next(self._salidas)

    def continuar(self, mensajes, salida, resultados):
        return mensajes


@pytest.mark.db
async def test_el_loop_junta_las_fichas_pedidas(pool_en_transaccion, monkeypatch, settings_de_prueba):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _del_cliente(conexion, id_conv, "qué es la lámina de privacidad?", "fichas-1")

    proveedor = _ProveedorFalso([
        Salida(texto="", llamadas=[Llamada("t1", "enviar_ficha", {"producto": PRIVACIDAD})]),
        Salida(texto="¿Para qué espacio sería la instalación?"),
    ])
    monkeypatch.setattr(loop, "_cliente", lambda: proveedor)

    r = await loop.responder(id_conv)

    assert r.fichas == [PRIVACIDAD]
    assert r.texto == "¿Para qué espacio sería la instalación?"


@pytest.mark.db
async def test_no_salen_mas_de_dos_fichas_por_mensaje(pool_en_transaccion, monkeypatch, settings_de_prueba):
    """A "¿qué productos tienen?" no se le manda el catálogo entero."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _del_cliente(conexion, id_conv, "qué productos tienen?", "fichas-2")

    proveedor = _ProveedorFalso([
        Salida(texto="", llamadas=[
            Llamada("t1", "enviar_ficha", {"producto": "control_solar_ventanas"}),
            Llamada("t2", "enviar_ficha", {"producto": PRIVACIDAD}),
            Llamada("t3", "enviar_ficha", {"producto": SEGURIDAD}),
            Llamada("t4", "enviar_ficha", {"producto": PRIVACIDAD}),
        ]),
        Salida(texto="¿Qué le gustaría resolver?"),
    ])
    monkeypatch.setattr(loop, "_cliente", lambda: proveedor)

    r = await loop.responder(id_conv)
    assert r.fichas == ["control_solar_ventanas", PRIVACIDAD]


@pytest.mark.db
async def test_el_video_enviado_no_se_lee_como_si_lo_mandara_el_cliente(pool_en_transaccion):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _del_cliente(conexion, id_conv, "qué es la de seguridad?", "fichas-3")
    await conexion.execute(
        "INSERT INTO mensajes (conversacion_id, rol, contenido, tipo, id_externo) "
        "VALUES ($1, 'agente', $2, 'video', 'fichas-v')", id_conv, fichas.VIDEO_PRODUCTOS.descripcion)
    await _del_cliente(conexion, id_conv, "ok", "fichas-4")

    historial = await loop.armar_historial(id_conv)
    del_agente = [m["texto"] for m in historial if m["rol"] == "agente"]
    assert del_agente == [fichas.VIDEO_PRODUCTOS.descripcion]


# --- el worker ---------------------------------------------------------------

async def _nada(*a, **k):
    return None


def _preparar_worker(monkeypatch, respuesta, enviados, video_falla=False):
    from app import worker

    async def _responder(_conversacion, _hasta=None):
        return respuesta

    async def _enviar(turno, texto):
        enviados.append(texto)
        return f"consola:{len(enviados)}"

    async def _enviar_video(turno, video):
        if video_falla:
            return None
        enviados.append(video)
        return f"consola:{len(enviados)}"

    async def _normal(_):
        return False

    monkeypatch.setattr(worker.loop, "responder", _responder)
    monkeypatch.setattr(worker, "_uso_anomalo", _normal)
    monkeypatch.setattr(worker, "_enviar", _enviar)
    monkeypatch.setattr(worker, "_enviar_video", _enviar_video)
    monkeypatch.setattr(worker, "_mostrar_escribiendo", _nada)
    monkeypatch.setattr(worker.asyncio, "sleep", _nada)
    monkeypatch.setattr(worker.humanizacion, "demora_de_escritura", lambda t: 0.0)
    return worker


@pytest.mark.db
async def test_la_ficha_sale_entera_antes_que_el_texto_y_con_el_video(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    id_msg = await _del_cliente(conexion, id_conv, "qué es la de privacidad?", "fichas-5")
    enviados = []
    worker = _preparar_worker(monkeypatch, Respuesta(
        texto="¿Para qué espacio sería la instalación?", fichas=[PRIVACIDAD],
        introduccion_fichas="Claro que sí, Ana. Le comparto la información de la lámina:"),
        enviados)

    await worker.procesar_turno(worker.Turno(
        conversacion_id=id_conv, texto="x", ids_mensajes=[id_msg], canal="consola"))

    assert enviados == [
        "Claro que sí, Ana. Le comparto la información de la lámina:",
        fichas.texto(PRIVACIDAD),  # un solo mensaje, sin partir
        fichas.VIDEO_PRODUCTOS,
        "¿Para qué espacio sería la instalación?",
    ]
    tipos = await conexion.fetch(
        "SELECT tipo FROM mensajes WHERE conversacion_id = $1 AND rol = 'agente' "
        "AND id_externo LIKE 'consola:%' ORDER BY id", id_conv)
    assert [t["tipo"] for t in tipos] == ["texto", "texto", "video", "texto"]
    assert await fichas.video_ya_enviado(id_conv) is True


@pytest.mark.db
async def test_si_el_video_falla_la_respuesta_sale_igual(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    id_msg = await _del_cliente(conexion, id_conv, "qué es la de seguridad?", "fichas-6")
    enviados = []
    worker = _preparar_worker(monkeypatch, Respuesta(
        texto="¿Es para su casa o su negocio?", fichas=[SEGURIDAD]), enviados, video_falla=True)

    await worker.procesar_turno(worker.Turno(
        conversacion_id=id_conv, texto="x", ids_mensajes=[id_msg], canal="consola"))

    assert enviados == [fichas.texto(SEGURIDAD), "¿Es para su casa o su negocio?"]
    # No quedó registrado como enviado: la próxima ficha lo intenta de nuevo.
    assert await fichas.video_ya_enviado(id_conv) is False


# --- WhatsApp ----------------------------------------------------------------

@pytest.fixture
def video(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(whatsapp, "_subidos", {})
    ruta = tmp_path / "video.mp4"
    ruta.write_bytes(b"\x00" * 64)
    return ruta


@respx.mock
async def test_el_video_se_sube_una_vez_y_se_reusa(respx_mock, video):
    subida = respx_mock.post("https://graph.facebook.com/v23.0/123/media").respond(
        200, json={"id": "MEDIA-1"})
    envio = respx_mock.post("https://graph.facebook.com/v23.0/123/messages").respond(
        200, json={"messages": [{"id": "wamid.V"}]})

    enviado = await whatsapp.enviar_video(
        "tok", "123", "+593987112233", video, leyenda="Le dejo un video")
    assert enviado == "whatsapp:wamid.V"
    await whatsapp.enviar_video("tok", "123", "+593987112233", video)

    assert subida.call_count == 1
    assert envio.call_count == 2
    import json
    cuerpo = json.loads(envio.calls[0].request.content)
    # La leyenda va dentro del mismo mensaje, no como un texto aparte que
    # podría llegar antes o después del video.
    assert cuerpo["type"] == "video"
    assert cuerpo["video"] == {"id": "MEDIA-1", "caption": "Le dejo un video"}
    assert json.loads(envio.calls[1].request.content)["video"] == {"id": "MEDIA-1"}


@respx.mock
async def test_si_meta_rechaza_el_id_guardado_se_sube_de_nuevo(respx_mock, video):
    """Un id subido dura 30 días y puede borrarse del lado de Meta. Eso no se
    ve hasta que se usa."""
    subida = respx_mock.post("https://graph.facebook.com/v23.0/123/media").mock(side_effect=[
        respx.MockResponse(200, json={"id": "MEDIA-VIEJO"}),
        respx.MockResponse(200, json={"id": "MEDIA-NUEVO"}),
    ])
    respx_mock.post("https://graph.facebook.com/v23.0/123/messages").mock(side_effect=[
        respx.MockResponse(400, json={"error": {"code": 131053, "message": "Media upload error"}}),
        respx.MockResponse(200, json={"messages": [{"id": "wamid.V2"}]}),
    ])

    assert await whatsapp.enviar_video("tok", "123", "+593987112233", video) == "whatsapp:wamid.V2"
    assert subida.call_count == 2


# --- la lista de precios y los textos fijos, en el worker ---------------------

@pytest.mark.db
async def test_la_introduccion_de_la_ficha_llega_al_worker_sin_saludo(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    """MasterShield pidió que antes del bloque el agente asienta y presente lo
    que viene. La línea la escribe el modelo, así que pasa los mismos controles
    que su texto: si arranca saludando, se le saca el saludo."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _del_cliente(conexion, id_conv, "qué es la de privacidad?", "fichas-9")
    proveedor = _ProveedorFalso([
        Salida(llamadas=[Llamada("t1", "enviar_ficha", {
            "producto": PRIVACIDAD,
            "introduccion": "Hola, Ana. Le comparto la información de la lámina de privacidad:"})]),
        Salida(texto="¿Para qué espacio sería?"),
    ])
    monkeypatch.setattr(loop, "_cliente", lambda: proveedor)

    r = await loop.responder(id_conv)

    assert r.fichas == [PRIVACIDAD]
    assert r.introduccion_fichas
    assert not r.introduccion_fichas.lower().startswith("hola")
    assert "privacidad" in r.introduccion_fichas




# --- el video de cierre -------------------------------------------------------
# MasterShield pidio que al cerrar la conversacion la persona vea como trabajan
# (15/9/2026). Si el video ya salio con una ficha, no se repite.

async def _cierra(monkeypatch, herramientas, ya_cerrada=False, video_enviado=False):
    async def _valor(sql, *args):
        return 1 if ya_cerrada else None
    monkeypatch.setattr(worker.db, "valor", _valor)
    async def _video_ya_enviado(_):
        return video_enviado
    monkeypatch.setattr(worker.fichas, "video_ya_enviado", _video_ya_enviado)
    return await worker._cierra_la_conversacion(
        1, Respuesta(texto="", herramientas_usadas=herramientas))


async def test_al_cerrar_la_calificacion_sale_el_video(monkeypatch):
    assert await _cierra(monkeypatch, ["finalizar_calificacion"]) is True


async def test_el_video_no_sale_dos_veces(monkeypatch):
    assert await _cierra(monkeypatch, ["finalizar_calificacion"], video_enviado=True) is False


async def test_en_un_turno_cualquiera_no_sale(monkeypatch):
    assert await _cierra(monkeypatch, ["guardar_dato"]) is False


async def test_si_la_conversacion_ya_estaba_cerrada_igual_sale(monkeypatch):
    """La despedida puede llegar un turno despues del cierre."""
    assert await _cierra(monkeypatch, [], ya_cerrada=True) is True


# --- el modelo no repite lo que dice la ficha ---------------------------------

@pytest.mark.db
async def test_no_resume_la_ficha_que_sale_en_este_turno(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    """Pablo, 16/9/2026: el agente presento la ficha, salio la ficha, y despues
    el volvio a explicar lo mismo con sus palabras. La ficha todavia no esta en
    `mensajes` cuando corre el filtro, asi que hay que comparar contra su texto."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _del_cliente(conexion, id_conv, "necesito laminas de seguridad", "fichas-rep")

    de_la_ficha = fichas.texto(SEGURIDAD).splitlines()[2]
    proveedor = _ProveedorFalso([
        Salida(llamadas=[Llamada("t1", "enviar_ficha", {
            "producto": SEGURIDAD,
            "introduccion": "Le comparto la información oficial de la lámina de seguridad:"})]),
        Salida(texto=f"{de_la_ficha}\n\n¿Cuántos m² son? 📏"),
    ])
    monkeypatch.setattr(loop, "_cliente", lambda: proveedor)

    r = await loop.responder(id_conv)

    assert r.fichas == [SEGURIDAD]
    assert de_la_ficha not in r.texto, "lo que ya dice la ficha no se repite"
    assert "¿Cuántos m² son?" in r.texto, "lo que agrega si sale"


def test_se_comparan_todos_los_bloques_que_salen_en_el_turno():
    """El filtro solo miraba `mensajes`, y lo que sale en este turno todavia no
    esta ahi. Cada bloque que el codigo manda tal cual tiene que listarse, o el
    modelo puede repetirlo sin que nadie lo note (Pablo, 16/9/2026)."""
    r = Respuesta(texto="", fichas=[SEGURIDAD],
                  introduccion_fichas="Le comparto la información oficial:",
                  lista_de_precios="▪ Actualmente en el material que necesita tenemos:",
                  introduccion_precios="Estos son los valores de este mes:")
    bloques = loop.bloques_del_turno(r)

    assert fichas.texto(SEGURIDAD) in bloques, "la ficha"
    assert fichas.VIDEO_PRODUCTOS.leyenda in bloques, "la leyenda del video"
    assert "▪ Actualmente en el material que necesita tenemos:" in bloques, "la lista"
    assert "Le comparto la información oficial:" in bloques
    assert "Estos son los valores de este mes:" in bloques


def test_sin_bloques_no_hay_nada_que_comparar():
    assert loop.bloques_del_turno(Respuesta(texto="Buenas tardes")) == []


def test_no_repite_la_leyenda_del_video():
    """Sale pegada al video, escrita por MasterShield: si el modelo la parafrasea
    al lado, la persona lee lo mismo dos veces."""
    leyenda = fichas.VIDEO_PRODUCTOS.leyenda
    texto = f"{leyenda}\n\n¿Cuántos m² son?"
    r = Respuesta(texto=texto, fichas=[SEGURIDAD])

    assert loop.lineas_nuevas(texto, loop.bloques_del_turno(r)) == "¿Cuántos m² son?"


# --- la nota de voz del asesor ------------------------------------------------
# Esteban grabo dos: la de Quito ofrece la visita tecnica, la del resto del pais
# ofrece la llamada. Salen pegadas a la lista de precios (30/9/2026).

@pytest.mark.parametrize("zona, archivo", [
    ("quito_y_valles", "audio-quito-visita.ogg"),
    ("pichincha_cercana", "audio-fuera-llamada.ogg"),
    ("zona_azul", "audio-fuera-llamada.ogg"),
    ("zona_verde", "audio-fuera-llamada.ogg"),
    ("zona_roja", "audio-fuera-llamada.ogg"),
])
def test_cada_zona_tiene_su_nota_de_voz(zona, archivo):
    assert fichas.audio_de_la_zona(zona).ruta.name == archivo


def test_solo_quito_escucha_la_de_la_visita():
    """Prometer la visita gratis en Loja es prometer algo que no se sostiene."""
    quito = fichas.audio_de_la_zona("quito_y_valles")
    afuera = fichas.audio_de_la_zona("zona_roja")
    assert quito.ruta != afuera.ruta
    assert "visita" in quito.descripcion
    assert "llamada" in afuera.descripcion


@pytest.mark.parametrize("zona", [None, "", "galapagos", "fuera_del_pais", "inventada"])
def test_sin_zona_de_venta_no_hay_nota_de_voz(zona):
    assert fichas.audio_de_la_zona(zona) is None


def test_los_dos_audios_estan_en_el_repo():
    """Si falta el archivo, el worker lo saltea y la respuesta sale igual, pero
    el cliente pidio que salgan: mejor que falle un test a que falte en silencio."""
    for zona in ("quito_y_valles", "zona_verde"):
        ruta = fichas.AUDIO_POR_ZONA[zona].ruta
        assert ruta.exists(), f"falta {ruta}"
        assert ruta.suffix == ".ogg", (
            "WhatsApp solo dibuja la burbuja de nota de voz con Ogg/Opus; "
            "en .m4a llega con el icono de auriculares, como un audio reenviado"
        )
        assert ruta.stat().st_size < 16 * 1024 * 1024, "el maximo de WhatsApp"


def _metadatos_de(ruta) -> int:
    """Cuantos campos tiene el header OpusTags del archivo."""
    datos = ruta.read_bytes()[:4096]
    i = datos.index(b"OpusTags")
    largo_vendor = int.from_bytes(datos[i + 8:i + 12], "little")
    inicio = i + 12 + largo_vendor
    return int.from_bytes(datos[inicio:inicio + 4], "little")


@pytest.mark.parametrize("zona", ["quito_y_valles", "zona_verde"])
def test_las_notas_de_voz_no_arrastran_metadatos(zona):
    """Con los tags que vienen del .mp4 original —creation_time, handler_name—
    Meta acepta la subida, acepta el envio, y despues no entrega: error 131053.
    Un audio asi parece mandado y nunca llega, asi que se mira el archivo.

    Se regeneran con `scripts/preparar_audio.py`, que ya saca los metadatos.
    """
    assert _metadatos_de(fichas.AUDIO_POR_ZONA[zona].ruta) <= 1


def test_lo_que_queda_en_el_historial_dice_que_fue_una_nota_de_voz():
    """El modelo lo lee en los turnos siguientes y el asesor en la transcripcion."""
    audio = fichas.audio_de_la_zona("quito_y_valles")
    assert audio.contenido.startswith("[nota de voz de un asesor MS")
