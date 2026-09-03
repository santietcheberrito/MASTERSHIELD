"""La pausa por intervención humana y su vencimiento.

Cuando un asesor contesta a mano el agente se calla, y hasta ahora se callaba
para siempre: `pausada` era un estado sin salida. A la semana siguiente el mismo
cliente escribía por otra cosa y no le contestaba nadie.
"""

import pytest

from app import ingesta, pausas

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("settings_de_prueba")]


async def _conversacion(conexion, identificador="pausa-test", estado="activa"):
    return await conexion.fetchval(
        "INSERT INTO conversaciones (canal, identificador, nombre, estado) "
        "VALUES ('consola', $1, 'Ines Robalino', $2) RETURNING id",
        identificador, estado,
    )


async def _vencer(conexion, id_conv):
    await conexion.execute(
        "UPDATE conversaciones SET pausada_hasta = now() - interval '1 minute' "
        "WHERE id = $1", id_conv,
    )


# --- la pausa se activa y se renueva ----------------------------------------

async def test_un_mensaje_del_vendedor_pausa_con_vencimiento(pool_en_transaccion):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)

    assert await ingesta.registrar_intervencion_humana(
        "consola", "pausa-test", "yo sigo, gracias", "eco-1"
    ) == id_conv

    fila = await conexion.fetchrow(
        "SELECT estado, pausada_hasta FROM conversaciones WHERE id = $1", id_conv
    )
    assert fila["estado"] == "pausada"
    assert fila["pausada_hasta"] is not None


async def test_el_segundo_mensaje_del_vendedor_corre_el_vencimiento(pool_en_transaccion):
    """Si el asesor sigue conversando, el agente sigue callado. Sin esto volvía
    a hablar a las seis horas en medio de una conversación ajena.

    Lo que se verifica es que la segunda llamada encuentra la conversación y la
    vuelve a escribir: antes el WHERE excluía `pausada` y devolvía None, así que
    el vencimiento quedaba fijado por el primer mensaje. No se compara el
    timestamp porque `now()` es constante dentro de una transacción y el test
    corre entero en una.
    """
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)

    await ingesta.registrar_intervencion_humana("consola", "pausa-test", "hola", "eco-1")

    assert await ingesta.registrar_intervencion_humana(
        "consola", "pausa-test", "y otra cosa", "eco-2"
    ) == id_conv, "una conversación ya pausada se vuelve a tomar para renovarla"

    assert await conexion.fetchval(
        "SELECT count(*) FROM mensajes WHERE conversacion_id = $1 AND rol = 'vendedor'",
        id_conv,
    ) == 2, "los dos mensajes del asesor quedan en el historial"


async def test_una_conversacion_derivada_no_se_repausa(pool_en_transaccion):
    """Derivada es una entrega deliberada a una persona: el agente no vuelve por
    vencimiento, y una pausa la reabriría a las seis horas."""
    conexion = pool_en_transaccion
    await _conversacion(conexion, estado="derivada")

    assert await ingesta.registrar_intervencion_humana(
        "consola", "pausa-test", "lo sigo yo", "eco-1"
    ) is None


async def test_pausar_cancela_el_turno_agendado(pool_en_transaccion):
    """El agente ya tenía una respuesta en camino. Si sale, llega encima de la
    del asesor."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await conexion.execute(
        "INSERT INTO pendientes (conversacion_id, procesar_despues) "
        "VALUES ($1, now() + interval '30 seconds')", id_conv,
    )

    await ingesta.registrar_intervencion_humana("consola", "pausa-test", "yo sigo", "eco-1")

    assert await conexion.fetchval(
        "SELECT count(*) FROM pendientes WHERE conversacion_id = $1", id_conv) == 0


# --- el despertar -----------------------------------------------------------

async def test_al_vencer_vuelve_a_activa(pool_en_transaccion):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await ingesta.registrar_intervencion_humana("consola", "pausa-test", "yo sigo", "eco-1")
    await _vencer(conexion, id_conv)

    await pausas.Despertador().una_vuelta()

    fila = await conexion.fetchrow(
        "SELECT estado, pausada_hasta FROM conversaciones WHERE id = $1", id_conv)
    assert fila["estado"] == "activa"
    assert fila["pausada_hasta"] is None


async def test_no_despierta_una_pausa_vigente(pool_en_transaccion):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await ingesta.registrar_intervencion_humana("consola", "pausa-test", "yo sigo", "eco-1")

    await pausas.Despertador().una_vuelta()

    assert await conexion.fetchval(
        "SELECT estado FROM conversaciones WHERE id = $1", id_conv) == "pausada"


async def test_si_nadie_quedo_esperando_el_agente_no_reaparece(pool_en_transaccion):
    """El asesor cerró el tema y no hay nada del cliente sin contestar. Volver a
    escribir sería el agente saludando porque venció un reloj."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await ingesta.registrar_intervencion_humana("consola", "pausa-test", "listo", "eco-1")
    await _vencer(conexion, id_conv)

    await pausas.Despertador().una_vuelta()

    assert await conexion.fetchval(
        "SELECT count(*) FROM pendientes WHERE conversacion_id = $1", id_conv) == 0


async def test_si_el_cliente_quedo_esperando_se_evalua(pool_en_transaccion):
    """No se contesta automáticamente: se agenda un turno para que el agente
    mire qué quedó. Un "gracias" y una consulta nueva no son lo mismo, y esa
    diferencia no se decide acá."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await ingesta.registrar_intervencion_humana("consola", "pausa-test", "listo", "eco-1")
    await conexion.execute(
        "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo, procesado) "
        "VALUES ($1, 'cliente', 'una consulta mas', 'c-1', false)", id_conv,
    )
    await _vencer(conexion, id_conv)

    await pausas.Despertador().una_vuelta()

    assert await conexion.fetchval(
        "SELECT count(*) FROM pendientes WHERE conversacion_id = $1", id_conv) == 1


async def test_el_mensaje_del_vendedor_no_cuenta_como_alguien_esperando(pool_en_transaccion):
    """Entra a `mensajes` ya procesado justamente para esto: si contara, toda
    pausa despertaría con un turno agendado."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await ingesta.registrar_intervencion_humana(
        "consola", "pausa-test", "lo llamo en un rato", "eco-1")
    await _vencer(conexion, id_conv)

    await pausas.Despertador().una_vuelta()

    assert await conexion.fetchval(
        "SELECT count(*) FROM pendientes WHERE conversacion_id = $1", id_conv) == 0


async def test_despertar_dos_veces_no_duplica_el_turno(pool_en_transaccion):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await ingesta.registrar_intervencion_humana("consola", "pausa-test", "listo", "eco-1")
    await conexion.execute(
        "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo, procesado) "
        "VALUES ($1, 'cliente', 'sigo esperando', 'c-1', false)", id_conv,
    )
    await _vencer(conexion, id_conv)

    despertador = pausas.Despertador()
    assert await despertador.una_vuelta() == 1
    assert await despertador.una_vuelta() == 0, "ya no está pausada"

    assert await conexion.fetchval(
        "SELECT count(*) FROM pendientes WHERE conversacion_id = $1", id_conv) == 1
