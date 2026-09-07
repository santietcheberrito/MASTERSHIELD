"""Servicio FastAPI: rutas y ciclo de vida del proceso.

El webhook, el worker y las rutas de operacion viven todos en este proceso.
Es a proposito: el sistema atiende decenas de conversaciones por dia y separar
el worker en otro servicio solo agregaria una pieza mas que puede fallar.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import db, metricas, registro, webhook
from app.config import Settings, obtener_settings
from app.poller import Poller
from app.crm.reintentos import Reintentos
from app.pausas import Despertador
from app.worker import Worker

logger = logging.getLogger(__name__)


def configurar_logging(nivel: str, formato: str = "texto") -> None:
    registro.configurar(nivel, formato)


def avisar_credenciales(settings: Settings) -> None:
    """Las credenciales del cliente llegan de a poco; que se sepa al arrancar.

    El servicio levanta igual: sin ANTHROPIC_API_KEY no hay agente, pero el
    webhook puede recibir y guardar mensajes, que es mejor que perderlos.
    """
    faltantes = settings.credenciales_faltantes
    if faltantes:
        logger.warning("credenciales sin configurar: %s", ", ".join(faltantes))

    # Sin secreto, el endpoint acepta cualquier POST: cualquiera puede fabricar
    # conversaciones y hacernos gastar llamadas al modelo. En polling no hay
    # endpoint expuesto, asi que solo importa en modo webhook.
    if settings.telegram_modo == "webhook" and not settings.telegram_webhook_secret:
        logger.error(
            "TELEGRAM_MODO=webhook sin TELEGRAM_WEBHOOK_SECRET: el endpoint queda abierto"
        )


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    settings = obtener_settings()
    configurar_logging(settings.log_level, settings.log_formato)
    avisar_credenciales(settings)

    base_lista = False
    try:
        await db.iniciar(settings.database_url)
        base_lista = True
        logger.info("pool de base iniciado")
    except Exception:
        # No abortamos el arranque: si la base esta caida queremos que el
        # proceso levante y que /health lo diga con un 503, para poder
        # distinguir "el deploy no arranco" de "la base no responde".
        logger.exception("no se pudo iniciar el pool de base")

    worker = Worker()
    reintentos = Reintentos()
    despertador = Despertador()
    poller: Poller | None = None

    if base_lista:
        worker.arrancar()
        reintentos.arrancar()
        despertador.arrancar()

        # El poller solo tiene sentido en desarrollo: en produccion Telegram
        # nos pega al webhook, que es ademas lo unico que soporta WhatsApp.
        if settings.telegram_modo == "polling" and settings.telegram_bot_token:
            poller = Poller(settings.telegram_bot_token)
            poller.arrancar()
        elif settings.telegram_modo == "polling":
            logger.warning("TELEGRAM_MODO=polling pero no hay TELEGRAM_BOT_TOKEN")
    else:
        logger.error(
            "sin base: no arrancan el worker ni los reintentos del CRM"
        )

    app.state.worker = worker
    app.state.reintentos = reintentos
    app.state.despertador = despertador
    app.state.poller = poller

    try:
        yield
    finally:
        if poller is not None:
            await poller.detener()
        await despertador.detener()
        await reintentos.detener()
        await worker.detener()
        await db.cerrar()
        logger.info("pool de base cerrado")


app = FastAPI(title="Agente de calificacion WhatsApp -> Kommo", lifespan=ciclo_de_vida)
app.include_router(webhook.router)


@app.get("/health")
async def health() -> JSONResponse:
    """Si el proceso esta vivo. Es lo que la plataforma usa para reiniciarlo.

    Devuelve 200 aunque la base no responda, y el cuerpo lo dice. La razon es
    que un health check que falla hace que Render reinicie el contenedor a los
    60 segundos, y reiniciar no arregla que Supabase este caido: solo corta los
    turnos en vuelo y entra en un ciclo de reinicios. Durante un deploy es peor
    todavia —cancela el deploy entero— por una falla que no es del deploy.

    Que la base este caida se ve en `/metricas`, que es el lugar donde se mira
    el estado del sistema, y en el cuerpo de aca.
    """
    base_viva = await db.esta_viva()
    return JSONResponse({
        "estado": "ok" if base_viva else "degradado",
        "base": "ok" if base_viva else "sin conexion",
    })


@app.get("/metricas")
async def ver_metricas() -> JSONResponse:
    """El estado del sistema ahora. Devuelve 200 siempre, incluso con alertas.

    Un 503 aca haria que el healthcheck de Railway reiniciara el proceso, y un
    lead que no llego al CRM no se arregla reiniciando: se arregla mirandolo.
    """
    settings = obtener_settings()
    return JSONResponse(await metricas.reunir(zona=str(settings.zona)))
