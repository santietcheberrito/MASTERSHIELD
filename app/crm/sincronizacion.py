"""Empuja una conversacion al CRM y registra que paso.

La regla que manda: **si el CRM falla, la conversacion no se pierde**. Queda
marcada para reintento, el error se registra en `eventos`, y el agente sigue
contestando como si nada. Una integracion caida no puede dejar sin respuesta a
alguien que esta escribiendo.
"""

from __future__ import annotations

import logging

from app import db
from app.config import obtener_settings
from app.crm import documento, notion
from app.kommo import sincronizacion as kommo

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

    destinos = _destinos()
    if not destinos:
        logger.info("CRM_DESTINO=ninguno: no se sincroniza | conversacion=%s", conversacion_id)
        return False

    previa = await db.valor(
        "SELECT crm_referencia FROM conversaciones WHERE id = $1", conversacion_id
    ) or {}

    referencia: dict = dict(previa)
    fallo = False

    for nombre, sincronizar_en in destinos.items():
        try:
            referencia[nombre] = await sincronizar_en(doc, previa.get(nombre))
        except (notion.NotionNoConfigurado, kommo.KommoNoConfigurado) as e:
            # No es un fallo: ese destino todavia no esta conectado.
            logger.info("destino %s sin configurar | conversacion=%s: %s",
                        nombre, conversacion_id, e)
        except Exception as e:
            fallo = True
            logger.exception("fallo la sincronizacion con %s | conversacion=%s",
                             nombre, conversacion_id)
            await _registrar_error(
                conversacion_id, f"{nombre}: {type(e).__name__}: {e}"[:500]
            )

    escribio = any(k in referencia for k in destinos)
    if fallo or not escribio:
        await _marcar_pendiente(conversacion_id)
        # Sin esto informaba exito cuando ningun destino estaba conectado: la
        # conversacion quedaba marcada para reintento y a la vez dada por
        # sincronizada, que es la peor combinacion posible.
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
        referencia,
    )
    await db.ejecutar(
        "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
        "VALUES ($1, 'crm_sincronizado', 'ok', $2)",
        conversacion_id,
        {"referencia": referencia, "etapa": doc.lead["etapa"], "score": doc.lead["score"]},
    )
    return True


def _destinos() -> dict:
    """Que destinos hay que escribir, segun la configuracion."""
    async def _notion(doc, previa):
        return {"pagina": await notion.sincronizar(doc)}

    async def _kommo(doc, previa):
        return await kommo.sincronizar(doc, previa)

    destino = obtener_settings().crm_destino
    todos = {"notion": _notion, "kommo": _kommo}
    if destino == "ambos":
        return todos
    return {destino: todos[destino]} if destino in todos else {}


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
