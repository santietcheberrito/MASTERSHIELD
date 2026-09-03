#!/usr/bin/env python3.12
"""Crea en Kommo las etapas y campos personalizados que el agente necesita.

    python3.12 scripts/preparar_kommo.py --revisar   # que haria, sin tocar nada
    python3.12 scripts/preparar_kommo.py             # lo crea

Es idempotente: lo que ya existe se reutiliza por nombre y no se duplica, asi
que se puede correr las veces que haga falta.

Al terminar deja los IDs en `config/kommo.yaml`. Esos IDs son de esta cuenta:
en la cuenta real de MasterShield hay que volver a correrlo.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx
import yaml

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app.config import obtener_settings  # noqa: E402

CONFIG = RAIZ / "config" / "kommo.yaml"

# Las etapas que faltan, con el color de la paleta de Kommo que les corresponde.
ETAPAS = [
    ("Calificado alto", "#87f2c0"),
    ("Calificado medio", "#fff000"),
    ("Calificado bajo", "#ffce5a"),
    ("No llega al mínimo", "#ffc8c8"),
    ("Derivado a un asesor", "#ccc8f9"),
]

# Campos del lead. La clave es como los llama el sistema; el nombre es como los
# ve un vendedor en Kommo.
CAMPOS = [
    ("score", "Score", "numeric", None),
    ("clasificacion", "Clasificación", "select", ["Alta", "Media", "Baja", "Descartada"]),
    ("zona", "Zona", "select", ["Quito y valles", "Otra ciudad del país", "Fuera de Ecuador"]),
    ("linea", "Línea", "select", ["Arquitectónico", "Vehicular"]),
    ("producto", "Producto sugerido", "select", [
        "Control Solar Arquitectónico", "Privacidad Arquitectónica",
        "Seguridad Arquitectónica", "Seguridad Vehicular"]),
    ("metros_cuadrados", "Metros cuadrados", "numeric", None),
    ("presupuesto", "Presupuesto estimado (sin IVA)", "numeric", None),
    ("garantia", "Garantía", "select", ["10 años", "5 años"]),
    ("aplicacion", "Aplicación", "select", [
        "Domicilio", "Oficina", "Pérgola", "Local", "Vehículo"]),
    ("urgencia", "Urgencia", "select", ["Inmediato", "Semanas", "Explorando"]),
    ("tipo_cliente", "Tipo de cliente", "select", [
        "Particular", "Empresa", "Constructora o arquitecto"]),
    ("disponibilidad", "Disponibilidad para el llamado", "text", None),
    ("modelo_vehiculo", "Modelo de vehículo", "text", None),
    ("medidas_detalle", "Detalle de medidas", "textarea", None),
    ("canal", "Canal", "select", ["Telegram", "WhatsApp", "Consola"]),
    # Solo se llena en las derivaciones, y es lo primero que necesita leer quien
    # tome la conversacion: por que hay alguien esperando.
    ("motivo_derivacion", "Motivo de derivación", "textarea", None),
]


async def _get(cliente: httpx.AsyncClient, ruta: str) -> dict:
    r = await cliente.get(ruta)
    if r.status_code == 204:
        return {}
    r.raise_for_status()
    return r.json()


async def _post(cliente: httpx.AsyncClient, ruta: str, cuerpo: list) -> dict:
    r = await cliente.post(ruta, json=cuerpo)
    if r.status_code >= 400:
        print(f"  ERROR {r.status_code}: {r.text[:400]}", file=sys.stderr)
        r.raise_for_status()
    return r.json()


async def principal(revisar: bool) -> int:
    settings = obtener_settings()
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    embudo = config["embudo"]["id"]

    base = f"https://{settings.kommo_subdomain}.kommo.com/api/v4"
    cabeceras = {"Authorization": f"Bearer {settings.kommo_access_token}"}

    async with httpx.AsyncClient(base_url=base, headers=cabeceras, timeout=30) as cliente:
        # --- etapas ---------------------------------------------------------
        etapas_cuenta = (await _get(cliente, f"/leads/pipelines/{embudo}")
                         ).get("_embedded", {}).get("statuses", [])
        ids_en_la_cuenta = {e["id"] for e in etapas_cuenta}
        existentes = {e["name"]: e["id"] for e in etapas_cuenta if e["name"]}

        # Las que ya estan anotadas en el YAML se reconocen por id y no por
        # nombre. Un nombre se puede perder —paso: el PATCH de Kommo reemplaza
        # la etapa entera y borro los nombres— y entonces buscar por nombre las
        # da por inexistentes y crea duplicados.
        for nombre, _ in ETAPAS:
            anotada = (config.get("etapas") or {}).get(nombre)
            if anotada in ids_en_la_cuenta:
                existentes[nombre] = anotada

        faltan = [(n, c) for n, c in ETAPAS if n not in existentes]

        print(f"Etapas: {len(existentes)} existentes, {len(faltan)} a crear")
        for nombre, _ in faltan:
            print(f"  + {nombre}")

        if faltan and not revisar:
            cuerpo = [
                {"name": nombre, "sort": 20 + i * 10, "color": color}
                for i, (nombre, color) in enumerate(faltan)
            ]
            creadas = await _post(cliente, f"/leads/pipelines/{embudo}/statuses", cuerpo)
            for etapa in creadas.get("_embedded", {}).get("statuses", []):
                existentes[etapa["name"]] = etapa["id"]
                print(f"    creada {etapa['id']}  {etapa['name']}")

        # --- orden ----------------------------------------------------------
        # Dos cosas aprendidas peleandome con esto:
        #
        # 1. El PATCH REEMPLAZA la etapa, no la fusiona. Si va solo `sort`, se
        #    pierden el nombre y el color. Van siempre los tres juntos.
        # 2. Hay que darle a cada etapa su posicion FINAL, bien espaciada. Con
        #    valores pegados (21, 22, 23...) Kommo los renormaliza y el orden
        #    sale invertido. Con 20, 30, 40... cae donde uno quiere.
        #
        # No existe un PATCH por lotes sobre /statuses: devuelve 405.
        if not revisar:
            print("\nOrdenando el embudo...")
            colores = dict(ETAPAS)
            for posicion, (nombre, _) in enumerate(ETAPAS, start=2):
                id_etapa = existentes.get(nombre)
                if not id_etapa:
                    continue
                r = await cliente.patch(
                    f"/leads/pipelines/{embudo}/statuses/{id_etapa}",
                    json={"name": nombre, "color": colores[nombre],
                          "sort": posicion * 10},
                )
                r.raise_for_status()
            print("  las etapas del agente quedan juntas, despues de la de entrada")

        # --- campos ---------------------------------------------------------
        campos_actuales = {
            c["name"]: c["id"]
            for c in (await _get(cliente, "/leads/custom_fields"))
            .get("_embedded", {}).get("custom_fields", [])
        }
        por_crear = [c for c in CAMPOS if c[1] not in campos_actuales]

        print(f"\nCampos de lead: {len(campos_actuales)} existentes, {len(por_crear)} a crear")
        for _, nombre, tipo, _opciones in por_crear:
            print(f"  + {nombre} [{tipo}]")

        if por_crear and not revisar:
            cuerpo = []
            for _clave, nombre, tipo, opciones in por_crear:
                campo = {"name": nombre, "type": tipo}
                if opciones:
                    campo["enums"] = [
                        {"value": v, "sort": i + 1} for i, v in enumerate(opciones)
                    ]
                cuerpo.append(campo)
            creados = await _post(cliente, "/leads/custom_fields", cuerpo)
            for campo in creados.get("_embedded", {}).get("custom_fields", []):
                campos_actuales[campo["name"]] = campo["id"]
                print(f"    creado {campo['id']}  {campo['name']}")

    if revisar:
        print("\n(--revisar: no se creo nada)")
        return 0

    # --- guardar los IDs ----------------------------------------------------
    config["etapas"] = {
        "Conversando": config["etapas_existentes"]["leads_entrantes"],
        "Calificado alto": existentes.get("Calificado alto"),
        "Calificado medio": existentes.get("Calificado medio"),
        "Calificado bajo": existentes.get("Calificado bajo"),
        "No llega al mínimo": existentes.get("No llega al mínimo"),
        "Fuera de Ecuador": config["etapas_existentes"]["venta_perdido"],
        "Derivado a un asesor": existentes.get("Derivado a un asesor"),
    }
    config["campos_lead"] = {
        clave: campos_actuales.get(nombre) for clave, nombre, _t, _o in CAMPOS
    }

    cabecera = CONFIG.read_text(encoding="utf-8").split("\ncuenta:")[0]
    CONFIG.write_text(
        cabecera + "\n" + yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"\nIDs guardados en {CONFIG.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepara la cuenta de Kommo.")
    parser.add_argument("--revisar", action="store_true", help="muestra que haria, sin crear")
    raise SystemExit(asyncio.run(principal(parser.parse_args().revisar)))
