"""Subir archivos a Kommo y colgarlos de un lead.

Un cliente que manda fotos de sus ventanas espera que alguien las mire. El
agente le dice que las revisa un asesor —no puede sacar medidas de una foto y
estimarlas seria inventar— y hasta ahora esa promesa no se cumplia: la foto
quedaba en la base y el asesor abria el lead sin nada.

Kommo lo hace en tres pasos y contra dos dominios distintos: la subida va al
drive de la cuenta y el adjunto al subdominio normal.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.kommo.cliente import Kommo, KommoError

logger = logging.getLogger(__name__)

# Cuanto se espera una subida. Es mas que el resto de las llamadas porque acá
# viajan megabytes, no un JSON.
TIMEOUT = 60


async def drive_de_la_cuenta(kommo: Kommo) -> str:
    """El dominio del drive, que no es el subdominio de la cuenta."""
    cuenta = await kommo._pedir("GET", "/account?with=drive_url")
    url = (cuenta or {}).get("drive_url")
    if not url:
        raise KommoError("la cuenta no informa drive_url: falta el permiso de archivos")
    return url.rstrip("/")


async def subir(kommo: Kommo, drive: str, nombre: str, contenido: bytes,
                mime: str) -> str:
    """Sube un archivo y devuelve su uuid.

    Se sube por partes porque Kommo las limita a medio mega y una foto de
    telefono pasa eso sin esfuerzo. Cada respuesta dice a donde va la parte
    siguiente; la ultima trae el uuid.
    """
    cabeceras = {"Authorization": f"Bearer {kommo._token}"}

    async with httpx.AsyncClient(timeout=TIMEOUT) as cliente:
        r = await cliente.post(f"{drive}/v1.0/sessions", headers=cabeceras, json={
            "file_name": nombre,
            "file_size": len(contenido),
            "content_type": mime,
        })
        if r.status_code >= 400:
            raise KommoError(f"no se pudo abrir la sesion de subida: "
                             f"{r.status_code} {r.text[:200]}")
        sesion = r.json()

        maximo = sesion.get("max_file_size")
        if maximo and len(contenido) > maximo:
            raise KommoError(f"el archivo pesa {len(contenido)} y el maximo es {maximo}")

        parte = sesion.get("max_part_size") or len(contenido)
        url = sesion["upload_url"]

        for desde in range(0, len(contenido), parte):
            r = await cliente.post(url, headers=cabeceras,
                                   content=contenido[desde:desde + parte])
            if r.status_code >= 400:
                raise KommoError(f"fallo una parte de la subida: "
                                 f"{r.status_code} {r.text[:200]}")
            respuesta = r.json()
            # Mientras queden partes, Kommo dice a donde mandar la siguiente.
            # Cuando termina, devuelve el archivo con su uuid.
            if respuesta.get("uuid"):
                return respuesta["uuid"]
            url = respuesta.get("next_url") or url

    raise KommoError("la subida termino sin devolver el uuid del archivo")


async def adjuntar(kommo: Kommo, id_lead: int, uuids: list[str]) -> None:
    """Cuelga del lead archivos que ya estan en el drive."""
    if not uuids:
        return
    # Devuelve 200 con el cuerpo vacio, no un JSON.
    await kommo._pedir("PUT", f"/leads/{id_lead}/files",
                       [{"file_uuid": u} for u in uuids])


def nombre_para(media: dict[str, Any], numero: int) -> str:
    """Un nombre legible para quien abra el lead.

    Las fotos de WhatsApp no traen nombre, asi que se les pone uno: un asesor
    que ve "foto-1.jpg" entiende mas que con un uuid de treinta caracteres.
    """
    if media.get("nombre"):
        return media["nombre"]
    mime = media.get("mime") or ""
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
                 "application/pdf": "pdf"}.get(mime, "bin")
    return f"foto-{numero}.{extension}"
