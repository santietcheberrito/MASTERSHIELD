"""Adaptador de la Cloud API de WhatsApp. Sin red."""

import hashlib
import hmac
import json

import pytest
import respx

from app.canales import whatsapp

SECRETO = "app-secret-de-prueba"


def payload(**cambios):
    mensaje = {
        "id": "wamid.HBgLNTkzOTg3MTEyMjMz",
        "from": "593987112233",
        "timestamp": "1788359877",
        "type": "text",
        "text": {"body": "buenas, necesito lamina"},
    }
    mensaje.update(cambios)
    return {"object": "whatsapp_business_account", "entry": [{
        "id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "contacts": [{"profile": {"name": "María Páez"}, "wa_id": "593987112233"}],
            "messages": [mensaje],
        }}],
    }]}


def firmar(cuerpo: bytes, secreto: str = SECRETO) -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


# --- firma ------------------------------------------------------------------

def test_una_firma_correcta_pasa():
    cuerpo = json.dumps(payload()).encode()
    assert whatsapp.firma_valida(SECRETO, cuerpo, firmar(cuerpo)) is True


def test_un_cuerpo_alterado_no_pasa():
    """Es lo único que distingue un mensaje de Meta de uno de cualquiera que
    haya descubierto la URL."""
    cuerpo = json.dumps(payload()).encode()
    firma = firmar(cuerpo)
    assert whatsapp.firma_valida(SECRETO, cuerpo + b" ", firma) is False


@pytest.mark.parametrize("cabecera", [None, "", "sha256=", "abc", "sha1=deadbeef"])
def test_firmas_mal_formadas(cabecera):
    assert whatsapp.firma_valida(SECRETO, b"{}", cabecera) is False


def test_sin_app_secret_no_se_valida():
    """Aceptable mientras se prueba; el arranque avisa si falta."""
    assert whatsapp.firma_valida("", b"{}", None) is True


# --- alta del webhook -------------------------------------------------------

def test_el_desafio_se_devuelve_si_el_token_coincide():
    parametros = {"hub.mode": "subscribe", "hub.verify_token": "mi-token",
                  "hub.challenge": "1234567890"}
    assert whatsapp.verificacion(parametros, "mi-token") == "1234567890"


@pytest.mark.parametrize("parametros", [
    {"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "1"},
    {"hub.mode": "unsubscribe", "hub.verify_token": "mi-token", "hub.challenge": "1"},
    {},
])
def test_el_desafio_se_rechaza(parametros):
    assert whatsapp.verificacion(parametros, "mi-token") is None


# --- parseo -----------------------------------------------------------------

def test_mensaje_de_texto():
    m = whatsapp.parsear(payload())
    assert m.canal == "whatsapp"
    assert m.texto == "buenas, necesito lamina"
    assert m.nombre == "María Páez"


def test_el_telefono_viene_en_el_payload():
    """A diferencia de Telegram, acá el handoff telefónico no depende de que la
    persona comparta su número."""
    m = whatsapp.parsear(payload())
    assert m.telefono == "+593987112233"
    assert m.identificador == "+593987112233"


def test_el_id_va_calificado_por_canal():
    assert whatsapp.parsear(payload()).id_externo == "whatsapp:wamid.HBgLNTkzOTg3MTEyMjMz"


def test_imagen_con_epigrafe():
    m = whatsapp.parsear(payload(
        type="image", text=None,
        image={"id": "123", "mime_type": "image/jpeg", "caption": "mide 1.20 x 2.40"},
    ))
    assert m.tipo == "imagen"
    assert m.texto == "mide 1.20 x 2.40"


def test_audio_sin_texto():
    m = whatsapp.parsear(payload(type="audio", audio={"id": "123"}))
    assert m.tipo == "audio"
    assert m.texto == ""


def test_respuesta_de_boton():
    m = whatsapp.parsear(payload(type="interactive", interactive={
        "type": "button_reply", "button_reply": {"id": "1", "title": "Sí, me interesa"}}))
    assert m.texto == "Sí, me interesa"


def test_los_estados_de_entrega_no_son_mensajes():
    """Meta manda por el mismo webhook los avisos de entregado y leído de lo
    que mandamos nosotros. Eso no es una consulta de nadie."""
    estados = {"object": "whatsapp_business_account", "entry": [{
        "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "statuses": [{"id": "wamid.X", "status": "delivered"}],
        }}]}]}
    assert whatsapp.parsear(estados) is None


@pytest.mark.parametrize("cuerpo", [{}, {"entry": []}, {"entry": [{"changes": []}]}, None])
def test_payloads_incompletos(cuerpo):
    assert whatsapp.parsear(cuerpo or {}) is None


# --- errores ----------------------------------------------------------------

@respx.mock
async def test_el_error_de_meta_llega_entero(respx_mock):
    """Meta explica en el cuerpo qué está mal y con un código propio.
    `raise_for_status` tiraba todo eso y dejaba un "400 Bad Request" que obliga
    a reproducir la llamada a mano para entender. Ya pasó una vez."""
    import httpx as _httpx

    respx_mock.post("https://graph.facebook.com/v23.0/123/messages").mock(
        return_value=_httpx.Response(400, json={"error": {
            "code": 131030,
            "message": "(#131030) Recipient phone number not in allowed list",
            "error_data": {"details": "Agregá el número a la lista de destinatarios."},
        }})
    )

    with pytest.raises(whatsapp.WhatsAppError) as e:
        await whatsapp.enviar("token", "123", "+5491160074604", "hola")

    assert "131030" in str(e.value)
    assert "allowed list" in str(e.value)
    assert "lista de destinatarios" in str(e.value)


# --- la rareza argentina del 9 ----------------------------------------------

@pytest.mark.parametrize("entrante,saliente", [
    # WhatsApp identifica con el 9, pero la API solo entrega sin él.
    ("+5491160074604", "541160074604"),
    ("5491160074604", "541160074604"),
    # Ecuador, que es donde opera el cliente: no se toca.
    ("+593987112233", "593987112233"),
    ("+593221234567", "593221234567"),
    # Un fijo argentino tampoco: la corrección es solo para el móvil de 13.
    ("+541143214321", "541143214321"),
])
def test_el_destino_de_envio(entrante, saliente):
    assert whatsapp.destino_de_envio(entrante) == saliente


# --- ecos de mensajes salientes ---------------------------------------------

def _eco(id_mensaje="wamid.eco1", texto="lo llamo yo en un rato", destino="593999123456"):
    return {"entry": [{"changes": [{"field": "message_echoes", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"phone_number_id": "1234"},
        "message_echoes": [{
            "from": "15551956977",
            "to": destino,
            "id": id_mensaje,
            "timestamp": "1757000000",
            "type": "text",
            "text": {"body": texto},
        }],
    }}]}]}


def test_un_eco_se_parsea_con_el_cliente_como_interlocutor():
    """El eco va hacia afuera, así que quien está del otro lado es `to`. Tomar
    `from` daría nuestro propio número como identificador de la conversación."""
    eco = whatsapp.parsear_eco(_eco())

    assert eco is not None
    assert eco.identificador == "+593999123456"
    assert eco.texto == "lo llamo yo en un rato"
    assert eco.id_externo == "whatsapp:wamid.eco1"


def test_un_mensaje_entrante_no_es_un_eco():
    assert whatsapp.parsear_eco(payload()) is None


def test_un_eco_no_es_un_mensaje_entrante():
    """Si `parsear` lo tomara, el mensaje del asesor entraría como si lo hubiera
    escrito el cliente y el agente le contestaría a su propio equipo."""
    assert whatsapp.parsear(_eco()) is None


def test_un_eco_sin_destinatario_se_descarta():
    payload = _eco()
    del payload["entry"][0]["changes"][0]["value"]["message_echoes"][0]["to"]
    assert whatsapp.parsear_eco(payload) is None


@pytest.mark.parametrize("tipo,esperado", [
    ("revoke", "[el asesor borro un mensaje]"),
    ("edit", "[el asesor edito un mensaje]"),
])
def test_borrar_o_editar_tambien_es_actividad_humana(tipo, esperado):
    """Los tres dicen lo mismo: hay una persona operando esta conversación. Sin
    texto propio quedarían como un mensaje vacío en el historial."""
    payload = _eco()
    eco_crudo = payload["entry"][0]["changes"][0]["value"]["message_echoes"][0]
    eco_crudo["type"] = tipo
    del eco_crudo["text"]

    eco = whatsapp.parsear_eco(payload)

    assert eco is not None
    assert eco.texto == esperado


# --- archivos que manda el cliente ------------------------------------------

def _con_imagen(caption=None, media_id="media-123", mime="image/jpeg"):
    imagen = {"id": media_id, "mime_type": mime, "sha256": "abc"}
    if caption is not None:
        imagen["caption"] = caption
    return {"entry": [{"changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"phone_number_id": "1234"},
        "contacts": [{"wa_id": "593987112233", "profile": {"name": "Santi"}}],
        "messages": [{"from": "593987112233", "id": "wamid.img", "type": "image",
                      "timestamp": "1757000000", "image": imagen}],
    }}]}]}


def test_encuentra_el_archivo_de_una_imagen():
    """El id vive dentro del objeto `image`, no en la raíz del mensaje."""
    medias = whatsapp.medias_del_payload(_con_imagen())

    assert len(medias) == 1
    assert medias[0]["id"] == "media-123"
    assert medias[0]["mime"] == "image/jpeg"


def test_un_mensaje_de_texto_no_trae_archivos():
    assert whatsapp.medias_del_payload(payload()) == []


def test_un_payload_roto_no_explota():
    """Llega por un webhook público: nunca se confía en su forma."""
    assert whatsapp.medias_del_payload({}) == []
    assert whatsapp.medias_del_payload({"entry": []}) == []


def test_el_audio_no_cuenta_como_archivo():
    """Nadie va a escuchar un audio para tomar medidas, y subirlo sólo llena el
    drive del cliente."""
    p = _con_imagen()
    mensaje = p["entry"][0]["changes"][0]["value"]["messages"][0]
    mensaje["type"] = "audio"
    mensaje["audio"] = mensaje.pop("image")

    assert whatsapp.medias_del_payload(p) == []


def test_el_epigrafe_sigue_siendo_el_texto_del_mensaje():
    """La foto va al CRM y lo que la persona escribió junto con ella, al
    historial: son dos caminos distintos y los dos importan."""
    mensaje = whatsapp.parsear(_con_imagen(caption="estas son las ventanas"))

    assert mensaje.tipo == "imagen"
    assert mensaje.texto == "estas son las ventanas"


# --- lo que no es un mensaje --------------------------------------------------

def test_una_reaccion_no_genera_turno():
    """Pablo, 23/9/2026: puso un 👍 sobre un mensaje despues de la despedida.
    Llegaba como tipo "otro" con contenido vacio y el agente contesto
    "recibimos el archivo", fuera de contexto y con la conversacion cerrada."""
    p = payload(type="reaction", reaction={"emoji": "👍", "message_id": "wamid.ABC"})
    del p["entry"][0]["changes"][0]["value"]["messages"][0]["text"]

    assert whatsapp.parsear(p) is None


def test_un_sticker_solo_tampoco():
    """Una expresion, no una consulta."""
    p = payload(type="sticker", sticker={"id": "stk-1", "mime_type": "image/webp"})
    del p["entry"][0]["changes"][0]["value"]["messages"][0]["text"]

    assert whatsapp.parsear(p) is None


def test_un_mensaje_normal_sigue_pasando():
    """La red no puede tragarse lo que si hay que contestar."""
    mensaje = whatsapp.parsear(payload())

    assert mensaje is not None
    assert mensaje.texto == "buenas, necesito lamina"


def test_una_foto_sigue_pasando():
    p = payload(type="image", image={"id": "img-1", "mime_type": "image/jpeg"})
    del p["entry"][0]["changes"][0]["value"]["messages"][0]["text"]

    mensaje = whatsapp.parsear(p)

    assert mensaje is not None and mensaje.tipo == "imagen"


# --- los fallos de entrega ----------------------------------------------------
# Meta responde 200 al enviar y avisa del fallo despues, por webhook. El payload
# de abajo es el real que devolvio la cuenta el 30/9/2026 con los audios en ogg.

def estado(**cambios):
    est = {
        "id": "wamid.HBgNNTQ5MTE2MDA3NDYwNA",
        "status": "failed",
        "timestamp": "1790781894",
        "recipient_id": "5491160074604",
        "errors": [{
            "code": 131053,
            "title": "Media upload error",
            "error_data": {"details": (
                "Audio file uploaded with mimetype as audio/ogg; codecs=opus, "
                "however on processing it is of type application/octet-stream."
            )},
        }],
    }
    est.update(cambios)
    return {"object": "whatsapp_business_account", "entry": [{
        "id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": "PNID"},
            "statuses": [est],
        }}]}]}


def test_un_fallo_de_entrega_se_puede_contar():
    """Lo que Meta acepto y despues tiro tiene que quedar en el log."""
    fallidos = whatsapp.entregas_fallidas(estado())

    assert len(fallidos) == 1
    wamid, motivo = fallidos[0]
    assert wamid == "wamid.HBgNNTQ5MTE2MDA3NDYwNA"
    assert "131053" in motivo
    assert "octet-stream" in motivo


@pytest.mark.parametrize("cual", ["sent", "delivered", "read"])
def test_los_estados_normales_no_son_fallos(cual):
    """Casi todos los estados son buenas noticias y no van al log de errores."""
    assert whatsapp.entregas_fallidas(estado(status=cual, errors=None)) == []


def test_un_mensaje_entrante_no_trae_fallos():
    assert whatsapp.entregas_fallidas(payload()) == []


def test_un_payload_raro_no_explota():
    """El webhook contesta 200 igual: si esto tira, Meta reintenta al infinito."""
    for raro in ({}, {"entry": []}, {"entry": [{"changes": []}]}, {"entry": "no"}):
        assert whatsapp.entregas_fallidas(raro) == []


# --- el tipo con el que se sube cada archivo -----------------------------------

@pytest.mark.parametrize("extension, esperado", [
    (".ogg", "audio/ogg; codecs=opus"),
    (".m4a", "audio/mp4"),
    (".mp3", "audio/mpeg"),
    (".mp4", "video/mp4"),
])
def test_cada_extension_se_sube_con_el_tipo_que_meta_acepta(extension, esperado):
    """`mimetypes` dice "audio/ogg" a secas y "audio/mp4a-latm" para .m4a, y
    Meta rechaza los dos."""
    assert whatsapp.MIME_POR_EXTENSION[extension] == esperado


def test_el_ogg_declara_el_codec():
    """Sin el codec en el mime, Meta no lo toma como nota de voz."""
    assert "opus" in whatsapp.MIME_POR_EXTENSION[".ogg"]


# --- el envio de la nota de voz -----------------------------------------------

@respx.mock
async def test_la_nota_de_voz_sale_marcada_como_nota_de_voz(respx_mock, tmp_path):
    """Sin `voice`, el mismo Ogg/Opus llega con el icono de auriculares, como un
    audio reenviado. El cliente lo rechazo por eso el 30/9/2026, y desde el
    cuerpo del mensaje no se nota: hay que mirar que el flag viaje."""
    import httpx as _httpx

    ruta = tmp_path / "nota.ogg"
    ruta.write_bytes(b"OggS-falso")
    subida = respx_mock.post("https://graph.facebook.com/v23.0/123/media").mock(
        return_value=_httpx.Response(200, json={"id": "media-1"}))
    envio = respx_mock.post("https://graph.facebook.com/v23.0/123/messages").mock(
        return_value=_httpx.Response(200, json={"messages": [{"id": "wamid.X"}]}))

    await whatsapp.enviar_audio("token", "123", "+5491160074604", ruta)

    assert subida.called and envio.called
    cuerpo = json.loads(envio.calls[0].request.content)
    assert cuerpo["type"] == "audio"
    assert cuerpo["audio"]["voice"] is True, "sin esto llega como archivo adjunto"
    assert cuerpo["audio"]["id"] == "media-1"


@respx.mock
async def test_la_nota_de_voz_se_sube_declarando_opus(respx_mock, tmp_path):
    """Meta pide el codec en el mime; `mimetypes` dice "audio/ogg" a secas."""
    import httpx as _httpx

    ruta = tmp_path / "nota.ogg"
    ruta.write_bytes(b"OggS-falso")
    subida = respx_mock.post("https://graph.facebook.com/v23.0/123/media").mock(
        return_value=_httpx.Response(200, json={"id": "media-1"}))
    respx_mock.post("https://graph.facebook.com/v23.0/123/messages").mock(
        return_value=_httpx.Response(200, json={"messages": [{"id": "wamid.X"}]}))

    await whatsapp.enviar_audio("token", "123", "+5491160074604", ruta)

    enviado = subida.calls[0].request.content.decode("latin-1")
    assert "audio/ogg; codecs=opus" in enviado
