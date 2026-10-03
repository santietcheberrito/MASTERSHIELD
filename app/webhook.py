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

from app import bandeja, db, humanizacion, ingesta
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


async def _atender_eco(eco) -> None:
    """Decide si el eco lo mandamos nosotros o una persona, y actua.

    Nunca propaga: un eco que no se puede procesar no puede hacer que Meta
    reintente el webhook, porque el mensaje del cliente ya se guardo o se va a
    guardar por otra via. Lo peor que pasa es que la pausa no se active y el
    agente hable encima del asesor, y eso queda en el log.
    """
    try:
        # `id_externo` es unico: si ya esta, es de un mensaje que enviamos
        # nosotros y lo guardamos al enviarlo.
        nuestro = await db.valor(
            "SELECT 1 FROM mensajes WHERE id_externo = $1", eco.id_externo
        )
        if nuestro:
            return

        id_conversacion = await ingesta.registrar_intervencion_humana(
            eco.canal, eco.identificador, eco.texto, eco.id_externo
        )
        if id_conversacion is None:
            logger.info("eco de una conversacion que no tenemos | %s", eco.identificador)
    except Exception:
        logger.exception("no se pudo procesar el eco de WhatsApp")


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

    # Un mensaje que salio de nuestro numero. Si el id no es de algo que
    # mandamos nosotros, lo escribio una persona del equipo desde otra
    # herramienta, y el agente tiene que dejar de contestar esa conversacion.
    eco = whatsapp.parsear_eco(payload)
    if eco is not None:
        await _atender_eco(eco)
        return Response(status_code=200)

    mensaje = whatsapp.parsear(payload)
    if mensaje is None:
        # Estados de entrega y lectura de lo que mandamos nosotros. El unico que
        # importa es el fallo: Meta lo avisa solo por aca, y sin log un mensaje
        # que nunca llego parece uno entregado.
        for wamid, motivo in whatsapp.entregas_fallidas(payload):
            logger.error("Meta no pudo entregar un mensaje | %s | %s", wamid, motivo)
        return Response(status_code=200)

    try:
        await ingesta.registrar(mensaje, humanizacion.demora_configurada())
    except Exception:
        logger.exception("no se pudo registrar el mensaje de WhatsApp")
        # 503 para que Meta reintente: el mensaje todavia no existe en ningun
        # lado y el indice unico sobre id_externo evita la respuesta duplicada.
        return Response(status_code=503)

    return Response(status_code=200)


@router.post("/webhook/chatwoot")
async def recibir_chatwoot(
    request: Request,
    x_chatwoot_signature: str | None = Header(default=None),
    x_chatwoot_timestamp: str | None = Header(default=None),
) -> Response:
    """Un asesor contesto desde la bandeja: sale por WhatsApp y el agente se calla.

    Chatwoot avisa de **todos** los mensajes salientes, incluidos los que
    espejamos nosotros. `respuesta_humana` descarta esos —van marcados— y las
    notas privadas; sin eso, cada respuesta del agente volveria a salir una y
    otra vez.
    """
    crudo = await request.body()
    if not bandeja.firma_valida(crudo, x_chatwoot_signature, x_chatwoot_timestamp):
        logger.warning("webhook de la bandeja con firma invalida")
        raise HTTPException(status_code=403, detail="firma invalida")

    try:
        payload = json.loads(crudo)
    except ValueError:
        logger.warning("webhook de la bandeja con cuerpo que no es JSON")
        return Response(status_code=200)

    # Resolver no manda ningun mensaje: es la via para que alguien se haga cargo
    # sin escribir todavia.
    resuelta = bandeja.conversacion_resuelta(payload)
    if resuelta is not None:
        conversacion = await bandeja.conversacion_de(resuelta)
        if conversacion is not None:
            await ingesta.registrar_conversacion_tomada(
                conversacion["id"], "resuelta en la bandeja")
        return Response(status_code=200)

    respuesta = bandeja.respuesta_humana(payload)
    if respuesta is None:
        return Response(status_code=200)

    conversacion = await bandeja.conversacion_de(respuesta["conversacion"])
    if conversacion is None:
        logger.info("respuesta en la bandeja sobre una conversacion que no es nuestra | %s",
                    respuesta["conversacion"])
        return Response(status_code=200)

    settings = obtener_settings()
    if conversacion["canal"] != "whatsapp":
        logger.error("la bandeja solo sabe contestar por WhatsApp, no por %s",
                     conversacion["canal"])
        return Response(status_code=200)

    try:
        await whatsapp.enviar(
            settings.whatsapp_token, settings.whatsapp_phone_number_id,
            conversacion["identificador"], respuesta["texto"],
        )
    except Exception:
        logger.exception("no se pudo mandar por WhatsApp lo que escribio %s",
                         respuesta["autor"])
        # 500 para que Chatwoot reintente: el asesor cree que ya contesto.
        return Response(status_code=500)

    await ingesta.registrar_intervencion_humana(
        conversacion["canal"], conversacion["identificador"],
        respuesta["texto"], respuesta["id_externo"],
    )
    return Response(status_code=200)
