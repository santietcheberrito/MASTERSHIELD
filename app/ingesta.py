"""Persistencia de un mensaje entrante y agendado del turno.

Lo llaman el webhook y el poller. Es deliberadamente barato: no hace ninguna
llamada al modelo ni al BSP. Todo el trabajo pesado es del worker.
"""

from __future__ import annotations

import logging

import asyncpg

from app import db
from app.config import obtener_settings
from app.canales.base import MensajeEntrante

logger = logging.getLogger(__name__)

# Mientras un humano se ocupa de la conversacion, el agente no contesta. El
# mensaje se guarda igual: el historial tiene que quedar completo para la nota
# que se sube a Kommo.
ESTADOS_SIN_AGENTE = ("pausada", "derivada")

# Si el cliente vuelve a escribir despues de darla por terminada, se retoma la
# misma conversacion en vez de crear otra.
ESTADOS_QUE_SE_REABREN = ("cerrada", "calificada")


# Todo en una sola sentencia, y no en una transaccion con cuatro consultas.
#
# El webhook tiene un presupuesto de 500ms y cada viaje a la base se los come:
# BEGIN, upsert de la conversacion, insert del mensaje, upsert de la pendiente y
# COMMIT son cinco idas y vueltas. Con las CTE es una. Ademas queda atomico sin
# transaccion explicita, porque una sentencia lo es por definicion.
#
# Dos imprecisiones asumidas, las dos inofensivas: un mensaje duplicado igual
# toca `ultimo_mensaje_en` y, si la conversacion estaba cerrada, igual la
# reabre. Un duplicado es el reintento de un mensaje que acaba de llegar, asi
# que el desfase es de milisegundos. Lo que si esta garantizado es que no se
# guarda dos veces ni corre la ventana del debounce, que es lo que importa.
_REGISTRAR = """
WITH conv AS (
    -- El telefono del canal siembra `datos` en el primer mensaje: en WhatsApp
    -- ya sabemos a que numero escribe la persona, y preguntarselo es hacerle
    -- tipear algo que tenemos delante. El agente lo confirma, no lo releva.
    -- Solo en el INSERT: despues manda `guardar_dato`.
    INSERT INTO conversaciones (canal, identificador, nombre, telefono, datos, ultimo_mensaje_en)
    VALUES ($1, $2, $3, $4,
            CASE WHEN $4::text IS NULL THEN '{}'::jsonb
                 ELSE jsonb_build_object('telefono', $4::text) END,
            now())
    ON CONFLICT (canal, identificador) DO UPDATE SET
        -- Un dato que ya teniamos no se pisa con un NULL del mensaje nuevo.
        nombre            = COALESCE(EXCLUDED.nombre, conversaciones.nombre),
        -- El telefono del canal es solo el valor inicial. En WhatsApp viene en
        -- cada mensaje, y si pisara al guardado, el numero que el cliente pidio
        -- que le llamen —una oficina, un fijo— se perderia en el mensaje
        -- siguiente. Gana el que ya esta: `guardar_dato` es quien lo cambia.
        telefono          = COALESCE(conversaciones.telefono, EXCLUDED.telefono),
        ultimo_mensaje_en = now(),
        estado            = CASE
                                WHEN conversaciones.estado = ANY($5::text[]) THEN 'activa'
                                ELSE conversaciones.estado
                            END
    RETURNING id, estado
),
msg AS (
    INSERT INTO mensajes (conversacion_id, rol, tipo, contenido, id_externo, payload)
    SELECT conv.id, 'cliente', $6, $7, $8, $9 FROM conv
    ON CONFLICT (id_externo) DO NOTHING
    RETURNING id, conversacion_id
),
pend AS (
    -- Solo si el mensaje era nuevo (sale de msg) y si el agente atiende esta
    -- conversacion. El corazon del debounce: la ventana se corre hacia adelante
    -- sin leer antes y sin importar si ya habia una fila.
    INSERT INTO pendientes (conversacion_id, procesar_despues)
    SELECT msg.conversacion_id, now() + make_interval(secs => $10)
    FROM msg, conv
    WHERE conv.estado <> ALL($11::text[])
    ON CONFLICT (conversacion_id) DO UPDATE SET
        procesar_despues = EXCLUDED.procesar_despues
    RETURNING conversacion_id
)
SELECT conv.id AS conversacion_id,
       conv.estado,
       EXISTS (SELECT 1 FROM msg)  AS guardado,
       EXISTS (SELECT 1 FROM pend) AS agendado
FROM conv
"""


async def registrar(mensaje: MensajeEntrante, demora_seg: int) -> bool:
    """Guarda el mensaje y agenda el turno. Devuelve False si era duplicado."""
    try:
        fila = await db.consultar_una(
            _REGISTRAR,
            mensaje.canal,
            mensaje.identificador,
            mensaje.nombre,
            mensaje.telefono,
            list(ESTADOS_QUE_SE_REABREN),
            mensaje.tipo,
            mensaje.texto,
            mensaje.id_externo,
            mensaje.payload,
            demora_seg,
            list(ESTADOS_SIN_AGENTE),
        )
    except asyncpg.PostgresError:
        logger.exception(
            "no se pudo registrar el mensaje", extra={"id_externo": mensaje.id_externo}
        )
        raise

    if not fila["guardado"]:
        logger.info(
            "mensaje duplicado descartado", extra={"id_externo": mensaje.id_externo}
        )
        return False

    logger.info(
        "mensaje registrado",
        extra={
            "conversacion": fila["conversacion_id"],
            "canal": mensaje.canal,
            "tipo": mensaje.tipo,
            "agendado": fila["agendado"],
        },
    )
    return True


# ---------------------------------------------------------------------------
# Intervencion humana
# ---------------------------------------------------------------------------

_MENSAJE_DE_VENDEDOR = """
WITH conv AS (
    -- `pausada` entra en el WHERE a proposito: el segundo mensaje del vendedor
    -- tiene que correr el vencimiento, igual que un mensaje del cliente corre
    -- la ventana del debounce. Sin eso el agente volveria a hablar a las seis
    -- horas en medio de una conversacion que el asesor sigue teniendo.
    --
    -- `derivada` no: ahi la conversacion se entrego a una persona a proposito y
    -- el agente no vuelve por vencimiento.
    UPDATE conversaciones SET
        estado        = 'pausada',
        pausada_hasta = now() + make_interval(hours => $5),
        ultimo_mensaje_en = now()
    WHERE canal = $1 AND identificador = $2 AND estado <> 'derivada'
    RETURNING id
),
msg AS (
    INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo, procesado)
    SELECT conv.id, 'vendedor', $3, $4, true FROM conv
    ON CONFLICT (id_externo) DO NOTHING
    RETURNING conversacion_id
),
-- Se cancela el turno agendado: si el vendedor ya contesto, la respuesta del
-- agente llegaria despues y encima de la suya.
pend AS (
    DELETE FROM pendientes WHERE conversacion_id IN (SELECT id FROM conv)
)
SELECT id FROM conv
"""


async def registrar_intervencion_humana(
    canal: str, identificador: str, texto: str, id_externo: str
) -> int | None:
    """Un vendedor contesto a mano: el agente deja de atender esa conversacion.

    Devuelve el id de la conversacion, o None si no habia ninguna activa.

    Quien detecta la intervencion es el canal, y no todos pueden: la Cloud API
    de WhatsApp avisa de los mensajes salientes que no mandamos nosotros, la
    API de bots de Telegram no. Por eso el mecanismo vive aca y el disparador
    en cada adaptador.
    """
    horas = obtener_settings().pausa_por_humano_horas
    id_conversacion = await db.valor(
        _MENSAJE_DE_VENDEDOR, canal, identificador, texto, id_externo, horas
    )
    if id_conversacion is None:
        return None

    logger.info("conversacion pausada por intervencion humana | conversacion=%s",
                id_conversacion)
    await db.ejecutar(
        "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
        "VALUES ($1, 'pausada_por_humano', 'ok', $2)",
        id_conversacion,
        {"canal": canal, "horas": horas},
    )
    return id_conversacion
