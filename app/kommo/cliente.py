"""Cliente HTTP de la API v4 de Kommo.

Token de larga duracion, sin refresh: Kommo los emite con vencimiento de hasta
5 años para integraciones privadas, asi que no hay que implementar el baile de
refrescar cada 24 horas que exige el OAuth normal.

Todo lo que sabe de negocio esta afuera. Aca solo viven la autenticacion, los
reintentos y la traduccion de errores.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.config import obtener_settings

logger = logging.getLogger(__name__)

RUTA_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "kommo.yaml"

REINTENTOS = 3
ESPERA_BASE = 2.0


class KommoNoConfigurado(RuntimeError):
    """Faltan credenciales o los IDs de la cuenta."""


class KommoError(RuntimeError):
    """La API respondio algo que no se puede resolver reintentando."""


@lru_cache
def configuracion() -> dict[str, Any]:
    return yaml.safe_load(RUTA_CONFIG.read_text(encoding="utf-8"))


class Kommo:
    def __init__(self, subdominio: str | None = None, token: str | None = None) -> None:
        settings = obtener_settings()
        self._subdominio = subdominio or settings.kommo_subdomain
        self._token = token or settings.kommo_access_token
        if not self._subdominio or not self._token:
            raise KommoNoConfigurado("faltan KOMMO_SUBDOMAIN o KOMMO_ACCESS_TOKEN")

    @property
    def _base(self) -> str:
        return f"https://{self._subdominio}.kommo.com/api/v4"

    async def _pedir(self, metodo: str, ruta: str, cuerpo: Any = None,
                     parametros: dict | None = None) -> Any:
        """Una llamada, con reintentos ante fallas transitorias.

        Un 4xx que no sea 429 no mejora reintentando: es el payload o los
        permisos. Se propaga para que quien llama lo registre y no insista.
        """
        cabeceras = {"Authorization": f"Bearer {self._token}"}
        ultimo: Exception | None = None

        for intento in range(1, REINTENTOS + 1):
            try:
                async with httpx.AsyncClient(base_url=self._base, headers=cabeceras,
                                             timeout=30) as cliente:
                    respuesta = await cliente.request(
                        metodo, ruta, json=cuerpo, params=parametros
                    )

                if respuesta.status_code == 204:
                    return None  # Kommo devuelve 204 cuando no hay resultados
                if respuesta.status_code < 400:
                    return respuesta.json()

                if respuesta.status_code < 500 and respuesta.status_code != 429:
                    raise KommoError(
                        f"{metodo} {ruta} -> {respuesta.status_code}: "
                        f"{respuesta.text[:400]}"
                    )
                ultimo = KommoError(f"{metodo} {ruta} -> {respuesta.status_code}")

            except (httpx.HTTPError, asyncio.TimeoutError) as e:
                ultimo = e

            if intento < REINTENTOS:
                espera = ESPERA_BASE * (2 ** (intento - 1))
                logger.warning("reintento %s de %s %s en %.0fs", intento, metodo, ruta, espera)
                await asyncio.sleep(espera)

        raise KommoError(f"{metodo} {ruta} fallo tras {REINTENTOS} intentos: {ultimo}")

    # --- contactos ----------------------------------------------------------

    async def buscar_contacto(self, telefono: str) -> int | None:
        """Busca por telefono. Es lo que evita crear un contacto por conversacion."""
        cuerpo = await self._pedir("GET", "/contacts", parametros={"query": telefono})
        if not cuerpo:
            return None
        contactos = cuerpo.get("_embedded", {}).get("contacts", [])
        return contactos[0]["id"] if contactos else None

    async def crear_contacto(self, nombre: str, telefono: str | None) -> int:
        campos = configuracion()["campos_contacto"]
        cuerpo: dict[str, Any] = {"name": nombre}
        if telefono:
            cuerpo["custom_fields_values"] = [{
                "field_id": campos["telefono"],
                "values": [{"value": telefono, "enum_id": campos["telefono_tipo"]}],
            }]
        respuesta = await self._pedir("POST", "/contacts", [cuerpo])
        return respuesta["_embedded"]["contacts"][0]["id"]

    # --- leads --------------------------------------------------------------

    async def crear_lead(self, cuerpo: dict) -> int:
        respuesta = await self._pedir("POST", "/leads", [cuerpo])
        return respuesta["_embedded"]["leads"][0]["id"]

    async def actualizar_lead(self, id_lead: int, cuerpo: dict) -> None:
        await self._pedir("PATCH", f"/leads/{id_lead}", cuerpo)

    async def agregar_nota(self, id_lead: int, texto: str) -> None:
        await self._pedir("POST", f"/leads/{id_lead}/notes", [
            {"note_type": "common", "params": {"text": texto[:65000]}}
        ])

    async def crear_tarea(self, id_lead: int, texto: str, vence: int) -> int:
        respuesta = await self._pedir("POST", "/tasks", [{
            "text": texto,
            "complete_till": vence,
            "entity_id": id_lead,
            "entity_type": "leads",
            "task_type_id": 1,  # llamada
        }])
        return respuesta["_embedded"]["tasks"][0]["id"]

    async def cuenta(self) -> dict:
        return await self._pedir("GET", "/account")
