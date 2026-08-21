"""Pool de conexiones a PostgreSQL y helpers de consulta.

Un unico pool para todo el proceso. El webhook y el worker corren en el mismo
proceso de FastAPI, asi que comparten el pool: no hay razon para tener dos.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# El pooler de Supabase atiende en dos puertos y solo uno da problemas:
#
#   5432 (session)     — conexion dedicada. Los prepared statements funcionan.
#   6543 (transaction) — cada query puede caer en otra conexion del servidor, y
#                        ahi el cache de prepared statements de asyncpg explota
#                        con "prepared statement _pg_N already exists".
#
# Por eso la deteccion es por puerto y no por host: el mismo host del pooler en
# 5432 no necesita el cache desactivado, y desactivarlo cuesta un viaje de ida
# y vuelta extra en cada consulta.
_PUERTOS_MODO_TRANSACCION = (6543,)


async def _inicializar_conexion(conexion: asyncpg.Connection) -> None:
    """Codecs que aplican a cada conexion nueva del pool.

    Sin esto asyncpg devuelve las columnas jsonb como texto y cada lugar que
    lee `conversaciones.datos` tendria que acordarse de hacer json.loads.
    """
    for tipo in ("json", "jsonb"):
        await conexion.set_type_codec(
            tipo,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )


def _usa_pooler_en_modo_transaccion(dsn: str) -> bool:
    try:
        puerto = urlparse(dsn).port
    except ValueError:
        # Un DSN que no parsea no es asunto de esta funcion: que falle mas
        # adelante, con el error de conexion, y no aca con uno enganoso.
        return False
    return puerto in _PUERTOS_MODO_TRANSACCION


async def crear_pool(
    dsn: str,
    *,
    minimo: int = 1,
    maximo: int = 10,
    timeout_comando: float = 30.0,
) -> asyncpg.Pool:
    """Crea un pool. Se usa directo en los tests; la app usa iniciar()."""
    extra: dict[str, Any] = {}
    if _usa_pooler_en_modo_transaccion(dsn):
        extra["statement_cache_size"] = 0
        logger.info("dsn apunta al pooler en modo transaccion: cache de statements desactivado")

    return await asyncpg.create_pool(
        dsn,
        min_size=minimo,
        max_size=maximo,
        command_timeout=timeout_comando,
        init=_inicializar_conexion,
        **extra,
    )


async def iniciar(dsn: str, **kwargs: Any) -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    _pool = await crear_pool(dsn, **kwargs)
    return _pool


async def cerrar() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("El pool no esta iniciado: falta llamar a db.iniciar()")
    return _pool


async def ejecutar(sql: str, *args: Any) -> str:
    async with pool().acquire() as conexion:
        return await conexion.execute(sql, *args)


async def consultar(sql: str, *args: Any) -> list[asyncpg.Record]:
    async with pool().acquire() as conexion:
        return await conexion.fetch(sql, *args)


async def consultar_una(sql: str, *args: Any) -> asyncpg.Record | None:
    async with pool().acquire() as conexion:
        return await conexion.fetchrow(sql, *args)


async def valor(sql: str, *args: Any) -> Any:
    async with pool().acquire() as conexion:
        return await conexion.fetchval(sql, *args)


async def esta_viva(timeout: float = 3.0) -> bool:
    """Para /health. No propaga la excepcion: devuelve False y la loguea.

    El servicio tiene que responder aunque la base este caida, para que el
    healthcheck de Railway pueda distinguir "proceso muerto" de "base caida".
    """
    if _pool is None:
        return False
    try:
        async with pool().acquire() as conexion:
            return await conexion.fetchval("SELECT 1", timeout=timeout) == 1
    except Exception:
        logger.exception("la base no responde")
        return False
