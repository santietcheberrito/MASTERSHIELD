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

from app import db
from app.config import Settings, obtener_settings

logger = logging.getLogger(__name__)


def configurar_logging(nivel: str) -> None:
    logging.basicConfig(
        level=getattr(logging, nivel),
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )


def avisar_credenciales(settings: Settings) -> None:
    """Las credenciales del cliente llegan de a poco; que se sepa al arrancar.

    El servicio levanta igual: sin ANTHROPIC_API_KEY no hay agente, pero el
    webhook puede recibir y guardar mensajes, que es mejor que perderlos.
    """
    faltantes = settings.credenciales_faltantes
    if faltantes:
        logger.warning("credenciales sin configurar: %s", ", ".join(faltantes))


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    settings = obtener_settings()
    configurar_logging(settings.log_level)
    avisar_credenciales(settings)

    try:
        await db.iniciar(settings.database_url)
        logger.info("pool de base iniciado")
    except Exception:
        # No abortamos el arranque: si la base esta caida queremos que el
        # proceso levante y que /health lo diga con un 503, para poder
        # distinguir "el deploy no arranco" de "la base no responde".
        logger.exception("no se pudo iniciar el pool de base")

    try:
        yield
    finally:
        await db.cerrar()
        logger.info("pool de base cerrado")


app = FastAPI(title="Agente de calificacion WhatsApp -> Kommo", lifespan=ciclo_de_vida)


@app.get("/health")
async def health() -> JSONResponse:
    base_viva = await db.esta_viva()
    cuerpo = {
        "estado": "ok" if base_viva else "degradado",
        "base": "ok" if base_viva else "sin conexion",
    }
    return JSONResponse(cuerpo, status_code=200 if base_viva else 503)
