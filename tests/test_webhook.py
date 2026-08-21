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

    async def _registrar(mensaje, ventana_seg):
        capturados.append((mensaje, ventana_seg))
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
    mensaje, ventana = registrados[0]
    assert mensaje.identificador == "7"
    assert mensaje.id_externo == "telegram:7:42"
    assert ventana == 6, "el default de VENTANA_BUFFER_SEG"


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

    async def _explota(mensaje, ventana_seg):
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
