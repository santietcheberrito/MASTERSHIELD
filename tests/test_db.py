"""Pool de conexiones y helpers."""

import pytest

from app import db


# --- sin base ---------------------------------------------------------------

@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:c@aws-0-us-west-2.pooler.supabase.com:6543/postgres",
        "postgresql://u:c@localhost:6543/base",
    ],
)
def test_detecta_modo_transaccion(dsn):
    assert db._usa_pooler_en_modo_transaccion(dsn) is True


@pytest.mark.parametrize(
    "dsn",
    [
        # Session pooler: conexion dedicada, los prepared statements andan.
        "postgresql://u:c@aws-0-us-west-2.pooler.supabase.com:5432/postgres",
        "postgresql://u:c@db.abcdefgh.supabase.co:5432/postgres",
        "postgresql://u:c@localhost:5432/base",
    ],
)
def test_session_y_directa_no_son_modo_transaccion(dsn):
    assert db._usa_pooler_en_modo_transaccion(dsn) is False


def test_pool_sin_iniciar_explota():
    with pytest.raises(RuntimeError, match="no esta iniciado"):
        db.pool()


async def test_esta_viva_sin_pool_es_false():
    """/health tiene que poder contestar aunque el pool nunca se haya creado."""
    assert await db.esta_viva() is False


# --- con base real ----------------------------------------------------------

@pytest.mark.db
async def test_conecta_y_responde(conexion):
    assert await conexion.fetchval("SELECT 1") == 1


@pytest.mark.db
async def test_jsonb_vuelve_como_dict(base):
    """Sin el codec, asyncpg devuelve jsonb como str y cada lector tendria que
    acordarse de hacer json.loads sobre conversaciones.datos."""
    pool = await db.crear_pool(base.url, minimo=1, maximo=1)
    try:
        async with pool.acquire() as conexion:
            devuelto = await conexion.fetchval("SELECT $1::jsonb", {"linea": "arquitectonico"})
            assert devuelto == {"linea": "arquitectonico"}
            assert isinstance(devuelto, dict)
    finally:
        await pool.close()


@pytest.mark.db
async def test_helpers_del_modulo(base):
    await db.iniciar(base.url, minimo=1, maximo=1)
    try:
        assert await db.valor("SELECT $1::int", 7) == 7
        assert (await db.consultar_una("SELECT 1 AS uno"))["uno"] == 1
        assert len(await db.consultar("SELECT generate_series(1, 3)")) == 3
        assert await db.esta_viva() is True
    finally:
        await db.cerrar()


@pytest.mark.db
async def test_iniciar_es_idempotente(base):
    try:
        primero = await db.iniciar(base.url, minimo=1, maximo=1)
        assert await db.iniciar(base.url) is primero
    finally:
        await db.cerrar()


def test_dsn_que_no_parsea_no_explota():
    """El DSN mal armado tiene que fallar en la validacion de Settings, no aca."""
    assert db._usa_pooler_en_modo_transaccion("postgresql://u[c]@host:5432/base") is False
