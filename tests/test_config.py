from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.config import Settings, obtener_settings

URL = "postgresql://usuario:clave@host:5432/base"


def armar(**extra) -> Settings:
    """Settings sin leer .env, para que el test no dependa del disco."""
    return Settings(_env_file=None, database_url=URL, **extra)


def test_valores_por_defecto():
    s = armar()
    assert s.ventana_buffer_seg == 6
    assert s.horario_atencion == "09:00-18:00"
    assert s.tz == "America/Argentina/Buenos_Aires"
    assert s.log_level == "INFO"


def test_lee_del_entorno(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("VENTANA_BUFFER_SEG", "10")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    s = Settings(_env_file=None)
    assert s.ventana_buffer_seg == 10
    assert s.log_level == "DEBUG"


def test_database_url_es_obligatoria():
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("valor", ["mysql://a:b@host/base", "host:5432/base", ""])
def test_database_url_invalida(valor):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=valor)


@pytest.mark.parametrize("valor", [0, -1, 121])
def test_ventana_buffer_fuera_de_rango(valor):
    with pytest.raises(ValidationError):
        armar(ventana_buffer_seg=valor)


@pytest.mark.parametrize(
    "valor",
    ["9:00-18:00", "09:00 - 18:00", "09:00", "25:00-26:00", "09:60-18:00", ""],
)
def test_horario_mal_formado(valor):
    with pytest.raises(ValidationError):
        armar(horario_atencion=valor)


def test_horario_invertido():
    with pytest.raises(ValidationError):
        armar(horario_atencion="18:00-09:00")


def test_horario_parseado():
    assert armar(horario_atencion="08:30-17:45").horario == (
        time(8, 30),
        time(17, 45),
    )


def test_log_level_invalido():
    with pytest.raises(ValidationError):
        armar(log_level="VERBOSE")


def test_tz_invalida():
    with pytest.raises(ValidationError):
        armar(tz="America/Buenos_Aires_Inventada")


def test_zona_es_la_de_buenos_aires():
    assert armar().zona == ZoneInfo("America/Argentina/Buenos_Aires")


def test_credenciales_faltantes():
    s = armar(anthropic_api_key="sk-test", kommo_subdomain="cliente")
    assert "ANTHROPIC_API_KEY" not in s.credenciales_faltantes
    assert "KOMMO_SUBDOMAIN" not in s.credenciales_faltantes
    assert "BSP_TOKEN" in s.credenciales_faltantes
    assert "KOMMO_ACCESS_TOKEN" in s.credenciales_faltantes


def test_sin_credenciales_faltan_todas():
    assert len(armar().credenciales_faltantes) == 6


@pytest.mark.parametrize(
    "momento,esperado",
    [
        # Miercoles 13/08/2025
        (datetime(2025, 8, 13, 9, 0), True),
        (datetime(2025, 8, 13, 13, 30), True),
        (datetime(2025, 8, 13, 17, 59), True),
        (datetime(2025, 8, 13, 18, 0), False),  # el limite superior queda afuera
        (datetime(2025, 8, 13, 8, 59), False),
        (datetime(2025, 8, 13, 3, 0), False),
        # Sabado y domingo
        (datetime(2025, 8, 16, 11, 0), False),
        (datetime(2025, 8, 17, 11, 0), False),
    ],
)
def test_esta_en_horario(momento, esperado):
    s = armar()
    assert s.esta_en_horario(momento.replace(tzinfo=s.zona)) is esperado


def test_esta_en_horario_convierte_zona():
    """Un momento en UTC se evalua contra la hora de Buenos Aires (UTC-3)."""
    s = armar()
    # 23:00 UTC del miercoles son las 20:00 en Buenos Aires: fuera de horario.
    assert s.esta_en_horario(datetime(2025, 8, 13, 23, 0, tzinfo=ZoneInfo("UTC"))) is False
    # 13:00 UTC son las 10:00 en Buenos Aires: dentro.
    assert s.esta_en_horario(datetime(2025, 8, 13, 13, 0, tzinfo=ZoneInfo("UTC"))) is True


def test_obtener_settings_cachea(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    obtener_settings.cache_clear()
    assert obtener_settings() is obtener_settings()
    obtener_settings.cache_clear()
