"""Fixtures compartidas."""

import os

import pytest

from app.config import CREDENCIALES_OPCIONALES

# Variables que Settings lee del entorno. Se limpian en cada test para que la
# maquina del que corre los tests no cambie el resultado.
VARIABLES = (
    "DATABASE_URL",
    "VENTANA_BUFFER_SEG",
    "HORARIO_ATENCION",
    "TZ",
    "LOG_LEVEL",
    *(c.upper() for c in CREDENCIALES_OPCIONALES),
)


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch):
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def database_url() -> str | None:
    """URL de una base real, si el que corre los tests la puso en el entorno."""
    return os.environ.get("DATABASE_URL_TEST")
