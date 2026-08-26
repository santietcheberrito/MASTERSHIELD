"""Empuja una conversacion al CRM y registra que paso.

La regla que manda: **si el CRM falla, la conversacion no se pierde**. Queda
marcada para reintento, el error se registra en `eventos`, y el agente sigue
contestando como si nada. Una integracion caida no puede dejar sin respuesta a
alguien que esta escribiendo.
"""

from __future__ import annotations

import logging

from app import db
from app.crm import documento, notion

logger = logging.getLogger(__name__)

# Cuanto se espera antes de reintentar una sincronizacion fallida.
ESPERA_REINTENTO = "30 minutes"


async def sincronizar(conversacion_id: int) -> bool:
    """Arma el documento y lo manda al CRM. Devuelve si salio bien.

    Nunca propaga: quien la llama es el agente, en medio de una conversacion.
    """
    try:
        doc = await documento.armar(conversacion_id)
    except Exception:
        logger.exception("no se pudo armar el documento | conversacion=%s", conversacion_id)
        await _registrar_error(conversacion_id, "no se pudo armar el documento")
        return False

    try:
        referencia = await notion.sincronizar(doc)
    except notion.NotionNoConfigurado as e:
        # No es un fallo: es que todavia no hay CRM conectado. Se marca para
        # cuando lo haya, sin ensuciar los eventos con errores.
        logger.info("sin CRM configurado, queda pendiente | conversacion=%s: %s",
                    conversacion_id, e)
        await _marcar_pendiente(conversacion_id)
        return False
    except Exception as e:
        logger.exception("fallo la sincronizacion con el CRM | conversacion=%s", conversacion_id)
        await _registrar_error(conversacion_id, f"{type(e).__name__}: {e}"[:500])
        await _marcar_pendiente(conversacion_id)
        return False

    await db.ejecutar(
        """
        UPDATE conversaciones SET
            crm_pendiente       = false,
            crm_reintentar_en   = NULL,
            crm_sincronizada_en = now(),
            crm_referencia      = $2::jsonb
        WHERE id = $1
        """,
        conversacion_id,
        {"destino": "notion", "pagina": referencia},
    )
    await db.ejecutar(
        "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
        "VALUES ($1, 'crm_sincronizado', 'ok', $2)",
        conversacion_id,
        {"destino": "notion", "pagina": referencia, "etapa": doc.lead["etapa"],
         "score": doc.lead["score"]},
    )
    return True


async def _marcar_pendiente(conversacion_id: int) -> None:
    await db.ejecutar(
        f"""
        UPDATE conversaciones SET
            crm_pendiente     = true,
            crm_intentos      = crm_intentos + 1,
            crm_reintentar_en = now() + interval '{ESPERA_REINTENTO}'
        WHERE id = $1
        """,
        conversacion_id,
    )


async def _registrar_error(conversacion_id: int, detalle: str) -> None:
    await db.ejecutar(
        "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
        "VALUES ($1, 'crm_sincronizado', 'error', $2)",
        conversacion_id,
        {"detalle": detalle},
    )
