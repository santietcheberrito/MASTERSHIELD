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
import mimetypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


# Eventos que Meta manda con forma de mensaje y no lo son: nadie escribio nada.
# Una reaccion —el 👍 sobre un mensaje nuestro— llegaba como tipo "otro" con
# contenido vacio, armaba un turno igual, y el agente le contesto a Pablo
# "recibimos el archivo" un minuto despues de haberse despedido (23/9/2026).
# Un sticker suelto es lo mismo: una expresion, no una consulta. Si vienen en
# una rafaga junto con texto, el turno se arma igual por los otros mensajes.
SIN_RESPUESTA = frozenset({"reaction", "sticker", "system"})


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
    if mensaje.get("type") in SIN_RESPUESTA:
        return None  # una reaccion o un sticker no piden respuesta

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


# Un eco no siempre es un mensaje: tambien avisa que alguien borro o edito uno
# que ya habia mandado. Para nosotros los tres dicen lo mismo —hay una persona
# operando esta conversacion— y el texto es solo para el historial.
_ECOS_SIN_TEXTO = {
    "revoke": "[el asesor borro un mensaje]",
    "edit": "[el asesor edito un mensaje]",
}


def parsear_eco(payload: dict[str, Any]) -> MensajeEntrante | None:
    """Un mensaje que salio de nuestro numero y que no mandamos nosotros.

    Meta lo manda en el campo `smb_message_echoes`, que hay que suscribir aparte
    de `messages`, y dentro del payload el array se llama `message_echoes`. Es
    la unica forma de enterarse de que alguien del equipo contesto desde la app
    de WhatsApp Business sobre el mismo numero. Sin esto el agente sigue
    escribiendo encima del asesor.

    Ojo con el alcance: esto cubre la app de WhatsApp Business, no una
    herramienta que mande por la Cloud API. Un vendedor que conteste desde
    Kommo no genera eco, y esa deteccion tiene que venir de Kommo.

    Devuelve None si el evento no es un eco. Quien decide si el eco es nuestro
    o de una persona es el webhook, comparando el id contra lo que enviamos.
    """
    try:
        cambio = payload["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return None

    ecos = cambio.get("message_echoes")
    if not ecos:
        return None

    eco = ecos[0]
    if eco.get("type") in SIN_RESPUESTA:
        return None  # un emoji del asesor no es intervenir en la conversacion

    destinatario = eco.get("to")
    id_mensaje = eco.get("id")
    if not destinatario or not id_mensaje:
        return None

    # El identificador de la conversacion es el cliente, no nuestro numero: un
    # eco va hacia afuera, asi que el interlocutor esta en `to`.
    tipo_meta = eco.get("type")
    return MensajeEntrante(
        canal=CANAL,
        identificador=f"+{destinatario.lstrip('+')}",
        id_externo=f"{CANAL}:{id_mensaje}",
        texto=_ECOS_SIN_TEXTO.get(tipo_meta) or _texto(eco),
        tipo=TIPOS.get(tipo_meta, "otro"),
        nombre=None,
        telefono=None,
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


# Lo que se sube al lead de Kommo. El audio y el video quedan afuera a
# proposito: nadie los va a mirar para tomar medidas, y subirlos al lead solo
# llena el drive del cliente. La nota de voz igual se baja —se transcribe y el
# asesor la lee en la conversacion (app/audio.py)—, pero no se sube.
TIPOS_CON_ARCHIVO = ("image", "document")


def medias_del_payload(
    payload: dict[str, Any], tipos: tuple[str, ...] = TIPOS_CON_ARCHIVO
) -> list[dict[str, str]]:
    """Los archivos que trae un mensaje entrante, si trae alguno.

    Devuelve el id con el que se descargan de Meta y lo que se sepa del
    archivo. El id vive dentro del objeto del tipo —`image`, `document`— y no
    en la raiz del mensaje.
    """
    try:
        mensajes = payload["entry"][0]["changes"][0]["value"]["messages"]
    except (KeyError, IndexError, TypeError):
        return []

    encontrados = []
    for mensaje in mensajes or []:
        tipo = mensaje.get("type")
        if tipo not in tipos:
            continue
        objeto = mensaje.get(tipo) or {}
        media_id = objeto.get("id")
        if not media_id:
            continue
        encontrados.append({
            "id": media_id,
            "tipo": tipo,
            "mime": objeto.get("mime_type", ""),
            # El nombre solo viene en documentos; una foto no tiene.
            "nombre": objeto.get("filename", ""),
        })
    return encontrados


async def descargar_media(token: str, media_id: str) -> tuple[bytes, str]:
    """Baja un archivo de Meta. Devuelve (contenido, mime).

    Son dos llamadas porque la primera devuelve una URL firmada que dura cinco
    minutos, y la descarga tambien necesita el token: sin el, Meta contesta 404
    en vez de decir que falta autenticacion.

    Un archivo recibido por webhook vive siete dias en Meta. Pasado eso el id
    ya no baja nada, asi que esto se hace cuando el archivo todavia esta.
    """
    cabeceras = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cliente:
        r = await cliente.get(f"{API}/{media_id}", headers=cabeceras)
        if r.status_code >= 400:
            raise WhatsAppError(f"no se pudo resolver el media {media_id}: "
                                f"{r.status_code} {r.text[:200]}")
        datos = r.json()
        url = datos.get("url")
        if not url:
            raise WhatsAppError(f"el media {media_id} no trajo url")

        descarga = await cliente.get(url, headers=cabeceras)
        if descarga.status_code >= 400:
            raise WhatsAppError(f"no se pudo bajar el media {media_id}: "
                                f"{descarga.status_code}")

    return descarga.content, datos.get("mime_type", "application/octet-stream")


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


# Un archivo subido a Meta se puede reusar durante 30 dias. Se vuelve a subir
# antes, para no descubrir que vencio en medio de una conversacion.
DIAS_DE_VIDA_DE_UN_ARCHIVO_SUBIDO = 25

# Lo ya subido en este proceso: (ruta, mtime, tamaño) -> (media_id, cuando).
# Con el mtime en la clave, reemplazar el archivo lo sube de nuevo solo. En
# memoria alcanza: un reinicio cuesta una subida de unos megas, no un problema.
_subidos: dict[tuple[str, int, int], tuple[str, datetime]] = {}


async def subir_media(token: str, phone_number_id: str, contenido: bytes,
                      mime: str, nombre: str) -> str:
    """Sube un archivo a Meta y devuelve el id con el que se manda."""
    async with httpx.AsyncClient(timeout=120) as cliente:
        r = await cliente.post(
            f"{API}/{phone_number_id}/media",
            headers={"Authorization": f"Bearer {token}"},
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (nombre, contenido, mime)},
        )
    if r.status_code >= 400:
        raise WhatsAppError(f"no se pudo subir {nombre}: {r.status_code} {r.text[:300]}")
    return r.json()["id"]


async def _id_del_archivo(token: str, phone_number_id: str, ruta: Path,
                          forzar: bool = False) -> str:
    estado = ruta.stat()
    clave = (str(ruta), estado.st_mtime_ns, estado.st_size)
    ahora = datetime.now(timezone.utc)
    guardado = _subidos.get(clave)
    if (guardado and not forzar
            and ahora - guardado[1] < timedelta(days=DIAS_DE_VIDA_DE_UN_ARCHIVO_SUBIDO)):
        return guardado[0]

    mime = mimetypes.guess_type(ruta.name)[0] or "video/mp4"
    media_id = await subir_media(token, phone_number_id, ruta.read_bytes(), mime, ruta.name)
    _subidos[clave] = (media_id, ahora)
    logger.info("archivo subido a Meta | %s -> %s", ruta.name, media_id)
    return media_id


async def enviar_video(token: str, phone_number_id: str, destino: str,
                       ruta: Path, leyenda: str = "") -> str | None:
    """Manda un video del disco. Lo sube la primera vez y despues reusa el id.

    La leyenda va dentro del mismo mensaje, debajo del video: mandada aparte
    podria llegar antes o despues y quedar suelta.

    Si Meta rechaza el envio se sube de nuevo y se reintenta una vez: el id
    guardado pudo haber vencido o haberse borrado del lado de Meta, y eso no se
    ve hasta que se usa.
    """
    def _cuerpo(media_id: str) -> dict:
        video = {"id": media_id}
        if leyenda:
            video["caption"] = leyenda
        return {
            "recipient_type": "individual",
            "to": destino_de_envio(destino),
            "type": "video",
            "video": video,
        }

    media_id = await _id_del_archivo(token, phone_number_id, ruta)
    try:
        cuerpo = await _llamar(token, phone_number_id, _cuerpo(media_id))
    except WhatsAppError:
        logger.warning("Meta rechazo el video con el id guardado; se sube de nuevo")
        media_id = await _id_del_archivo(token, phone_number_id, ruta, forzar=True)
        cuerpo = await _llamar(token, phone_number_id, _cuerpo(media_id))

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
