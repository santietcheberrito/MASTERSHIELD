#!/usr/bin/env python3.12
"""Lista la estructura real de la cuenta de Kommo.

    python3.12 scripts/mapear_kommo.py

**Solo lee.** No crea ni modifica nada. Sirve para llenar `config/kommo.yaml`
con los IDs numericos de embudos, etapas y campos personalizados, que son lo
unico de la integracion que no se puede escribir sin ver la cuenta.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app.config import obtener_settings  # noqa: E402


async def _traer(cliente: httpx.AsyncClient, ruta: str) -> dict:
    respuesta = await cliente.get(ruta)
    if respuesta.status_code == 204:
        return {}
    respuesta.raise_for_status()
    return respuesta.json()


async def principal() -> int:
    settings = obtener_settings()
    if not settings.kommo_subdomain or not settings.kommo_access_token:
        print("Faltan KOMMO_SUBDOMAIN o KOMMO_ACCESS_TOKEN en .env", file=sys.stderr)
        return 1

    base = f"https://{settings.kommo_subdomain}.kommo.com/api/v4"
    cabeceras = {"Authorization": f"Bearer {settings.kommo_access_token}"}

    async with httpx.AsyncClient(base_url=base, headers=cabeceras, timeout=30) as cliente:
        cuenta = await _traer(cliente, "/account")
        print(f"Cuenta: {cuenta.get('name')} (id {cuenta.get('id')}) · "
              f"pais {cuenta.get('country')} · moneda {cuenta.get('currency')}\n")

        print("=" * 70)
        print("EMBUDOS Y ETAPAS")
        print("=" * 70)
        embudos = (await _traer(cliente, "/leads/pipelines")).get("_embedded", {}).get("pipelines", [])
        for embudo in embudos:
            principal = " (principal)" if embudo.get("is_main") else ""
            print(f"\nembudo {embudo['id']}: {embudo['name']}{principal}")
            for etapa in embudo.get("_embedded", {}).get("statuses", []):
                print(f"    etapa {etapa['id']:<12} {etapa['name']}")

        for entidad, titulo in (("leads", "LEADS"), ("contacts", "CONTACTOS")):
            print("\n" + "=" * 70)
            print(f"CAMPOS PERSONALIZADOS DE {titulo}")
            print("=" * 70)
            campos = (await _traer(cliente, f"/{entidad}/custom_fields")
                      ).get("_embedded", {}).get("custom_fields", [])
            if not campos:
                print("  (ninguno)")
            for campo in campos:
                print(f"  campo {campo['id']:<12} {campo['name']}  [{campo['type']}]")
                for enum in (campo.get("enums") or []):
                    print(f"        opcion {enum['id']:<10} {enum['value']}")

        print("\n" + "=" * 70)
        print("USUARIOS")
        print("=" * 70)
        usuarios = (await _traer(cliente, "/users")).get("_embedded", {}).get("users", [])
        for usuario in usuarios:
            print(f"  usuario {usuario['id']:<12} {usuario['name']}  <{usuario.get('email','')}>")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(principal()))
