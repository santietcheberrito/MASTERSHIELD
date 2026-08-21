"""Persistencia de un mensaje entrante y agendado del turno.

Lo llaman el webhook y el poller. Es deliberadamente barato: no hace ninguna
llamada al modelo ni al BSP. Todo el trabajo pesado es del worker.
"""

from __future__ import annotations

import logging

import asyncpg

from app import db
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
    INSERT INTO conversaciones (canal, identificador, nombre, telefono, ultimo_mensaje_en)
    VALUES ($1, $2, $3, $4, now())
    ON CONFLICT (canal, identificador) DO UPDATE SET
        -- Un dato que ya teniamos no se pisa con un NULL del mensaje nuevo.
        nombre            = COALESCE(EXCLUDED.nombre, conversaciones.nombre),
        telefono          = COALESCE(EXCLUDED.telefono, conversaciones.telefono),
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


async def registrar(mensaje: MensajeEntrante, ventana_seg: int) -> bool:
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
            ventana_seg,
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
