"""Ingesta: deduplicacion, reuso de conversacion y agendado del debounce."""

import pytest

from app import ingesta
from app.canales.base import MensajeEntrante

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("settings_de_prueba")]


def mensaje(n: int = 1, chat: str = "7", **extra) -> MensajeEntrante:
    datos = {
        "canal": "telegram",
        "identificador": chat,
        "id_externo": f"telegram:{chat}:{n}",
        "texto": f"mensaje {n}",
        "nombre": "Maria Paez",
        "payload": {"update_id": n},
    }
    datos.update(extra)
    return MensajeEntrante(**datos)


async def _cuantas(conexion, *identificadores) -> int:
    """Cuenta solo las conversaciones del test. Contar la tabla entera ataria el
    resultado a lo que haya quedado de otra prueba."""
    return await conexion.fetchval(
        "SELECT count(*) FROM conversaciones WHERE identificador = ANY($1::text[])",
        list(identificadores),
    )


async def conversacion_de(conexion, chat="7"):
    return await conexion.fetchrow(
        "SELECT * FROM conversaciones WHERE canal = 'telegram' AND identificador = $1",
        chat,
    )


async def test_crea_la_conversacion_y_agenda(pool_en_transaccion):
    conexion = pool_en_transaccion
    assert await ingesta.registrar(mensaje(1), 6) is True

    conv = await conversacion_de(conexion)
    assert conv["estado"] == "activa"
    assert conv["nombre"] == "Maria Paez"
    assert conv["telefono"] is None, "en Telegram el telefono se releva despues"
    assert conv["ultimo_mensaje_en"] is not None

    assert await conexion.fetchval(
        "SELECT count(*) FROM pendientes WHERE conversacion_id = $1", conv["id"]
    ) == 1


async def test_mensaje_duplicado_no_se_guarda_dos_veces(pool_en_transaccion):
    """El caso del reintento del canal."""
    conexion = pool_en_transaccion
    assert await ingesta.registrar(mensaje(1), 6) is True
    assert await ingesta.registrar(mensaje(1), 6) is False

    conv = await conversacion_de(conexion)
    assert await conexion.fetchval(
        "SELECT count(*) FROM mensajes WHERE conversacion_id = $1", conv["id"]
    ) == 1


async def test_el_duplicado_no_corre_la_ventana(pool_en_transaccion):
    """Si un reintento moviera procesar_despues, un canal insistente podria
    postergar la respuesta indefinidamente."""
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 6)
    conv = await conversacion_de(conexion)
    antes = await conexion.fetchval(
        "SELECT procesar_despues FROM pendientes WHERE conversacion_id = $1", conv["id"]
    )

    await ingesta.registrar(mensaje(1), 600)

    despues = await conexion.fetchval(
        "SELECT procesar_despues FROM pendientes WHERE conversacion_id = $1", conv["id"]
    )
    assert despues == antes


async def test_tres_mensajes_seguidos_son_una_sola_pendiente(pool_en_transaccion):
    """La rafaga tipica: tres mensajes, un solo turno agendado."""
    conexion = pool_en_transaccion
    for n in (1, 2, 3):
        assert await ingesta.registrar(mensaje(n), 6) is True

    conv = await conversacion_de(conexion)
    assert await conexion.fetchval(
        "SELECT count(*) FROM mensajes WHERE conversacion_id = $1", conv["id"]
    ) == 3
    assert await conexion.fetchval("SELECT count(*) FROM pendientes") == 1


async def test_cada_mensaje_corre_la_ventana(pool_en_transaccion):
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 6)
    conv = await conversacion_de(conexion)
    primera = await conexion.fetchval(
        "SELECT procesar_despues FROM pendientes WHERE conversacion_id = $1", conv["id"]
    )

    await ingesta.registrar(mensaje(2), 30)

    segunda = await conexion.fetchval(
        "SELECT procesar_despues FROM pendientes WHERE conversacion_id = $1", conv["id"]
    )
    assert segunda > primera


async def test_dos_chats_no_se_mezclan(pool_en_transaccion):
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1, chat="7"), 6)
    await ingesta.registrar(mensaje(1, chat="99"), 6)

    assert await _cuantas(conexion, "7", "99") == 2
    assert await conexion.fetchval(
        "SELECT count(*) FROM pendientes p JOIN conversaciones c ON c.id = p.conversacion_id "
        "WHERE c.identificador = ANY($1::text[])",
        ["7", "99"],
    ) == 2


async def test_el_mismo_numero_en_dos_canales_son_dos_conversaciones(pool_en_transaccion):
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1, chat="+593999123456"), 6)
    await ingesta.registrar(
        mensaje(1, chat="+593999123456", canal="whatsapp", id_externo="whatsapp:wamid.A"), 6
    )
    assert await _cuantas(conexion, "+593999123456") == 2


async def test_el_telefono_compartido_despues_se_guarda(pool_en_transaccion):
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 6)
    await ingesta.registrar(mensaje(2, telefono="+593999123456"), 6)

    assert (await conversacion_de(conexion))["telefono"] == "+593999123456"


async def test_un_dato_conocido_no_se_pisa_con_nulo(pool_en_transaccion):
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1, telefono="+593999123456"), 6)
    await ingesta.registrar(mensaje(2, nombre=None), 6)

    conv = await conversacion_de(conexion)
    assert conv["telefono"] == "+593999123456"
    assert conv["nombre"] == "Maria Paez"


@pytest.mark.parametrize("estado", ["pausada", "derivada"])
async def test_si_un_humano_atiende_se_guarda_pero_no_se_agenda(pool_en_transaccion, estado):
    """El vendedor esta en la conversacion: el agente no contesta, pero el
    historial tiene que quedar completo para la nota de Kommo."""
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 6)
    conv = await conversacion_de(conexion)
    await conexion.execute("DELETE FROM pendientes WHERE conversacion_id = $1", conv["id"])
    await conexion.execute(
        "UPDATE conversaciones SET estado = $2 WHERE id = $1", conv["id"], estado
    )

    assert await ingesta.registrar(mensaje(2), 6) is True

    assert await conexion.fetchval(
        "SELECT count(*) FROM mensajes WHERE conversacion_id = $1", conv["id"]
    ) == 2
    assert await conexion.fetchval("SELECT count(*) FROM pendientes") == 0
    assert (await conversacion_de(conexion))["estado"] == estado


@pytest.mark.parametrize("estado", ["cerrada", "calificada"])
async def test_si_vuelve_a_escribir_se_retoma_la_conversacion(pool_en_transaccion, estado):
    """No se crea una nueva: se reabre la que ya existe."""
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 6)
    conv = await conversacion_de(conexion)
    await conexion.execute(
        "UPDATE conversaciones SET estado = $2 WHERE id = $1", conv["id"], estado
    )

    await ingesta.registrar(mensaje(2), 6)

    reabierta = await conversacion_de(conexion)
    assert reabierta["id"] == conv["id"]
    assert reabierta["estado"] == "activa"
    assert await _cuantas(conexion, "7") == 1


# --- intervención humana ----------------------------------------------------

async def test_si_un_vendedor_contesta_el_agente_se_calla(pool_en_transaccion):
    """Dos respuestas a la vez, una del vendedor y otra del agente, es lo peor
    que puede ver un cliente."""
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 60)
    conv = await conversacion_de(conexion)
    assert await conexion.fetchval("SELECT count(*) FROM pendientes WHERE conversacion_id=$1",
                                   conv["id"]) == 1

    id_conv = await ingesta.registrar_intervencion_humana(
        "telegram", "7", "Buenas, le escribe Juan de MasterShield", "telegram:7:manual-1"
    )

    assert id_conv == conv["id"]
    assert (await conversacion_de(conexion))["estado"] == "pausada"
    # El turno agendado se cancela: si no, la respuesta del agente llegaría
    # encima de la del vendedor.
    assert await conexion.fetchval(
        "SELECT count(*) FROM pendientes WHERE conversacion_id=$1", conv["id"]
    ) == 0
    assert await conexion.fetchval(
        "SELECT rol FROM mensajes WHERE conversacion_id=$1 ORDER BY id DESC LIMIT 1", conv["id"]
    ) == "vendedor"


async def test_estando_pausada_los_mensajes_se_guardan_pero_no_se_contestan(
    pool_en_transaccion,
):
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 60)
    await ingesta.registrar_intervencion_humana("telegram", "7", "le escribe Juan", "m-1")

    assert await ingesta.registrar(mensaje(2), 60) is True

    conv = await conversacion_de(conexion)
    assert conv["estado"] == "pausada"
    assert await conexion.fetchval(
        "SELECT count(*) FROM mensajes WHERE conversacion_id=$1", conv["id"]
    ) == 3
    assert await conexion.fetchval(
        "SELECT count(*) FROM pendientes WHERE conversacion_id=$1", conv["id"]
    ) == 0


async def test_una_conversacion_derivada_no_se_vuelve_a_pausar(pool_en_transaccion):
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 60)
    conv = await conversacion_de(conexion)
    await conexion.execute("UPDATE conversaciones SET estado='derivada' WHERE id=$1", conv["id"])

    assert await ingesta.registrar_intervencion_humana("telegram", "7", "hola", "m-2") is None
    assert (await conversacion_de(conexion))["estado"] == "derivada"


async def test_sin_conversacion_no_pasa_nada(pool_en_transaccion):
    assert await ingesta.registrar_intervencion_humana(
        "telegram", "999999", "hola", "m-3"
    ) is None


async def test_el_telefono_relevado_no_lo_pisa_el_del_canal(pool_en_transaccion):
    """En WhatsApp el número del remitente viene en cada mensaje. Si pisara al
    relevado, el número que el cliente pidió que le llamen —una oficina, un
    fijo— se perdería en cuanto escribiera de nuevo."""
    conexion = pool_en_transaccion
    from app.agente import herramientas

    await ingesta.registrar(
        mensaje(1, chat="+5490000000001", canal="whatsapp",
                id_externo="whatsapp:1", telefono="+5490000000001"), 6)
    conv = await conexion.fetchrow(
        "SELECT id, telefono FROM conversaciones WHERE identificador = '+5490000000001'")
    assert conv["telefono"] == "+5490000000001", "el del canal sirve como valor inicial"

    await herramientas.guardar_dato(conv["id"], "telefono", "+593 2 1234567")

    # Llega otro mensaje del mismo remitente.
    await ingesta.registrar(
        mensaje(2, chat="+5490000000001", canal="whatsapp",
                id_externo="whatsapp:2", telefono="+5490000000001"), 6)

    assert await conexion.fetchval(
        "SELECT telefono FROM conversaciones WHERE id = $1", conv["id"]
    ) == "+59321234567", "gana el que dio el cliente"


async def test_el_telefono_del_canal_siembra_los_datos(pool_en_transaccion):
    """En WhatsApp ya sabemos a qué número escribe la persona.

    Sembrarlo en `datos` desde el primer mensaje es lo que le permite al agente
    confirmarlo —"¿lo llamamos a este mismo número?"— en vez de hacerle tipear
    algo que tenemos delante.
    """
    conexion = pool_en_transaccion

    await ingesta.registrar(
        mensaje(1, chat="+593999123456", canal="whatsapp",
                id_externo="whatsapp:siembra", telefono="+593999123456"), 6)

    datos = await conexion.fetchval(
        "SELECT datos FROM conversaciones WHERE identificador = '+593999123456'")
    assert datos["telefono"] == "+593999123456"


async def test_sin_telefono_de_canal_los_datos_arrancan_vacios(pool_en_transaccion):
    """En Telegram no viene, y `datos` no puede quedar con una clave en NULL:
    `finalizar_calificacion` la leería como presente y cerraría sin número."""
    conexion = pool_en_transaccion

    await ingesta.registrar(mensaje(1, chat="555001", id_externo="tg:siembra"), 6)

    assert await conexion.fetchval(
        "SELECT datos FROM conversaciones WHERE identificador = '555001'") == {}
