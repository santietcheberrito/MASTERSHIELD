"""Adaptador de la Cloud API de WhatsApp (Meta directo, sin BSP).

Diferencias con Telegram que importan:

- Meta **firma el cuerpo** del webhook con HMAC-SHA256 y el app secret. Hay que
  validar sobre los bytes crudos: si se reserializa el JSON, la firma no da.
- El indicador de "escribiendo" viene pegado a marcar el mensaje como leido, en
  una sola llamada, y dura 25 segundos o hasta que se responde.
- El id de mensaje ya es unico globalmente, pero se guarda calificado por canal
  igual, para que no dependa del proveedor.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.canales.base import MensajeEntrante

logger = logging.getLogger(__name__)

CANAL = "whatsapp"
API = "https://graph.facebook.com/v23.0"

TIPOS = {
    "text": "texto", "image": "imagen", "audio": "audio", "voice": "audio",
    "video": "video", "document": "documento", "location": "ubicacion",
    "sticker": "otro", "contacts": "otro", "button": "texto",
    "interactive": "texto",
}


def firma_valida(app_secret: str, cuerpo: bytes, cabecera: str | None) -> bool:
    """Verifica X-Hub-Signature-256 sobre los bytes crudos del pedido.

    Sin app secret configurado no hay nada que validar; se acepta y se avisa al
    arrancar. Con secret, una firma que no coincide se rechaza: es la unica
    forma de saber que el mensaje vino de Meta y no de cualquiera que descubrio
    la URL.
    """
    if not app_secret:
        return True
    if not cabecera or not cabecera.startswith("sha256="):
        return False
    esperada = hmac.new(app_secret.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, cabecera[len("sha256="):])


def verificacion(parametros: dict[str, str], verify_token: str) -> str | None:
    """Responde el desafio del alta del webhook.

    Meta hace un GET con hub.challenge cuando uno registra la URL, y espera que
    se le devuelva ese valor tal cual si el token coincide.
    """
    if (parametros.get("hub.mode") == "subscribe"
            and parametros.get("hub.verify_token") == verify_token):
        return parametros.get("hub.challenge")
    return None


def _texto(mensaje: dict[str, Any]) -> str:
    tipo = mensaje.get("type")
    if tipo == "text":
        return (mensaje.get("text") or {}).get("body", "")
    if tipo == "button":
        return (mensaje.get("button") or {}).get("text", "")
    if tipo == "interactive":
        interactivo = mensaje.get("interactive") or {}
        for clave in ("button_reply", "list_reply"):
            if clave in interactivo:
                return (interactivo[clave] or {}).get("title", "")
        return ""
    # Imagen, video o documento pueden venir con epigrafe.
    contenido = mensaje.get(tipo) or {}
    return contenido.get("caption", "") if isinstance(contenido, dict) else ""


def parsear(payload: dict[str, Any]) -> MensajeEntrante | None:
    """Devuelve None si el evento no es un mensaje entrante que nos interese.

    Meta manda por el mismo webhook los cambios de estado de los mensajes que
    enviamos —entregado, leido—, y eso no es una consulta de nadie.
    """
    try:
        cambio = payload["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return None

    mensajes = cambio.get("messages")
    if not mensajes:
        return None  # estados de entrega, no mensajes

    mensaje = mensajes[0]
    telefono = mensaje.get("from")
    id_mensaje = mensaje.get("id")
    if not telefono or not id_mensaje:
        return None

    perfiles = {c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in cambio.get("contacts") or []}

    # En WhatsApp el telefono ES el identificador de la conversacion, asi que
    # el handoff telefonico no depende de que la persona lo comparta.
    return MensajeEntrante(
        canal=CANAL,
        identificador=f"+{telefono}",
        id_externo=f"{CANAL}:{id_mensaje}",
        texto=_texto(mensaje),
        tipo=TIPOS.get(mensaje.get("type"), "otro"),
        nombre=perfiles.get(telefono),
        telefono=f"+{telefono}",
        payload=payload,
    )


class WhatsAppError(RuntimeError):
    """Meta rechazo la llamada. El mensaje incluye lo que dijo."""


async def _llamar(token: str, phone_number_id: str, cuerpo: dict) -> dict:
    async with httpx.AsyncClient(timeout=20) as cliente:
        respuesta = await cliente.post(
            f"{API}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"messaging_product": "whatsapp", **cuerpo},
        )

    if respuesta.status_code >= 400:
        # Meta explica en el cuerpo QUE esta mal, con un codigo propio, y
        # `raise_for_status` tira todo eso a la basura: queda un "400 Bad
        # Request" que obliga a reproducir la llamada a mano para entender.
        # Ya paso una vez.
        try:
            error = respuesta.json().get("error", {})
            detalle = (f"({error.get('code')}) {error.get('message')} "
                       f"{(error.get('error_data') or {}).get('details', '')}").strip()
        except ValueError:
            detalle = respuesta.text[:300]
        logger.error("WhatsApp rechazo la llamada | %s: %s",
                     respuesta.status_code, detalle)
        raise WhatsAppError(f"{respuesta.status_code}: {detalle}")

    return respuesta.json()


def destino_de_envio(numero: str) -> str:
    """Corrige la rareza argentina del 9.

    En Argentina, WhatsApp identifica a la persona como 549 11 XXXXXXXX pero la
    API solo acepta enviarle a 54 11 XXXXXXXX, sin el 9. Verificado contra la
    cuenta: al primero devuelve 131030 y al segundo entrega.

    No afecta a los clientes de MasterShield —Ecuador no tiene esta rareza— pero
    si a cualquiera que pruebe desde Argentina.
    """
    limpio = numero.lstrip("+")
    if limpio.startswith("549") and len(limpio) == 13:
        return "54" + limpio[3:]
    return limpio


async def enviar(token: str, phone_number_id: str, destino: str, texto: str) -> str | None:
    cuerpo = await _llamar(token, phone_number_id, {
        "recipient_type": "individual",
        "to": destino_de_envio(destino),
        "type": "text",
        "text": {"preview_url": False, "body": texto},
    })
    enviados = cuerpo.get("messages") or []
    return f"{CANAL}:{enviados[0]['id']}" if enviados else None


async def indicar_escribiendo(token: str, phone_number_id: str, id_mensaje: str) -> None:
    """Marca como leido y muestra el indicador, que en Meta es la misma llamada.

    Dura 25 segundos o hasta que se responda, asi que no hace falta renovarlo
    como en Telegram. Meta pide no mostrarlo si uno no va a responder.
    """
    await _llamar(token, phone_number_id, {
        "status": "read",
        "message_id": id_mensaje.removeprefix(f"{CANAL}:"),
        "typing_indicator": {"type": "text"},
    })
