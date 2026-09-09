#!/usr/bin/env python3.12
"""Deja una cuenta de Kommo lista para el agente.

    python3.12 scripts/preparar_kommo.py --revisar    # que haria, sin tocar nada
    python3.12 scripts/preparar_kommo.py              # lo hace
    python3.12 scripts/preparar_kommo.py --verificar  # dice si quedo lista

Descubre la cuenta y el embudo, crea las etapas y los campos que faltan, carga
el catalogo de productos, y guarda todos los IDs en `config/kommo.yaml`.

Es idempotente: lo que ya existe se reutiliza y no se duplica, asi que se puede
correr las veces que haga falta.

Los IDs son de la cuenta contra la que se corrio y **no significan nada en
otra**. Para mudarse a la cuenta real de MasterShield se cambian las
credenciales del `.env` y se vuelve a correr: el codigo no cambia. El
procedimiento completo esta en `docs/mudanza-a-la-cuenta-real.md`.
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


async def _verificar(cliente: httpx.AsyncClient, config: dict) -> int:
    """Dice si la cuenta quedo lista, y que falta si no.

    Existe para no descubrir en produccion que faltaba un campo. Devuelve la
    cantidad de problemas: 0 es que se puede empezar a atender.
    """
    problemas: list[str] = []
    avisos: list[str] = []

    cuenta = await _get(cliente, "/account?with=drive_url")
    if not cuenta.get("drive_url"):
        problemas.append("el token no tiene permiso de archivos: las fotos que "
                         "mande un cliente no se van a poder adjuntar al lead")

    embudo = config.get("embudo", {}).get("id")
    etapas_cuenta = {
        e["id"]: e["name"]
        for e in (await _get(cliente, f"/leads/pipelines/{embudo}")
                  ).get("_embedded", {}).get("statuses", [])
    }
    for nombre, id_etapa in (config.get("etapas") or {}).items():
        if id_etapa not in etapas_cuenta:
            problemas.append(f"la etapa '{nombre}' apunta al id {id_etapa}, que no "
                             "existe en este embudo")
    print(f"Etapas: {len(config.get('etapas') or {})} anotadas")

    campos_cuenta = {
        c["id"] for c in (await _get(cliente, "/leads/custom_fields")
                          ).get("_embedded", {}).get("custom_fields", [])
    }
    sin_id = [k for k, v in (config.get("campos_lead") or {}).items() if not v]
    rotos = [k for k, v in (config.get("campos_lead") or {}).items()
             if v and v not in campos_cuenta]
    if sin_id:
        problemas.append(f"campos sin id en el YAML: {', '.join(sin_id)}")
    if rotos:
        problemas.append(f"campos que apuntan a ids inexistentes: {', '.join(rotos)}")
    print(f"Campos de lead: {len(config.get('campos_lead') or {})} anotados")

    # Los usuarios deciden a quien le suena el aviso de una derivacion.
    usuarios = (await _get(cliente, "/users")).get("_embedded", {}).get("users", [])
    print(f"Usuarios en la cuenta: {len(usuarios)}")
    for u in usuarios[:6]:
        print(f"  {u['id']:>10}  {u.get('name')}  <{u.get('email')}>")
    if not (config.get("usuarios") or {}).get("responsable_tareas"):
        avisos.append("no hay `usuarios.responsable_tareas` en el YAML: las tareas "
                      "de llamado quedan a nombre del dueño del token")

    catalogo = (config.get("catalogo_productos") or {}).get("id")
    if catalogo:
        elementos = (await _get(cliente, f"/catalogs/{catalogo}/elements")
                     ).get("_embedded", {}).get("elements", [])
        esperados = len(_productos_a_cargar())
        print(f"Catalogo de productos: {len(elementos)} cargados de {esperados}")
        if len(elementos) < esperados:
            avisos.append("faltan productos en el catalogo: correr sin --verificar")
    else:
        avisos.append("no se mapeo el catalogo de productos")

    print()
    for a in avisos:
        print(f"  aviso     {a}")
    for p in problemas:
        print(f"  PROBLEMA  {p}")
    if not problemas:
        print("  La cuenta esta lista." if not avisos
              else "  La cuenta funciona; los avisos de arriba son decisiones pendientes.")
    return len(problemas)


async def _catalogo_de_precios(cliente: httpx.AsyncClient, config: dict,
                              revisar: bool) -> dict:
    """Deja los productos cargados en Kommo, para que MasterShield vea sus precios.

    No es donde el agente los lee —eso sigue saliendo de `config/productos.yaml`—
    pero es el paso previo: cuando el equipo pueda editarlos desde Kommo, la
    tabla ya va a estar armada y con los SKU que la enlazan al producto interno.

    El SKU es la clave: `id_del_producto:garantia`. Sin el, un renombre en Kommo
    dejaria al agente sin saber que fila es cual.
    """
    catalogos = (await _get(cliente, "/catalogs")
                 ).get("_embedded", {}).get("catalogs", [])
    productos = next((c for c in catalogos if c.get("type") == "products"), None)
    if not productos:
        print("\nCatalogo de productos: la cuenta no tiene uno. Se omite.")
        return config

    print(f"\nCatalogo de productos: {productos['name']} (id {productos['id']})")

    campos = {
        c.get("code"): c["id"]
        for c in (await _get(cliente, f"/catalogs/{productos['id']}/custom_fields")
                  ).get("_embedded", {}).get("custom_fields", [])
    }
    if not {"SKU", "PRICE"} <= set(campos):
        print("  le faltan los campos SKU o Precio; se omite")
        return config

    elementos = (await _get(cliente, f"/catalogs/{productos['id']}/elements")
                 ).get("_embedded", {}).get("elements", [])
    por_sku = {}
    for elemento in elementos:
        for cf in elemento.get("custom_fields_values") or []:
            if cf.get("field_id") == campos["SKU"]:
                valor = (cf.get("values") or [{}])[0].get("value")
                if valor:
                    por_sku[str(valor)] = elemento["id"]

    quiero = _productos_a_cargar()
    faltan = [p for p in quiero if p["sku"] not in por_sku]
    print(f"  {len(por_sku)} cargados, {len(faltan)} a crear")
    for p in faltan:
        print(f"  + {p['nombre']}")

    if faltan and not revisar:
        cuerpo = []
        for p in faltan:
            valores = [
                {"field_id": campos["SKU"], "values": [{"value": p["sku"]}]},
                {"field_id": campos["PRICE"], "values": [{"value": p["normal"]}]},
            ]
            if p["especial"] is not None and campos.get("SPECIAL_PRICE_1"):
                valores.append({"field_id": campos["SPECIAL_PRICE_1"],
                                "values": [{"value": p["especial"]}]})
            cuerpo.append({"name": p["nombre"], "custom_fields_values": valores})
        creados = await _post(cliente, f"/catalogs/{productos['id']}/elements", cuerpo)
        for elemento in creados.get("_embedded", {}).get("elements", []):
            print(f"    creado {elemento['id']}  {elemento['name']}")

    config["catalogo_productos"] = {"id": productos["id"], "campos": campos}
    return config


def _productos_a_cargar() -> list[dict]:
    """Los productos del YAML, aplanados a una fila por calidad.

    Kommo tiene un precio por fila, y nuestros productos tienen dos calidades:
    van como dos filas, que ademas es como las lee un vendedor.
    """
    precios = yaml.safe_load(
        (RAIZ / "config" / "productos.yaml").read_text(encoding="utf-8"))
    filas = []
    for producto in precios.get("productos") or []:
        for opcion in producto.get("opciones") or []:
            normal = opcion.get("precio_normal", opcion.get("precio_desde"))
            if normal is None:
                continue
            garantia = opcion.get("garantia_anios")
            filas.append({
                "sku": f"{producto['id']}:{garantia}",
                "nombre": f"{producto['nombre']} — {garantia} años",
                "normal": normal,
                "especial": opcion.get("precio_especial"),
            })
    return filas


async def _descubrir(cliente: httpx.AsyncClient, config: dict) -> dict:
    """Averigua contra que cuenta y que embudo estamos trabajando.

    Antes esto se leia del YAML, que traia los IDs de la cuenta de prueba. En
    una cuenta nueva esos numeros no existen y el script fallaba con un 404 sin
    explicar por que. Se descubren.
    """
    cuenta = await _get(cliente, "/account?with=drive_url")
    print(f"Cuenta: {cuenta.get('name')} (id {cuenta.get('id')})")

    if not cuenta.get("drive_url"):
        print("  OJO: el token no tiene permiso de archivos. Las fotos que mande")
        print("       un cliente no se van a poder adjuntar al lead.")

    embudos = (await _get(cliente, "/leads/pipelines")
               ).get("_embedded", {}).get("pipelines", [])
    if not embudos:
        raise SystemExit("la cuenta no tiene ningun embudo")

    # El principal, o el primero si ninguno lo es.
    embudo = next((e for e in embudos if e.get("is_main")), embudos[0])
    print(f"Embudo: {embudo['name']} (id {embudo['id']})")
    if len(embudos) > 1:
        otros = ", ".join(e["name"] for e in embudos if e["id"] != embudo["id"])
        print(f"  hay otros embudos y no se tocan: {otros}")

    # Las etapas de sistema: la de entrada y la de perdido. El agente las
    # reutiliza en vez de crear unas propias.
    del_sistema = {}
    for etapa in embudo.get("_embedded", {}).get("statuses", []):
        if etapa.get("type") == 1:            # entrantes
            del_sistema["leads_entrantes"] = etapa["id"]
        elif etapa["id"] == 143:              # perdido, id fijo en toda cuenta
            del_sistema["venta_perdido"] = etapa["id"]
    if "leads_entrantes" not in del_sistema:
        # Sin etapa de entrada, la primera del embudo hace las veces.
        primera = sorted(embudo.get("_embedded", {}).get("statuses", []),
                         key=lambda e: e.get("sort", 0))
        if primera:
            del_sistema["leads_entrantes"] = primera[0]["id"]

    config["cuenta"] = {"id": cuenta.get("id"), "subdominio": cuenta.get("subdomain")}
    config["embudo"] = {"id": embudo["id"], "nombre": embudo["name"]}
    config["etapas_existentes"] = {**(config.get("etapas_existentes") or {}), **del_sistema}
    return config


async def principal(revisar: bool, verificar: bool = False) -> int:
    settings = obtener_settings()
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    base = f"https://{settings.kommo_subdomain}.kommo.com/api/v4"
    cabeceras = {"Authorization": f"Bearer {settings.kommo_access_token}"}

    async with httpx.AsyncClient(base_url=base, headers=cabeceras, timeout=30) as cliente:
        config = await _descubrir(cliente, config)
        embudo = config["embudo"]["id"]
        print()

        if verificar:
            return await _verificar(cliente, config)
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

        config = await _catalogo_de_precios(cliente, config, revisar)

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
    parser.add_argument("--verificar", action="store_true",
                        help="dice si la cuenta quedo lista y que falta")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(principal(args.revisar, args.verificar)))
