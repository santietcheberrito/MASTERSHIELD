"""Fixtures compartidas."""

import asyncio
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest

from app import db
from app.config import CREDENCIALES_OPCIONALES, Settings, obtener_settings

RAIZ = Path(__file__).resolve().parent.parent

# Variables que Settings lee del entorno. Se limpian en cada test para que la
# maquina del que corre los tests no cambie el resultado.
VARIABLES = (
    "DATABASE_URL",
    "DEMORA_RESPUESTA_MIN_SEG",
    "DEMORA_RESPUESTA_MAX_SEG",
    "HORARIO_ATENCION",
    "TZ",
    "PAIS",
    "PREFIJO_TELEFONICO",
    "TELEGRAM_MODO",
    "NOTION_DATA_SOURCE_ID",
    "CRM_DESTINO",
    "WHATSAPP_VERIFY_TOKEN",
    "MODELO_AGENTE",
    "PROVEEDOR_MODELO",
    "MAX_ITERACIONES_HERRAMIENTAS",
    "TELEGRAM_WEBHOOK_SECRET",
    "MENSAJES_POR_HORA_MAX",
    "MENSAJES_POR_MINUTO_MAX",
    "LARGO_MAXIMO_MENSAJE",
    "MENSAJES_TOTALES_MAX",
    "LOG_LEVEL",
    *(c.upper() for c in CREDENCIALES_OPCIONALES),
)


class BaseDePrueba:
    """Envuelve el DSN para que no se filtre en la salida de pytest.

    Cuando un test recibe una fixture y falla, pytest imprime el valor del
    argumento en el encabezado del error. Con el DSN crudo eso deja la clave de
    la base en la consola y en el log de CI.
    """

    def __init__(self, url: str) -> None:
        self.url = url

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or "?"

    def __repr__(self) -> str:
        return f"<base {self.host}>"


def _dsn_para_tests() -> str | None:
    """DSN de una base real, si hay alguna a mano.

    Se resuelve al importar el modulo y no dentro de una fixture, porque
    `entorno_limpio` borra DATABASE_URL del entorno antes de cada test.
    """
    for variable in ("DATABASE_URL_TEST", "DATABASE_URL"):
        if os.environ.get(variable):
            return os.environ[variable]

    archivo = RAIZ / ".env"
    if archivo.exists():
        for linea in archivo.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea.startswith("DATABASE_URL=") and not linea.startswith("#"):
                valor = linea.split("=", 1)[1].strip().strip('"').strip("'")
                if valor:
                    return valor
    return None


DSN = _dsn_para_tests()


def _motivo_para_saltear(url: str) -> str | None:
    """Prueba una conexion. Devuelve el motivo del skip, o None si la base sirve.

    "No hay base a mano" se saltea; "la base contesta pero rechaza" no. Una
    clave mal puesta o un esquema sin migrar tienen que romper los tests, no
    esconderse detras de un skip verde.
    """
    async def _conectar() -> None:
        conexion = await asyncpg.connect(url, statement_cache_size=0, timeout=10)
        await conexion.close()

    try:
        asyncio.run(_conectar())
    except (socket.gaierror, ConnectionRefusedError, asyncio.TimeoutError, TimeoutError) as e:
        host = urlparse(url).hostname or "?"
        return f"la base no esta disponible ({host}): {type(e).__name__}"
    except OSError as e:
        host = urlparse(url).hostname or "?"
        return f"la base no esta disponible ({host}): {e.strerror or e}"
    return None


_motivo_cacheado: str | None = None
_ya_probado = False


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch):
    """Aisla a Settings de la maquina: ni variables de entorno ni .env.

    Limpiar solo el entorno no alcanzaba: Settings lee `.env`, asi que cambiar
    ahi VENTANA_BUFFER_SEG rompia un test que no tenia nada que ver. El valor
    que usa un test tiene que estar escrito en el test.
    """
    for variable in VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture
def base() -> BaseDePrueba:
    global _motivo_cacheado, _ya_probado

    if not DSN:
        pytest.skip("sin DATABASE_URL: se saltean los tests marcados con `db`")

    if not _ya_probado:
        _motivo_cacheado = _motivo_para_saltear(DSN)
        _ya_probado = True

    if _motivo_cacheado:
        pytest.skip(_motivo_cacheado)

    return BaseDePrueba(DSN)


@pytest.fixture
async def conexion(base):
    """Conexion dentro de una transaccion que siempre se revierte.

    Estos tests pueden correr contra la base real del cliente, asi que nada de
    lo que escriben tiene que quedar. El rollback lo garantiza incluso si el
    test falla a la mitad.
    """
    conn = await asyncpg.connect(base.url, statement_cache_size=0)
    # Los mismos codecs que registra el pool de la app: sin esto los tests
    # verian `conversaciones.datos` como str y no como dict, que no es como lo
    # va a ver el codigo que corre en produccion.
    await db._inicializar_conexion(conn)
    transaccion = conn.transaction()
    await transaccion.start()
    try:
        yield conn
    finally:
        await transaccion.rollback()
        await conn.close()


class _Adquisicion:
    """Imita `pool.acquire()` devolviendo siempre la misma conexion."""

    def __init__(self, conexion):
        self._conexion = conexion

    async def __aenter__(self):
        return self._conexion

    async def __aexit__(self, *_):
        return False


class _PoolFalso:
    def __init__(self, conexion):
        self._conexion = conexion

    def acquire(self):
        return _Adquisicion(self._conexion)


@pytest.fixture
def pool_en_transaccion(conexion, monkeypatch):
    """Hace que `db.pool()` devuelva la conexion de test.

    `ingesta` y `worker` usan el pool del modulo, no una conexion suelta. Sin
    esto habria que dejarlos escribir de verdad y limpiar despues, con el riesgo
    de olvidarse algo en la base del cliente. Asi todo lo que escriben queda
    dentro de la transaccion que se revierte al terminar el test.

    Ojo con una consecuencia: dentro de una transaccion `now()` no avanza, asi
    que las ventanas de tiempo se manejan con valores explicitos.
    """
    monkeypatch.setattr(db, "pool", lambda: _PoolFalso(conexion))
    return conexion


@pytest.fixture
def settings_de_prueba(monkeypatch):
    """Configuracion valida para el codigo que llama a `obtener_settings()`.

    `entorno_limpio` deja el entorno sin DATABASE_URL para que los tests de
    configuracion sean deterministas, pero cualquier modulo que lea settings
    por dentro explota con eso. Los que lo necesitan piden esta fixture.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://usuario:clave@host:5432/base")
    # Ningun destino por defecto: un test no puede escribir sin querer en el
    # CRM real. El que pruebe un destino lo pide explicitamente.
    monkeypatch.setenv("CRM_DESTINO", "ninguno")
    obtener_settings.cache_clear()
    yield
    obtener_settings.cache_clear()


@pytest.fixture
def con_promocion(monkeypatch):
    """Pone la promoción del mes en vigencia, por el mismo motivo que su opuesta.

    Un test que afirme "el especial es 37" pasa en septiembre y falla en
    octubre, y esa falla no dice nada sobre el código: dice qué día se corrió.
    Paso el 1/10/2026, con dos tests así.
    """
    from app import precios

    monkeypatch.setattr(precios, "_especial_vigente", lambda cfg, hoy=None: True)


@pytest.fixture
def sin_promocion(monkeypatch):
    """Saca la promoción del mes, para que los precios no dependan del calendario.

    El precio especial vence a fin de mes y el código lo respeta. Un test que
    afirme "el presupuesto es 42 × 25" sin fijar esto pasa en octubre y falla en
    septiembre, o al revés: la falla no dice nada sobre el código.
    """
    from app import precios

    monkeypatch.setattr(precios, "_especial_vigente", lambda cfg, hoy=None: False)
