"""Las notas de voz de WhatsApp, pasadas a texto.

Desde el 15/9/2026. Antes el agente veia "[el cliente envio un archivo de tipo
audio]" y contestaba pidiendo que se lo escribieran, que es justo lo que la
persona no quiso hacer.
"""

import pytest

from app import audio, worker
from app.canales import whatsapp
from app.config import obtener_settings

API = "https://graph.facebook.com/v23.0"
MEDIA = "media-de-la-nota"


def _payload_con_audio(tipo="audio"):
    return {"entry": [{"changes": [{"value": {"messages": [{
        "id": "wamid.NOTA", "from": "593999772230", "type": tipo,
        tipo: {"id": MEDIA, "mime_type": "audio/ogg; codecs=opus", "voice": True},
    }]}}]}]}


class _Transcripcion:
    def __init__(self, texto):
        self.text = texto


class _OpenAIFalso:
    """Devuelve la transcripcion preparada, sin llamar a OpenAI."""

    def __init__(self, texto="", explota=False):
        self.audio = self
        self.transcriptions = self
        self._texto = texto
        self._explota = explota
        self.recibido = {}

    async def create(self, model, file, language):
        if self._explota:
            raise RuntimeError("OpenAI no contesto")
        self.recibido = {"model": model, "nombre": file[0], "bytes": file[1],
                         "mime": file[2], "idioma": language}
        return _Transcripcion(self._texto)


@pytest.fixture
def con_token(monkeypatch, settings_de_prueba):
    """Sin token no se baja nada: `texto_de_la_nota` se va sin llamar a Meta."""
    monkeypatch.setenv("WHATSAPP_TOKEN", "token-de-prueba")
    obtener_settings.cache_clear()
    yield
    obtener_settings.cache_clear()


@pytest.fixture
def con_openai(monkeypatch):
    def _armar(texto="", explota=False):
        falso = _OpenAIFalso(texto, explota)
        monkeypatch.setattr(audio, "_openai", lambda: falso)
        return falso
    return _armar


def _con_la_descarga(respx_mock, contenido=b"OggS-audio", mime="audio/ogg"):
    respx_mock.get(f"{API}/{MEDIA}").respond(
        json={"url": "https://lookaside.fbsbx.com/nota", "mime_type": mime})
    return respx_mock.get("https://lookaside.fbsbx.com/nota").respond(content=contenido)


# --- que audios se bajan ------------------------------------------------------

def test_el_audio_se_reconoce_como_archivo_que_se_baja():
    medias = whatsapp.medias_del_payload(_payload_con_audio(), tipos=("audio",))
    assert [m["id"] for m in medias] == [MEDIA]
    assert medias[0]["mime"].startswith("audio/ogg")


def test_el_audio_no_se_sube_al_lead():
    """Una nota de voz no le sirve a nadie para tomar medidas: se transcribe,
    pero el archivo no va al CRM."""
    assert whatsapp.medias_del_payload(_payload_con_audio()) == []


# --- la transcripcion ---------------------------------------------------------

async def test_la_nota_de_voz_se_baja_y_se_transcribe(
    respx_mock, con_openai, con_token
):
    descarga = _con_la_descarga(respx_mock)
    falso = con_openai("Hola, necesito lámina para el calor, son unas seis ventanas.")

    texto = await audio.texto_de_la_nota(_payload_con_audio())

    assert texto == "Hola, necesito lámina para el calor, son unas seis ventanas."
    assert descarga.called
    assert falso.recibido["bytes"] == b"OggS-audio"
    assert falso.recibido["idioma"] == "es"
    assert falso.recibido["nombre"].endswith(".ogg"), "la extension le dice el formato"


async def test_si_la_transcripcion_falla_el_turno_sigue(
    respx_mock, con_openai, con_token
):
    """Una nota que no se pudo leer no puede dejar a la persona sin respuesta."""
    _con_la_descarga(respx_mock)
    con_openai(explota=True)

    assert await audio.texto_de_la_nota(_payload_con_audio()) == ""


async def test_si_meta_no_da_el_archivo_no_rompe(respx_mock, con_token):
    respx_mock.get(f"{API}/{MEDIA}").respond(status_code=404, json={"error": {}})

    assert await audio.texto_de_la_nota(_payload_con_audio()) == ""


async def test_un_mensaje_sin_audio_no_llama_a_nadie(con_token):
    assert await audio.texto_de_la_nota({"entry": []}) == ""


async def test_un_audio_enorme_no_se_transcribe(con_openai, settings_de_prueba):
    """WhatsApp no deja mandar mas de 16 MB: eso no es una nota de voz nuestra."""
    falso = con_openai("no tendria que llegar")

    assert await audio.transcribir(b"x" * (audio.MAXIMO_BYTES + 1), "audio/ogg") == ""
    assert falso.recibido == {}


# --- el turno -----------------------------------------------------------------

@pytest.mark.db
async def test_la_transcripcion_queda_guardada_en_el_mensaje(
    pool_en_transaccion, respx_mock, con_openai, con_token
):
    """Guardada, el historial de los turnos siguientes la lleva sola y el audio
    no se baja dos veces."""
    conexion = pool_en_transaccion
    _con_la_descarga(respx_mock)
    con_openai("Son unos veinte metros")
    id_conv = await conexion.fetchval(
        "INSERT INTO conversaciones (canal, identificador) "
        # Un identificador de prueba, no un numero real: con el de Pablo, el
        # test choca con su conversacion de verdad y falla sin motivo.
        "VALUES ('whatsapp', 'audio-test') RETURNING id")
    id_msg = await conexion.fetchval(
        "INSERT INTO mensajes (conversacion_id, rol, tipo, contenido, id_externo, payload) "
        "VALUES ($1, 'cliente', 'audio', '', 'wamid.NOTA', $2) RETURNING id",
        id_conv, _payload_con_audio())

    filas = await conexion.fetch(
        "SELECT id, tipo, contenido, id_externo, payload FROM mensajes WHERE id = $1", id_msg)
    mensajes = await worker.transcribir_audios(id_conv, filas)

    assert mensajes[0]["contenido"] == "Son unos veinte metros"
    assert await conexion.fetchval(
        "SELECT contenido FROM mensajes WHERE id = $1", id_msg) == "Son unos veinte metros"


def test_la_nota_transcrita_entra_al_turno_como_lo_que_dijo():
    turno = worker.armar_turno(1, [
        {"id": 1, "tipo": "audio", "contenido": "Son unas seis ventanas",
         "id_externo": "wamid.NOTA"},
    ])
    assert turno.texto == "[nota de voz] Son unas seis ventanas"


def test_una_nota_que_no_se_pudo_transcribir_igual_avisa():
    turno = worker.armar_turno(1, [
        {"id": 1, "tipo": "audio", "contenido": "", "id_externo": "wamid.NOTA"},
    ])
    assert turno.texto == "[audio]"
