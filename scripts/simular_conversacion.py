#!/usr/bin/env python3.12
"""Conversar con el agente por consola.

    python3.12 scripts/simular_conversacion.py
    python3.12 scripts/simular_conversacion.py --nombre "Maria" --reiniciar

Usa la base real y las mismas tablas que una conversacion de verdad, asi que lo
que se ve aca es exactamente lo que veria un cliente. Despues de cada respuesta
muestra que herramientas se llamaron y como quedaron los datos relevados, que
es lo que hace falta para darse cuenta de si el agente esta entendiendo.

Comandos: /datos  /reiniciar  /salir
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app import db  # noqa: E402
from app.agente import loop  # noqa: E402
from app.config import obtener_settings  # noqa: E402

CANAL = "consola"

GRIS = "\033[90m"
AZUL = "\033[94m"
VERDE = "\033[92m"
AMARILLO = "\033[93m"
FIN_COLOR = "\033[0m"


async def _conversacion(identificador: str, nombre: str) -> int:
    return await db.valor(
        """
        INSERT INTO conversaciones (canal, identificador, nombre, estado, ultimo_mensaje_en)
        VALUES ($1, $2, $3, 'activa', now())
        ON CONFLICT (canal, identificador) DO UPDATE SET
            estado = 'activa', ultimo_mensaje_en = now()
        RETURNING id
        """,
        CANAL,
        identificador,
        nombre,
    )


async def _borrar(identificador: str) -> None:
    await db.ejecutar(
        "DELETE FROM conversaciones WHERE canal = $1 AND identificador = $2",
        CANAL,
        identificador,
    )


async def _guardar(conversacion_id: int, rol: str, texto: str, n: int) -> None:
    await db.ejecutar(
        """
        INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo, procesado)
        VALUES ($1, $2, $3, $4, true)
        """,
        conversacion_id,
        rol,
        texto,
        f"{CANAL}:{conversacion_id}:{rol}:{n}",
    )


async def _datos(conversacion_id: int) -> dict:
    return await db.valor("SELECT datos FROM conversaciones WHERE id = $1", conversacion_id) or {}


async def _estado(conversacion_id: int) -> str:
    return await db.valor("SELECT estado FROM conversaciones WHERE id = $1", conversacion_id)


def _mostrar_datos(datos: dict) -> None:
    if not datos:
        print(f"{GRIS}  (todavia no relevo nada){FIN_COLOR}")
        return
    for clave, valor in sorted(datos.items()):
        print(f"{GRIS}  {clave} = {valor}{FIN_COLOR}")


async def principal(identificador: str, nombre: str, reiniciar: bool) -> int:
    settings = obtener_settings()
    if not settings.anthropic_api_key:
        print("Falta ANTHROPIC_API_KEY en .env", file=sys.stderr)
        return 1

    await db.iniciar(settings.database_url, minimo=1, maximo=2)
    try:
        if reiniciar:
            await _borrar(identificador)

        conversacion_id = await _conversacion(identificador, nombre)
        datos = await _datos(conversacion_id)

        print(f"{AZUL}Conversacion {conversacion_id} · modelo {settings.modelo_agente}{FIN_COLOR}")
        print(f"{GRIS}Comandos: /datos  /reiniciar  /salir{FIN_COLOR}\n")
        if datos:
            print(f"{GRIS}Retomando. Ya sabe:{FIN_COLOR}")
            _mostrar_datos(datos)
            print()

        n = 0
        while True:
            try:
                entrada = input(f"{VERDE}vos>{FIN_COLOR} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not entrada:
                continue
            if entrada == "/salir":
                break
            if entrada == "/datos":
                _mostrar_datos(await _datos(conversacion_id))
                print(f"{GRIS}  estado = {await _estado(conversacion_id)}{FIN_COLOR}")
                continue
            if entrada == "/reiniciar":
                await _borrar(identificador)
                conversacion_id = await _conversacion(identificador, nombre)
                n = 0
                print(f"{GRIS}  conversacion {conversacion_id}, de cero{FIN_COLOR}")
                continue

            n += 1
            await _guardar(conversacion_id, "cliente", entrada, n)

            respuesta = await loop.responder(conversacion_id)

            if respuesta.texto:
                await _guardar(conversacion_id, "agente", respuesta.texto, n)
                print(f"{AMARILLO}MS >{FIN_COLOR} {respuesta.texto}")
            else:
                print(f"{AMARILLO}MS >{FIN_COLOR} {GRIS}(sin texto){FIN_COLOR}")

            usadas = ", ".join(respuesta.herramientas_usadas) or "-"
            print(
                f"{GRIS}  [{respuesta.iteraciones} vuelta(s) · herramientas: {usadas} · "
                f"tokens {respuesta.tokens_entrada}/{respuesta.tokens_salida} · "
                f"cache {respuesta.tokens_cache_leidos}]{FIN_COLOR}"
            )
            _mostrar_datos(await _datos(conversacion_id))
            print()

        print(f"{GRIS}La conversacion queda guardada. Para borrarla: --reiniciar{FIN_COLOR}")
        return 0
    finally:
        await db.cerrar()


def main() -> int:
    parser = argparse.ArgumentParser(description="Conversar con el agente por consola.")
    parser.add_argument("--identificador", default="simulador")
    parser.add_argument("--nombre", default="Cliente de prueba")
    parser.add_argument("--reiniciar", action="store_true", help="borra la conversacion anterior")
    args = parser.parse_args()
    return asyncio.run(principal(args.identificador, args.nombre, args.reiniciar))


if __name__ == "__main__":
    raise SystemExit(main())
