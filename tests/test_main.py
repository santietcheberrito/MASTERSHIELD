"""Arranque del servicio y /health."""

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.config import obtener_settings

URL = "postgresql://usuario:clave@host:5432/base"


@pytest.fixture
def cliente(monkeypatch):
    """TestClient con el pool de base simulado.

    /health se testea sin base: lo que interesa es que traduzca el estado de la
    conexion al codigo HTTP correcto, no que asyncpg funcione.
    """
    monkeypatch.setenv("DATABASE_URL", URL)
    obtener_settings.cache_clear()

    async def _iniciar(*args, **kwargs):
        return None

    async def _cerrar():
        return None

    monkeypatch.setattr(db, "iniciar", _iniciar)
    monkeypatch.setattr(db, "cerrar", _cerrar)

    with TestClient(main.app) as cliente:
        yield cliente

    obtener_settings.cache_clear()


def test_health_con_base_viva(cliente, monkeypatch):
    async def _viva(timeout=3.0):
        return True

    monkeypatch.setattr(db, "esta_viva", _viva)

    respuesta = cliente.get("/health")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "ok", "base": "ok"}


def test_health_con_base_caida(cliente, monkeypatch):
    """503 y no 500: el proceso esta vivo, la base no. Railway tiene que poder
    distinguir las dos cosas."""

    async def _muerta(timeout=3.0):
        return False

    monkeypatch.setattr(db, "esta_viva", _muerta)

    respuesta = cliente.get("/health")
    assert respuesta.status_code == 503
    assert respuesta.json() == {"estado": "degradado", "base": "sin conexion"}


def test_el_servicio_levanta_aunque_la_base_no_responda(monkeypatch):
    """Si el pool no se puede crear, el arranque no se aborta: se loguea y
    /health lo reporta."""
    monkeypatch.setenv("DATABASE_URL", URL)
    obtener_settings.cache_clear()

    async def _explota(*args, **kwargs):
        raise OSError("no hay ruta al host")

    async def _cerrar():
        return None

    monkeypatch.setattr(db, "iniciar", _explota)
    monkeypatch.setattr(db, "cerrar", _cerrar)

    with TestClient(main.app) as cliente:
        assert cliente.get("/health").status_code == 503

    obtener_settings.cache_clear()


def test_avisar_credenciales_lista_las_que_faltan(caplog):
    from app.config import Settings

    settings = Settings(_env_file=None, database_url=URL, anthropic_api_key="sk-test")
    with caplog.at_level("WARNING"):
        main.avisar_credenciales(settings)

    assert "WHATSAPP_TOKEN" in caplog.text
    assert "ANTHROPIC_API_KEY" not in caplog.text
