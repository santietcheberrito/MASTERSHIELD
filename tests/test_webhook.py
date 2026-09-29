"""Endpoint de recepcion. Sin base: lo que importa es que persista y agende, y
que nunca haga trabajo pesado dentro del request."""

import pytest
from fastapi.testclient import TestClient

from app import db, ingesta, main
from app.config import obtener_settings

URL = "postgresql://usuario:clave@host:5432/base"
RUTA = "/webhook/telegram"

UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 42,
        "from": {"id": 7, "is_bot": False, "first_name": "Maria"},
        "chat": {"id": 7, "type": "private"},
        "text": "buenas, necesito lamina para una oficina",
    },
}


class _WorkerFalso:
    def arrancar(self):
        pass

    async def detener(self):
        pass


@pytest.fixture
def registrados(monkeypatch):
    """Reemplaza la ingesta: acá se testea el endpoint, no la persistencia."""
    capturados = []

    async def _registrar(mensaje, demora_seg):
        capturados.append((mensaje, demora_seg))
        return True

    monkeypatch.setattr(ingesta, "registrar", _registrar)
    return capturados


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("TELEGRAM_MODO", "off")
    obtener_settings.cache_clear()

    async def _nada(*args, **kwargs):
        return None

    monkeypatch.setattr(db, "iniciar", _nada)
    monkeypatch.setattr(db, "cerrar", _nada)
    monkeypatch.setattr(main, "Worker", _WorkerFalso)

    with TestClient(main.app) as c:
        yield c

    obtener_settings.cache_clear()


def test_update_valido_se_registra(cliente, registrados):
    assert cliente.post(RUTA, json=UPDATE).status_code == 200

    assert len(registrados) == 1
    mensaje, demora = registrados[0]
    assert mensaje.identificador == "7"
    assert mensaje.id_externo == "telegram:7:42"
    s = obtener_settings()
    assert s.demora_respuesta_min_seg <= demora <= s.demora_respuesta_max_seg, (
        "la demora se sortea en el rango configurado")


def test_update_que_no_es_mensaje_se_acepta_sin_registrar(cliente, registrados):
    """Ediciones y callbacks: recibido, nada que hacer. Un no-200 haria que
    Telegram lo repita para siempre."""
    respuesta = cliente.post(RUTA, json={"update_id": 2, "edited_message": {}})
    assert respuesta.status_code == 200
    assert registrados == []


def test_cuerpo_ilegible_se_acepta(cliente, registrados):
    respuesta = cliente.post(RUTA, content=b"esto no es json")
    assert respuesta.status_code == 200
    assert registrados == []


def test_falla_de_base_devuelve_503(cliente, monkeypatch):
    """Y no 200: el mensaje todavia no existe en ningun lado, y el reintento del
    canal es lo unico que impide perderlo. El indice unico sobre id_externo
    garantiza que ese reintento no genere una respuesta duplicada."""

    async def _explota(mensaje, demora_seg):
        raise ConnectionError("la base no responde")

    monkeypatch.setattr(ingesta, "registrar", _explota)
    assert cliente.post(RUTA, json=UPDATE).status_code == 503


# --- validacion del secreto -------------------------------------------------

@pytest.fixture
def cliente_con_secreto(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("TELEGRAM_MODO", "off")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3creto")
    obtener_settings.cache_clear()

    async def _nada(*args, **kwargs):
        return None

    monkeypatch.setattr(db, "iniciar", _nada)
    monkeypatch.setattr(db, "cerrar", _nada)
    monkeypatch.setattr(main, "Worker", _WorkerFalso)

    with TestClient(main.app) as c:
        yield c

    obtener_settings.cache_clear()


def test_con_el_secreto_correcto_pasa(cliente_con_secreto, registrados):
    respuesta = cliente_con_secreto.post(
        RUTA, json=UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": "s3creto"}
    )
    assert respuesta.status_code == 200
    assert len(registrados) == 1


def test_sin_secreto_se_rechaza(cliente_con_secreto, registrados):
    assert cliente_con_secreto.post(RUTA, json=UPDATE).status_code == 403
    assert registrados == []


def test_con_secreto_incorrecto_se_rechaza(cliente_con_secreto, registrados):
    respuesta = cliente_con_secreto.post(
        RUTA, json=UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": "otro"}
    )
    assert respuesta.status_code == 403
    assert registrados == []


# --- WhatsApp ---------------------------------------------------------------

RUTA_WA = "/webhook/whatsapp"

PAYLOAD_WA = {
    "object": "whatsapp_business_account",
    "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "contacts": [{"profile": {"name": "María Páez"}, "wa_id": "593987112233"}],
        "messages": [{"id": "wamid.ABC", "from": "593987112233", "type": "text",
                      "text": {"body": "buenas, necesito lamina"}}],
    }}]}],
}


@pytest.fixture
def cliente_wa(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("TELEGRAM_MODO", "off")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "mi-verify-token")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "secreto")
    obtener_settings.cache_clear()

    async def _nada(*args, **kwargs):
        return None

    monkeypatch.setattr(db, "iniciar", _nada)
    monkeypatch.setattr(db, "cerrar", _nada)
    monkeypatch.setattr(main, "Worker", _WorkerFalso)

    with TestClient(main.app) as c:
        yield c

    obtener_settings.cache_clear()


def _firmar(cuerpo: bytes, secreto: str = "secreto") -> dict:
    import hashlib
    import hmac
    return {"X-Hub-Signature-256":
            "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()}


def test_meta_verifica_la_url_con_un_desafio(cliente_wa):
    """Es el alta del webhook: Meta manda un GET y espera el challenge de vuelta."""
    respuesta = cliente_wa.get(RUTA_WA, params={
        "hub.mode": "subscribe", "hub.verify_token": "mi-verify-token",
        "hub.challenge": "1234567890"})
    assert respuesta.status_code == 200
    assert respuesta.text == "1234567890"


def test_un_verify_token_que_no_coincide_se_rechaza(cliente_wa):
    respuesta = cliente_wa.get(RUTA_WA, params={
        "hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "1"})
    assert respuesta.status_code == 403


def test_un_mensaje_firmado_se_registra(cliente_wa, registrados):
    import json as _json
    cuerpo = _json.dumps(PAYLOAD_WA).encode()
    respuesta = cliente_wa.post(RUTA_WA, content=cuerpo, headers=_firmar(cuerpo))

    assert respuesta.status_code == 200
    assert len(registrados) == 1
    mensaje, _demora = registrados[0]
    assert mensaje.identificador == "+593987112233"
    assert mensaje.telefono == "+593987112233"


def test_sin_firma_valida_se_rechaza(cliente_wa, registrados):
    """La firma es lo único que distingue a Meta de cualquiera que descubra la
    URL del túnel."""
    import json as _json
    cuerpo = _json.dumps(PAYLOAD_WA).encode()
    respuesta = cliente_wa.post(RUTA_WA, content=cuerpo,
                                headers={"X-Hub-Signature-256": "sha256=falsa"})
    assert respuesta.status_code == 403
    assert registrados == []


def test_los_estados_de_entrega_no_generan_conversacion(cliente_wa, registrados):
    import json as _json
    estados = {"object": "whatsapp_business_account", "entry": [{"changes": [
        {"field": "messages", "value": {"messaging_product": "whatsapp",
         "statuses": [{"id": "wamid.X", "status": "read"}]}}]}]}
    cuerpo = _json.dumps(estados).encode()
    respuesta = cliente_wa.post(RUTA_WA, content=cuerpo, headers=_firmar(cuerpo))
    assert respuesta.status_code == 200
    assert registrados == []


# --- ecos: un asesor escribió desde la app de WhatsApp Business --------------

ECO_WA = {"object": "whatsapp_business_account", "entry": [{"changes": [
    {"field": "smb_message_echoes", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"phone_number_id": "1234"},
        "message_echoes": [{
            "from": "15551956977", "to": "593987112233", "id": "wamid.eco",
            "timestamp": "1757000000", "type": "text",
            "text": {"body": "buenas, soy Andrés, lo llamo en un rato"},
        }],
    }}]}]}


@pytest.fixture
def intervenciones(monkeypatch):
    """Lo que importa acá es que el endpoint distinga un eco de un mensaje."""
    capturadas = []

    async def _registrar(canal, identificador, texto, id_externo):
        capturadas.append((canal, identificador, texto, id_externo))
        return 1

    async def _valor(*args, **kwargs):
        return None  # ningún mensaje nuestro con ese id: lo escribió una persona

    monkeypatch.setattr(ingesta, "registrar_intervencion_humana", _registrar)
    monkeypatch.setattr(db, "valor", _valor)
    return capturadas


def test_un_eco_ajeno_pausa_la_conversacion(cliente_wa, registrados, intervenciones):
    """Un asesor contestó desde su teléfono. El agente tiene que callarse, y el
    mensaje no puede entrar como si lo hubiera escrito el cliente."""
    import json as _json
    cuerpo = _json.dumps(ECO_WA).encode()

    respuesta = cliente_wa.post(RUTA_WA, content=cuerpo, headers=_firmar(cuerpo))

    assert respuesta.status_code == 200
    assert len(intervenciones) == 1
    canal, identificador, texto, id_externo = intervenciones[0]
    assert identificador == "+593987112233", "el interlocutor es el cliente, no nuestro número"
    assert "soy Andrés" in texto
    assert id_externo == "whatsapp:wamid.eco"
    assert registrados == [], "un eco no es una consulta entrante"


def test_el_eco_de_un_mensaje_nuestro_se_ignora(cliente_wa, registrados, monkeypatch):
    """Meta hace eco también de lo que mandamos nosotros. Si eso pausara, el
    agente se callaría solo después de cada respuesta."""
    import json as _json
    capturadas = []

    async def _registrar(*args):
        capturadas.append(args)
        return 1

    async def _valor(*args, **kwargs):
        return 1  # ya está en `mensajes`: lo enviamos nosotros

    monkeypatch.setattr(ingesta, "registrar_intervencion_humana", _registrar)
    monkeypatch.setattr(db, "valor", _valor)

    cuerpo = _json.dumps(ECO_WA).encode()
    respuesta = cliente_wa.post(RUTA_WA, content=cuerpo, headers=_firmar(cuerpo))

    assert respuesta.status_code == 200
    assert capturadas == []
