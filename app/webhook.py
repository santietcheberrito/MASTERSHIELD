"""Recepcion de mensajes entrantes.

El webhook solo persiste y agenda. No llama al modelo ni al canal: los
proveedores reintentan si tardamos, y un turno del agente puede llevar varios
segundos. Todo el trabajo pesado es del worker.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from app import humanizacion, ingesta
from app.canales import telegram, whatsapp
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


@router.get("/webhook/whatsapp")
async def verificar_whatsapp(request: Request) -> Response:
    """Meta hace un GET con un desafio al dar de alta la URL del webhook."""
    settings = obtener_settings()
    desafio = whatsapp.verificacion(dict(request.query_params), settings.whatsapp_verify_token)
    if desafio is None:
        logger.warning("verificacion de webhook de WhatsApp rechazada")
        raise HTTPException(status_code=403, detail="token de verificacion invalido")
    return PlainTextResponse(desafio)


@router.post("/webhook/whatsapp")
async def recibir_whatsapp(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
) -> Response:
    # Sobre los bytes crudos: si se reserializa el JSON, la firma no da.
    crudo = await request.body()
    settings = obtener_settings()

    if not whatsapp.firma_valida(settings.whatsapp_app_secret, crudo, x_hub_signature_256):
        logger.warning("mensaje de WhatsApp con firma invalida")
        raise HTTPException(status_code=403, detail="firma invalida")

    try:
        payload = json.loads(crudo)
    except ValueError:
        logger.warning("webhook de WhatsApp con cuerpo que no es JSON")
        return Response(status_code=200)

    mensaje = whatsapp.parsear(payload)
    if mensaje is None:
        # Estados de entrega y lectura de lo que mandamos nosotros.
        return Response(status_code=200)

    try:
        await ingesta.registrar(mensaje, humanizacion.demora_configurada())
    except Exception:
        logger.exception("no se pudo registrar el mensaje de WhatsApp")
        # 503 para que Meta reintente: el mensaje todavia no existe en ningun
        # lado y el indice unico sobre id_externo evita la respuesta duplicada.
        return Response(status_code=503)

    return Response(status_code=200)
