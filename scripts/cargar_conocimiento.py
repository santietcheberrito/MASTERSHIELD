#!/usr/bin/env python3.12
"""Carga las respuestas por situacion y la informacion de MasterShield a la base.

    .venv/bin/python scripts/cargar_conocimiento.py            # carga todo
    .venv/bin/python scripts/cargar_conocimiento.py --revisar  # solo muestra que cargaria

Fuentes:
- conocimiento/respuestas.yaml -> tabla `respuestas` (se actualiza por clave; las
  claves que ya no estan en el archivo quedan inactivas, no se borran).
- prompts/conocimiento.md      -> tabla `catalogo`, un fragmento por seccion "##".

Cada fila lleva su embedding para la busqueda por similitud. Se puede correr las
veces que haga falta: el resultado es el mismo.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app import db  # noqa: E402
from app.config import obtener_settings  # noqa: E402
from app.contexto import MODELO_EMBEDDINGS, vector_sql  # noqa: E402

RESPUESTAS = RAIZ / "conocimiento" / "respuestas.yaml"
CONOCIMIENTO = RAIZ / "prompts" / "conocimiento.md"
DOCUMENTO = "conocimiento"
ETAPAS_VALIDAS = {"nombre_ciudad", "pedido", "superficie", "referencia", "precio",
                  "llamada", "despues_del_cierre", "general"}
# Secciones que el agente tiene que tener presentes aunque la pregunta no las nombre.
SECCIONES_PRIORITARIAS = ("NO hace", "límites")


def _zonas_validas() -> set[str]:
    import yaml as _yaml
    productos = _yaml.safe_load(
        (RAIZ / "config" / "productos.yaml").read_text(encoding="utf-8"))
    return set(productos.get("zonas") or {})


ZONAS_VALIDAS = _zonas_validas()


def leer_respuestas(ruta: Path = RESPUESTAS) -> list[dict]:
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    filas = datos["respuestas"]
    claves = [f["clave"] for f in filas]
    if len(claves) != len(set(claves)):
        raise ValueError("hay claves repetidas en respuestas.yaml")
    for f in filas:
        if f["etapa"] not in ETAPAS_VALIDAS:
            raise ValueError(f"etapa desconocida en {f['clave']}: {f['etapa']}")
        if not (f.get("respuesta") or f.get("instrucciones")):
            raise ValueError(f"{f['clave']} no tiene respuesta ni instrucciones")
        # Una zona mal escrita en `solo_zonas` deja al agente sin esa respuesta
        # y sin aviso: en el cierre, sin nada que ofrecer.
        for zona in f.get("solo_zonas") or []:
            if zona not in ZONAS_VALIDAS:
                raise ValueError(f"zona desconocida en {f['clave']}: {zona}")
    return filas


def fragmentos(ruta: Path = CONOCIMIENTO) -> list[dict]:
    """Un fragmento por seccion '## '. Lo que esta antes de la primera es la
    explicacion del archivo, no informacion para el cliente."""
    texto = ruta.read_text(encoding="utf-8")
    salida = []
    for bloque in texto.split("\n## ")[1:]:
        titulo, _, cuerpo = bloque.partition("\n")
        cuerpo = cuerpo.replace("\n---\n", "\n").strip()
        if not cuerpo:
            continue
        prioridad = 2 if any(p in titulo for p in SECCIONES_PRIORITARIAS) else 0
        salida.append({"titulo": titulo.strip(), "contenido": cuerpo, "prioridad": prioridad})
    return salida


def texto_para_buscar_respuesta(f: dict) -> str:
    return f"{f['situacion']}\n{f.get('respuesta') or ''}".strip()


async def _embeddings(textos: list[str]) -> list[list[float]]:
    from openai import AsyncOpenAI

    cliente = AsyncOpenAI(api_key=obtener_settings().openai_api_key)
    r = await cliente.embeddings.create(model=MODELO_EMBEDDINGS, input=textos)
    return [d.embedding for d in r.data]


async def cargar() -> None:
    respuestas = leer_respuestas()
    partes = fragmentos()
    vectores_r = await _embeddings([texto_para_buscar_respuesta(f) for f in respuestas])
    vectores_c = await _embeddings([f"{p['titulo']}\n{p['contenido']}" for p in partes])

    await db.iniciar(obtener_settings().database_url)
    try:
        async with db.pool().acquire() as conexion:
            async with conexion.transaction():
                for f, v in zip(respuestas, vectores_r):
                    await conexion.execute(
                        """
                        INSERT INTO respuestas (clave, etapa, situacion, respuesta,
                                                instrucciones, solo_zonas, activa,
                                                embedding, actualizado_en)
                        VALUES ($1, $2, $3, $4, $5, $6, true, $7::vector, now())
                        ON CONFLICT (clave) DO UPDATE SET
                            etapa = EXCLUDED.etapa, situacion = EXCLUDED.situacion,
                            respuesta = EXCLUDED.respuesta,
                            instrucciones = EXCLUDED.instrucciones,
                            solo_zonas = EXCLUDED.solo_zonas, activa = true,
                            embedding = EXCLUDED.embedding, actualizado_en = now()
                        """,
                        f["clave"], f["etapa"], f["situacion"].strip(),
                        (f.get("respuesta") or "").strip(),
                        (f.get("instrucciones") or "").strip(),
                        f.get("solo_zonas") or None, vector_sql(v),
                    )
                await conexion.execute(
                    "UPDATE respuestas SET activa = false WHERE NOT (clave = ANY($1::text[]))",
                    [f["clave"] for f in respuestas])

                await conexion.execute("DELETE FROM catalogo WHERE documento = $1", DOCUMENTO)
                for p, v in zip(partes, vectores_c):
                    await conexion.execute(
                        "INSERT INTO catalogo (documento, titulo, contenido, prioridad, embedding) "
                        "VALUES ($1, $2, $3, $4, $5::vector)",
                        DOCUMENTO, p["titulo"], p["contenido"], p["prioridad"], vector_sql(v))
    finally:
        await db.cerrar()

    print(f"respuestas cargadas: {len(respuestas)}")
    print(f"fragmentos de conocimiento cargados: {len(partes)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--revisar", action="store_true", help="solo muestra que cargaria")
    args = parser.parse_args()
    if args.revisar:
        for f in leer_respuestas():
            print(f"[{f['etapa']}] {f['clave']}: {f['situacion']}")
        for p in fragmentos():
            print(f"(catalogo, prioridad {p['prioridad']}) {p['titulo']}")
        return 0
    asyncio.run(cargar())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
