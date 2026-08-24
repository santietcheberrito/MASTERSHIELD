"""Adaptador de Telegram: traduce un update a MensajeEntrante.

Telegram es el canal de la etapa de pruebas. La Cloud API de WhatsApp entra
despues implementando `parsear` con la misma firma.
"""

from __future__ import annotations

import hmac
from typing import Any

from app.canales.base import MensajeEntrante

CANAL = "telegram"

# Un update de Telegram trae una sola de estas claves con el contenido. El
# orden importa: `text` y `caption` se leen aparte.
TIPOS_POR_CLAVE = {
    "text": "texto",
    "photo": "imagen",
    "voice": "audio",
    "audio": "audio",
    "video": "video",
    "video_note": "video",
    "document": "documento",
    "location": "ubicacion",
    "contact": "otro",
    "sticker": "otro",
}


def firma_valida(secreto: str, cabecera: str | None) -> bool:
    """Telegram no firma el cuerpo: manda el secreto que uno registro en
    setWebhook, tal cual, en una cabecera. La comparacion va con compare_digest
    igual, para no filtrar informacion por el tiempo que tarda.
    """
    if not secreto:
        # Sin secreto configurado no hay nada que validar. Es aceptable en
        # desarrollo con polling, donde no hay endpoint publico expuesto.
        return True
    return hmac.compare_digest(secreto, cabecera or "")


def _nombre(origen: dict[str, Any]) -> str | None:
    partes = [origen.get("first_name"), origen.get("last_name")]
    nombre = " ".join(p for p in partes if p).strip()
    return nombre or origen.get("username")


def _tipo_y_texto(mensaje: dict[str, Any]) -> tuple[str, str]:
    if "text" in mensaje:
        return "texto", mensaje["text"]
    for clave, tipo in TIPOS_POR_CLAVE.items():
        if clave in mensaje:
            # Una foto con epigrafe trae el texto en `caption`. Es el caso de
            # "le mando la foto de la ventana y le escribo las medidas".
            return tipo, mensaje.get("caption", "") or ""
    return "otro", mensaje.get("caption", "") or ""


def parsear(update: dict[str, Any]) -> MensajeEntrante | None:
    """Devuelve None si el update no es un mensaje entrante que nos interese.

    Telegram manda muchas cosas por el mismo canal: ediciones, callbacks de
    botones, altas y bajas de grupos. Nada de eso es una consulta de un cliente.
    """
    mensaje = update.get("message")
    if not isinstance(mensaje, dict):
        return None

    chat = mensaje.get("chat") or {}
    if chat.get("type") != "private":
        # Solo conversaciones uno a uno. Un grupo no es un lead.
        return None

    chat_id = chat.get("id")
    id_mensaje = mensaje.get("message_id")
    if chat_id is None or id_mensaje is None:
        return None

    if (mensaje.get("from") or {}).get("is_bot"):
        return None

    tipo, texto = _tipo_y_texto(mensaje)

    return MensajeEntrante(
        canal=CANAL,
        identificador=str(chat_id),
        id_externo=f"{CANAL}:{chat_id}:{id_mensaje}",
        texto=texto,
        tipo=tipo,
        nombre=_nombre(mensaje.get("from") or chat),
        telefono=(mensaje.get("contact") or {}).get("phone_number"),
        payload=update,
    )


async def enviar(token: str, chat_id: str, texto: str) -> str | None:
    """Manda un mensaje. Devuelve el id_externo del mensaje enviado.

    Sin delays ni partido de mensajes: eso es de la sesion 6. Aca se manda tal
    cual para poder probar el agente de punta a punta.
    """
    import httpx

    async with httpx.AsyncClient(timeout=20) as cliente:
        respuesta = await cliente.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": texto},
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()

    if not cuerpo.get("ok"):
        raise RuntimeError(f"sendMessage fallo: {cuerpo.get('description')}")

    enviado = cuerpo["result"]
    return f"{CANAL}:{enviado['chat']['id']}:{enviado['message_id']}"


async def indicar_escribiendo(token: str, chat_id: str) -> None:
    """El indicador dura ~5 segundos o hasta que llegue un mensaje."""
    import httpx

    async with httpx.AsyncClient(timeout=10) as cliente:
        await cliente.post(
            f"https://api.telegram.org/bot{token}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
        )
