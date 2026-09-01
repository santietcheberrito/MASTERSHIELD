"""Detección de uso anómalo.

Cada mensaje que entra es una llamada al modelo, y cada llamada cuesta. Sin un
tope, cualquiera con el número puede hacer una factura. Lo que se verifica acá
es que los umbrales corten antes de gastar, y que un cliente normal —aunque sea
conversador— nunca los toque.
"""

import pytest

from app import limites

pytestmark = pytest.mark.db

TOPES = {"por_hora": 30, "por_minuto": 10, "largo_maximo": 4000, "total_maximo": 200}


async def _conversacion(conexion) -> int:
    return await conexion.fetchval(
        "INSERT INTO conversaciones (canal, identificador) "
        "VALUES ('consola', 'limites-test') RETURNING id"
    )


async def _mensajes(conexion, id_conv, cantidad, *, hace="0 seconds", largo=30):
    for n in range(cantidad):
        await conexion.execute(
            "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo, creado_en) "
            f"VALUES ($1, 'cliente', $2, $3, now() - interval '{hace}')",
            id_conv, "x" * largo, f"lim:{id_conv}:{hace}:{n}",
        )


async def test_una_conversacion_normal_no_dispara_nada(pool_en_transaccion):
    """Un cliente conversador manda 15 mensajes en una charla. Eso es normal."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensajes(conexion, id_conv, 15, hace="30 minutes")

    assert await limites.revisar(id_conv, **TOPES) is None


async def test_treinta_mensajes_en_una_hora_dispara(pool_en_transaccion):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensajes(conexion, id_conv, 30, hace="10 minutes")

    motivo = await limites.revisar(id_conv, **TOPES)
    assert motivo is not None
    assert "ultima hora" in motivo


async def test_los_mensajes_viejos_no_cuentan(pool_en_transaccion):
    """El límite es por hora, no acumulado: alguien que vuelve al día siguiente
    arranca de cero."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensajes(conexion, id_conv, 40, hace="3 hours")

    assert await limites.revisar(id_conv, **TOPES) is None


async def test_una_ráfaga_en_un_minuto_dispara_antes(pool_en_transaccion):
    """Diez mensajes en un minuto no es alguien escribiendo, es un script."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensajes(conexion, id_conv, 10, hace="10 seconds")

    motivo = await limites.revisar(id_conv, **TOPES)
    assert motivo is not None
    assert "un minuto" in motivo


async def test_un_mensaje_enorme_dispara(pool_en_transaccion):
    """Un solo mensaje larguísimo es una forma barata de quemar tokens."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensajes(conexion, id_conv, 1, hace="1 minute", largo=5000)

    motivo = await limites.revisar(id_conv, **TOPES)
    assert motivo is not None
    assert "caracteres" in motivo


async def test_una_conversacion_que_no_termina_nunca_dispara(pool_en_transaccion):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await _mensajes(conexion, id_conv, 200, hace="5 days")

    motivo = await limites.revisar(id_conv, **TOPES)
    assert motivo is not None
    assert "no termina nunca" in motivo


async def test_los_mensajes_del_agente_no_cuentan(pool_en_transaccion):
    """Si contaran, una conversación larga se cortaría sola por las respuestas
    del propio agente."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    for n in range(40):
        await conexion.execute(
            "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo) "
            "VALUES ($1, 'agente', 'respuesta', $2)", id_conv, f"ag:{id_conv}:{n}",
        )

    assert await limites.revisar(id_conv, **TOPES) is None


async def test_una_conversacion_sin_mensajes(pool_en_transaccion):
    assert await limites.revisar(await _conversacion(pool_en_transaccion), **TOPES) is None
