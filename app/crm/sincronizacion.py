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

# Cuanto se espera antes de cada reintento, por numero de intento. Arranca
# corto porque la mayoria de las caidas de un CRM duran segundos, y se abre
# rapido para no golpear una hora entera a algo que esta roto de verdad.
ESPERAS_REINTENTO = (60, 300, 900, 3600, 21600)  # 1 min, 5, 15, 1 h, 6 h

# Despues de esto se deja de reintentar. La fila queda pendiente con
# `crm_reintentar_en` en NULL: no la toma nadie, pero sigue contada como
# pendiente, que es lo que tiene que ver una metrica. Borrarla seria decir que
# se sincronizo.
MAX_REINTENTOS = len(ESPERAS_REINTENTO)


def espera_del_intento(intentos: int) -> int:
    """Segundos hasta el proximo reintento. `intentos` es el que acaba de fallar."""
    indice = min(max(intentos, 1), len(ESPERAS_REINTENTO)) - 1
    return ESPERAS_REINTENTO[indice]


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

    # El puntaje es nuestro, no del CRM: se guarda antes de salir a la red, asi
    # queda registrado aunque Kommo este caido. Las columnas existian desde la
    # primera migracion y hasta ahora ninguna se escribia: el tablero mostraba
    # el score y la base lo tenia en NULL.
    await db.ejecutar(
        """
        UPDATE conversaciones SET
            score         = $2,
            clasificacion = $3,
            detalle_score = $4::jsonb
        WHERE id = $1
        """,
        conversacion_id,
        doc.puntaje.score,
        doc.puntaje.clasificacion,
        {"etapa": doc.puntaje.etapa, "motivo": doc.puntaje.motivo,
         "desglose": doc.puntaje.desglose},
    )

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
    """Agenda el proximo reintento, o lo da por agotado.

    Agotado no es lo mismo que resuelto: la fila queda `crm_pendiente` con
    `crm_reintentar_en` en NULL, asi el loop no la vuelve a tomar pero sigue
    contando como pendiente. Que una metrica la muestre es el unico modo de
    enterarse de que un lead no llego al CRM.
    """
    intentos = await db.valor(
        "UPDATE conversaciones SET crm_pendiente = true, "
        "crm_intentos = crm_intentos + 1 WHERE id = $1 RETURNING crm_intentos",
        conversacion_id,
    )

    if intentos is not None and intentos >= MAX_REINTENTOS:
        await db.ejecutar(
            "UPDATE conversaciones SET crm_reintentar_en = NULL WHERE id = $1",
            conversacion_id,
        )
        await db.ejecutar(
            "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
            "VALUES ($1, 'crm_agotado', 'error', $2)",
            conversacion_id,
            {"intentos": intentos},
        )
        logger.error(
            "el CRM no acepto la conversacion tras %s intentos, se deja de "
            "reintentar | conversacion=%s", intentos, conversacion_id,
        )
        return

    await db.ejecutar(
        "UPDATE conversaciones SET crm_reintentar_en = now() + "
        "make_interval(secs => $2) WHERE id = $1",
        conversacion_id,
        espera_del_intento(intentos or 1),
    )


async def _registrar_error(conversacion_id: int, detalle: str) -> None:
    await db.ejecutar(
        "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
        "VALUES ($1, 'crm_sincronizado', 'error', $2)",
        conversacion_id,
        {"detalle": detalle},
    )
