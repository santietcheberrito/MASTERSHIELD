"""Recepcion de mensajes entrantes.

El webhook solo persiste y agenda. No llama al modelo ni al canal: los
proveedores reintentan si tardamos, y un turno del agente puede llevar varios
segundos. Todo el trabajo pesado es del worker.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request, Response

from app import humanizacion, ingesta
from app.canales import telegram
from app.config import obtener_settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/webhook/telegram")
async def recibir_telegram(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> Response:
    settings = obtener_settings()

    if not telegram.firma_valida(
        settings.telegram_webhook_secret, x_telegram_bot_api_secret_token
    ):
        logger.warning("update de Telegram con secreto invalido")
        raise HTTPException(status_code=403, detail="secreto invalido")

    try:
        update = await request.json()
    except ValueError:
        logger.warning("update de Telegram con cuerpo que no es JSON")
        # 200 igual: un cuerpo ilegible no mejora reintentandolo.
        return Response(status_code=200)

    mensaje = telegram.parsear(update)
    if mensaje is None:
        # Ediciones, callbacks, altas de grupo. Nada que hacer, pero recibido.
        return Response(status_code=200)

    try:
        await ingesta.registrar(mensaje, humanizacion.demora_configurada())
    except Exception:
        logger.exception("no se pudo registrar el update de Telegram")
        # 503 y no 200: si la base no responde, el mensaje todavia no existe en
        # ningun lado y contestar 200 lo perderia para siempre. El reintento del
        # canal es la unica red que queda, y el indice unico sobre id_externo ya
        # garantiza que un reintento no genere una respuesta duplicada.
        return Response(status_code=503)

    return Response(status_code=200)
