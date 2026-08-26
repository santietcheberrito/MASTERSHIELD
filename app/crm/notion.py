"""Destino de prueba: una base de Notion que hace de CRM.

Traduce el documento —que no sabe de ningun CRM— a las propiedades de la base.
Cuando haya acceso a Kommo se escribe el destino equivalente y este queda como
herramienta de prueba, sin tocar nada de lo que esta arriba.

Es idempotente por conversacion: si la fila ya existe, se actualiza en vez de
crear una segunda. Es la misma logica que en Kommo hay que aplicar buscando el
contacto por telefono antes de crearlo.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import obtener_settings
from app.crm.documento import Documento

logger = logging.getLogger(__name__)

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"

REINTENTOS = 3
ESPERA_BASE = 2.0  # segundos; se duplica en cada intento


class NotionNoConfigurado(RuntimeError):
    pass


def _cabeceras() -> dict[str, str]:
    settings = obtener_settings()
    if not settings.notion_token or not settings.notion_data_source_id:
        raise NotionNoConfigurado("faltan NOTION_TOKEN o NOTION_DATA_SOURCE_ID")
    return {
        "Authorization": f"Bearer {settings.notion_token}",
        "Notion-Version": VERSION,
        "Content-Type": "application/json",
    }


def _texto(valor: Any) -> list[dict]:
    return [{"type": "text", "text": {"content": str(valor)[:2000]}}]


def propiedades(documento: Documento) -> dict[str, Any]:
    """El documento traducido a los nombres de campo de la base."""
    lead = documento.lead
    props: dict[str, Any] = {
        "Contacto": {"title": _texto(lead["nombre"])},
        "Etapa": {"select": {"name": lead["etapa"]}},
        "Score": {"number": lead["score"]},
        "Conversación": {"number": documento.conversacion_id},
    }

    simples = {
        "Zona": "zona", "Línea": "linea", "Producto sugerido": "producto",
        "Aplicación": "aplicacion", "Urgencia": "urgencia",
        "Tipo de cliente": "tipo_cliente", "Garantía": "garantia", "Canal": "canal",
    }
    for propiedad, campo in simples.items():
        if lead.get(campo):
            props[propiedad] = {"select": {"name": lead[campo]}}

    if lead.get("telefono"):
        props["Teléfono"] = {"phone_number": lead["telefono"]}
    for propiedad, campo in (("Metros cuadrados", "metros_cuadrados"),
                             ("Presupuesto estimado", "presupuesto")):
        if lead.get(campo) is not None:
            props[propiedad] = {"number": lead[campo]}
    for propiedad, campo in (("Disponibilidad", "disponibilidad"),
                             ("Modelo de vehículo", "modelo_vehiculo"),
                             ("Detalle de medidas", "medidas_detalle")):
        if lead.get(campo):
            props[propiedad] = {"rich_text": _texto(lead[campo])}

    # La tarea de llamado. En Kommo es una tarea sin responsable: los tres
    # vendedores la ven y se la queda el primero que la toma.
    if documento.tarea.get("vence"):
        props["Llamar antes de"] = {"date": {"start": str(documento.tarea["vence"])}}

    return props


def bloques(documento: Documento) -> list[dict]:
    """La nota, que en Kommo va como nota del lead."""
    parrafos = [p for p in documento.nota.split("\n") if p.strip()]
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _texto(p)},
        }
        for p in parrafos[:100]  # Notion acepta 100 bloques por request
    ]


async def _buscar(cliente: httpx.AsyncClient, conversacion_id: int) -> str | None:
    settings = obtener_settings()
    respuesta = await cliente.post(
        f"{API}/data_sources/{settings.notion_data_source_id}/query",
        headers=_cabeceras(),
        json={
            "filter": {"property": "Conversación", "number": {"equals": conversacion_id}},
            "page_size": 1,
        },
    )
    respuesta.raise_for_status()
    resultados = respuesta.json().get("results", [])
    return resultados[0]["id"] if resultados else None


async def sincronizar(documento: Documento) -> str:
    """Crea o actualiza la fila. Devuelve el id de la pagina.

    Reintenta con espera creciente. Si igual falla, propaga: quien llama decide
    si marcar la conversacion para reintentar mas tarde.
    """
    settings = obtener_settings()
    ultimo_error: Exception | None = None

    for intento in range(1, REINTENTOS + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as cliente:
                existente = await _buscar(cliente, documento.conversacion_id)

                if existente:
                    respuesta = await cliente.patch(
                        f"{API}/pages/{existente}",
                        headers=_cabeceras(),
                        json={"properties": propiedades(documento)},
                    )
                    respuesta.raise_for_status()
                    logger.info(
                        "lead actualizado en Notion | conversacion=%s",
                        documento.conversacion_id,
                    )
                    return existente

                respuesta = await cliente.post(
                    f"{API}/pages",
                    headers=_cabeceras(),
                    json={
                        "parent": {"type": "data_source_id",
                                   "data_source_id": settings.notion_data_source_id},
                        "properties": propiedades(documento),
                        "children": bloques(documento),
                    },
                )
                respuesta.raise_for_status()
                id_pagina = respuesta.json()["id"]
                logger.info(
                    "lead creado en Notion | conversacion=%s etapa=%s",
                    documento.conversacion_id,
                    documento.lead["etapa"],
                )
                return id_pagina

        except httpx.HTTPStatusError as e:
            ultimo_error = e
            # 4xx que no sea 429 no mejora reintentando: es un problema del
            # payload o de permisos.
            if e.response.status_code < 500 and e.response.status_code != 429:
                logger.error(
                    "Notion rechazo el lead | conversacion=%s %s %s",
                    documento.conversacion_id,
                    e.response.status_code,
                    e.response.text[:300],
                )
                raise
        except (httpx.HTTPError, asyncio.TimeoutError) as e:
            ultimo_error = e

        if intento < REINTENTOS:
            espera = ESPERA_BASE * (2 ** (intento - 1))
            logger.warning(
                "reintento %s de sincronizacion con Notion en %.0fs | conversacion=%s",
                intento, espera, documento.conversacion_id,
            )
            await asyncio.sleep(espera)

    raise RuntimeError(f"no se pudo sincronizar con Notion: {ultimo_error}")
