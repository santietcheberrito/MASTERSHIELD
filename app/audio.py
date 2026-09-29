"""Las notas de voz del cliente, pasadas a texto.

Por WhatsApp mucha gente prefiere hablar antes que escribir, y mas cuando esta
midiendo una ventana con el telefono en la mano. Hasta el 15/9/2026 esas notas
llegaban como "[el cliente envio un archivo de tipo audio]": el agente sabia que
habia llegado algo pero no que decia, y contestaba pidiendo que lo escribieran.

Se transcriben al empezar el turno, no al recibir el mensaje: el webhook tiene
medio segundo de presupuesto y bajar un archivo no entra. Meta guarda lo que
llega por webhook siete dias, asi que a los segundos sigue estando.

Si algo falla —la nota es larguisima, OpenAI no contesta— el turno sigue sin la
transcripcion y el agente ve el mismo aviso de antes. Una nota que no se pudo
leer no puede dejar a la persona sin respuesta.
"""

from __future__ import annotations

import logging

from app.canales import whatsapp
from app.config import obtener_settings

logger = logging.getLogger(__name__)

# El modelo de transcripcion de OpenAI. Es la misma cuenta que ya usan el agente
# y los embeddings.
MODELO = "gpt-4o-mini-transcribe"

# El idioma sale dado: el cliente es ecuatoriano. Sin esto, un audio corto o con
# ruido a veces se transcribe como si fuera otro idioma.
IDIOMA = "es"

# WhatsApp no deja mandar audios de mas de 16 MB. Mas que eso no es una nota de
# voz nuestra: no se baja.
MAXIMO_BYTES = 16 * 1024 * 1024

_cliente = None


def _openai():
    global _cliente
    if _cliente is None:
        from openai import AsyncOpenAI

        _cliente = AsyncOpenAI(api_key=obtener_settings().openai_api_key)
    return _cliente


async def transcribir(contenido: bytes, mime: str) -> str:
    """El texto de un audio. Cadena vacia si no se pudo."""
    if not contenido or len(contenido) > MAXIMO_BYTES:
        logger.warning("audio de %s bytes: no se transcribe", len(contenido or b""))
        return ""
    # La API quiere un archivo con nombre: la extension es lo que le dice que
    # formato es (WhatsApp manda ogg/opus).
    extension = (mime or "").split("/")[-1].split(";")[0] or "ogg"
    try:
        r = await _openai().audio.transcriptions.create(
            model=MODELO, file=(f"nota.{extension}", contenido, mime or "audio/ogg"),
            language=IDIOMA,
        )
    except Exception:
        logger.exception("no se pudo transcribir la nota de voz")
        return ""
    return " ".join((getattr(r, "text", "") or "").split())


async def texto_de_la_nota(payload: dict) -> str:
    """Baja la nota de voz de un mensaje de WhatsApp y la devuelve en texto.

    Cadena vacia si el mensaje no traia audio, si no hay token o si algo fallo.
    """
    settings = obtener_settings()
    if not settings.whatsapp_token:
        return ""

    audios = whatsapp.medias_del_payload(payload or {}, tipos=("audio",))
    if not audios:
        return ""

    nota = audios[0]
    try:
        contenido, mime = await whatsapp.descargar_media(settings.whatsapp_token, nota["id"])
    except Exception:
        logger.exception("no se pudo bajar la nota de voz | media=%s", nota["id"])
        return ""

    texto = await transcribir(contenido, mime or nota.get("mime", ""))
    if texto:
        logger.info("nota de voz transcrita | media=%s | %s", nota["id"], texto)
    return texto
