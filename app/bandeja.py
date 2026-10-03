"""La bandeja humana: Chatwoot.

El agente le habla a Meta directo y le **espeja** a Chatwoot lo que pasa, en
los dos sentidos. No es un canal: nadie manda nada por aca. Es la pantalla
donde el equipo ve las conversaciones y puede meterse si hace falta.

Se eligio asi, y no conectando el numero a Chatwoot, por una razon concreta:
el envio queda de nuestro lado. Las notas de voz necesitan `voice: true` y un
Ogg sin metadatos, y eso solo se garantiza hablandole a Meta nosotros. Una
herramienta que mande por su cuenta manda como ella sabe.

Cuando un vendedor contesta desde Chatwoot, Chatwoot nos llama al webhook, el
mensaje sale por WhatsApp y el agente se calla. Lo ultimo ya existia: es el
mismo mecanismo que ya cubria a alguien contestando desde la app.

Si Chatwoot no responde, no pasa nada: la conversacion sigue. Perder el espejo
es perder visibilidad, no perder al cliente.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from app import db
from app.config import obtener_settings

logger = logging.getLogger(__name__)

TIEMPO_LIMITE = 10.0

# Lo que el agente espeja lleva esta marca. Sin ella entrariamos en un bucle:
# Chatwoot avisa de cada mensaje saliente, incluidos los que escribimos
# nosotros, y los volveriamos a mandar por WhatsApp una y otra vez.
MARCA = "agente_mastershield"


class BandejaError(RuntimeError):
    pass


def configurada() -> bool:
    s = obtener_settings()
    return bool(s.chatwoot_url and s.chatwoot_token and s.chatwoot_cuenta
                and s.chatwoot_bandeja)


async def _pedir(metodo: str, ruta: str, cuerpo: dict | None = None) -> Any:
    s = obtener_settings()
    async with httpx.AsyncClient(timeout=TIEMPO_LIMITE) as cliente:
        r = await cliente.request(
            metodo,
            f"{s.chatwoot_url.rstrip('/')}/api/v1/accounts/{s.chatwoot_cuenta}{ruta}",
            headers={"api_access_token": s.chatwoot_token},
            json=cuerpo,
        )
    if r.status_code >= 400:
        raise BandejaError(f"{metodo} {ruta} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.content.strip() else None


async def _referencia(conversacion_id: int) -> dict | None:
    return await db.valor(
        "SELECT bandeja_referencia FROM conversaciones WHERE id = $1", conversacion_id
    )


async def _buscar_contacto(telefono: str) -> int | None:
    """El contacto de ese telefono, si la bandeja ya lo tiene."""
    from urllib.parse import quote
    r = await _pedir("GET", f"/contacts/search?q={quote(telefono)}")
    for c in ((r or {}).get("payload") or []):
        if (c.get("phone_number") or "") == telefono:
            return c.get("id")
    return None


async def _crear(conversacion_id: int, telefono: str, nombre: str | None) -> dict:
    """Crea el contacto y la conversacion del lado de la bandeja.

    El contacto puede existir de antes: un cliente que vuelve a escribir meses
    despues es el mismo telefono. Crearlo de nuevo da 422 y antes eso dejaba la
    conversacion sin espejar, en silencio.
    """
    s = obtener_settings()
    id_contacto = await _buscar_contacto(telefono)
    if id_contacto is None:
        contacto = await _pedir("POST", "/contacts", {
            "inbox_id": s.chatwoot_bandeja,
            "name": nombre or telefono,
            "phone_number": telefono,
        })
        id_contacto = ((contacto or {}).get("payload") or {}).get("contact", {}).get("id")
    if not id_contacto:
        raise BandejaError(f"la bandeja no devolvio un contacto para {telefono}")

    conversacion = await _pedir("POST", "/conversations", {
        "inbox_id": s.chatwoot_bandeja,
        "contact_id": id_contacto,
        "source_id": telefono,
    })
    id_conversacion = (conversacion or {}).get("id")
    if not id_conversacion:
        raise BandejaError(f"la bandeja no devolvio una conversacion: {str(conversacion)[:200]}")

    referencia = {"contacto": id_contacto, "conversacion": id_conversacion}
    await db.ejecutar(
        "UPDATE conversaciones SET bandeja_referencia = $2 WHERE id = $1",
        conversacion_id, referencia,
    )
    logger.info("conversacion espejada en la bandeja | conversacion=%s bandeja=%s",
                conversacion_id, id_conversacion)
    return referencia


async def _asegurar(conversacion_id: int, telefono: str, nombre: str | None) -> dict:
    return await _referencia(conversacion_id) or await _crear(
        conversacion_id, telefono, nombre)


async def espejar(conversacion_id: int, telefono: str, texto: str, *,
                  del_cliente: bool, nombre: str | None = None,
                  id_externo: str | None = None) -> int | None:
    """Pone un mensaje en la bandeja. Devuelve su id, o None si no se pudo.

    Nunca levanta: un fallo de la bandeja no puede cortarle la respuesta a un
    cliente. Queda en el log y la conversacion sigue.
    """
    if not configurada() or not texto.strip():
        return None
    try:
        referencia = await _asegurar(conversacion_id, telefono, nombre)
        cuerpo: dict[str, Any] = {
            "content": texto,
            "message_type": "incoming" if del_cliente else "outgoing",
        }
        if not del_cliente:
            # Para no volver a mandarlo cuando Chatwoot nos avise de el.
            cuerpo["content_attributes"] = {MARCA: True}
        if id_externo:
            cuerpo["source_id"] = id_externo
        mensaje = await _pedir(
            "POST", f"/conversations/{referencia['conversacion']}/messages", cuerpo)
        return (mensaje or {}).get("id")
    except Exception:
        logger.exception("no se pudo espejar en la bandeja | conversacion=%s",
                         conversacion_id)
        return None


def firma_valida(cuerpo: bytes, firma: str | None, marca_de_tiempo: str | None) -> bool:
    """Si el webhook lo mando Chatwoot y no cualquiera que descubrio la URL.

    Chatwoot firma `marca_de_tiempo + "." + cuerpo` con el secreto de la
    bandeja, en HMAC-SHA256, y lo manda en `X-Chatwoot-Signature`. Verificado
    contra una firma real el 3/10/2026; no esta documentado.

    Sin secreto configurado se rechaza todo. Un webhook sin validar deja que
    cualquiera le escriba a los clientes del cliente.
    """
    s = obtener_settings()
    if not firma or not marca_de_tiempo:
        return False
    recibida = firma.removeprefix("sha256=")
    for secreto in (s.chatwoot_secreto, s.chatwoot_secreto_eventos):
        if not secreto:
            continue
        esperado = hmac.new(
            secreto.encode(),
            marca_de_tiempo.encode() + b"." + cuerpo,
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(esperado, recibida):
            return True
    return False


def respuesta_humana(payload: dict) -> dict | None:
    """Lo que escribio una persona en la bandeja, o None si no corresponde.

    Se descarta todo lo que no sea una respuesta de verdad: lo que espejamos
    nosotros —que vuelve marcado—, lo que entra del cliente, y las notas
    privadas, que son para el equipo y el cliente no tiene que verlas.
    """
    if payload.get("event") != "message_created":
        return None
    if payload.get("message_type") != "outgoing":
        return None
    if payload.get("private"):
        return None
    if (payload.get("content_attributes") or {}).get(MARCA):
        return None

    texto = (payload.get("content") or "").strip()
    conversacion = (payload.get("conversation") or {}).get("id")
    if not texto or not conversacion:
        return None

    autor = payload.get("sender") or {}
    return {
        "texto": texto,
        "conversacion": conversacion,
        "autor": autor.get("name") or "un asesor",
        "id_externo": f"chatwoot:{payload.get('id')}",
    }


def conversacion_resuelta(payload: dict) -> int | None:
    """La conversacion que alguien acaba de dar por atendida, si es el caso.

    Resolver no manda ningun mensaje, asi que es la unica forma de enterarse de
    que una persona se hizo cargo sin escribir todavia.
    """
    if payload.get("event") != "conversation_status_changed":
        return None
    if payload.get("status") != "resolved":
        return None
    return payload.get("id")


async def conversacion_de(id_en_la_bandeja: int) -> dict | None:
    """A que conversacion nuestra corresponde una de la bandeja."""
    fila = await db.consultar_una(
        "SELECT id, canal, identificador FROM conversaciones "
        "WHERE bandeja_referencia->>'conversacion' = $1",
        str(id_en_la_bandeja),
    )
    return dict(fila) if fila else None
