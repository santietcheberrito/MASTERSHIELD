#!/usr/bin/env python3.12
"""Aplica las migraciones SQL pendientes.

No usamos Alembic: son cinco tablas y una dependencia fuera del stack cerrado.
El registro de lo aplicado vive en la tabla `migraciones`.

    python3.12 scripts/migrar.py            # aplica lo que falte
    python3.12 scripts/migrar.py --estado   # solo lista, no toca nada
    python3.12 scripts/migrar.py --dsn ...  # contra otra base

Cada archivo se aplica dentro de una transaccion junto con su registro en
`migraciones`: si el SQL falla a la mitad, no queda ni el esquema a medias ni
la marca de aplicado.
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app import db  # noqa: E402
from app.config import obtener_settings  # noqa: E402

MIGRACIONES = RAIZ / "migrations"

TABLA_REGISTRO = """
CREATE TABLE IF NOT EXISTS migraciones (
    nombre      text PRIMARY KEY,
    aplicada_en timestamptz NOT NULL DEFAULT now()
)
"""


def archivos() -> list[Path]:
    return sorted(MIGRACIONES.glob("*.sql"))


async def _aplicadas(conexion) -> set[str]:
    filas = await conexion.fetch("SELECT nombre FROM migraciones")
    return {fila["nombre"] for fila in filas}


async def correr(dsn: str, *, solo_estado: bool = False) -> int:
    pool = await db.crear_pool(dsn, minimo=1, maximo=1)
    try:
        async with pool.acquire() as conexion:
            await conexion.execute(TABLA_REGISTRO)
            aplicadas = await _aplicadas(conexion)

            pendientes = [a for a in archivos() if a.name not in aplicadas]

            for archivo in archivos():
                marca = "ya aplicada" if archivo.name in aplicadas else "PENDIENTE"
                print(f"  {archivo.name:<28} {marca}")

            if solo_estado:
                return 0

            if not pendientes:
                print("\nNo hay migraciones pendientes.")
                return 0

            for archivo in pendientes:
                print(f"\nAplicando {archivo.name}...")
                sql = archivo.read_text(encoding="utf-8")
                async with conexion.transaction():
                    await conexion.execute(sql)
                    await conexion.execute(
                        "INSERT INTO migraciones (nombre) VALUES ($1)", archivo.name
                    )
                print(f"  {archivo.name} aplicada.")

            print(f"\n{len(pendientes)} migracion(es) aplicada(s).")
            return 0
    finally:
        await pool.close()


def explicar_dns(destino: str) -> str:
    """El error de DNS mas comun con Supabase tiene una causa muy puntual.

    Los hosts `db.<ref>.supabase.co` solo publican registro AAAA: si la red no
    tiene IPv6, no resuelven. El mensaje de socket.gaierror no dice nada de eso
    y manda a buscar el problema donde no esta.
    """
    mensaje = [f"\nNo se pudo resolver el host: {destino}"]
    if ".supabase.co" in destino and ".pooler." not in destino:
        mensaje += [
            "",
            "La conexion directa de Supabase (db.<ref>.supabase.co) solo tiene",
            "registro IPv6. Si esta red no tiene IPv6, no hay forma de llegar.",
            "",
            "Usar el Session pooler del dashboard, que si tiene IPv4:",
            "  Project Settings -> Database -> Connection string -> Session pooler",
            "  postgresql://postgres.<ref>:<clave>@aws-N-<region>.pooler.supabase.com:5432/postgres",
            "",
            "Ojo con el puerto: 5432 es session, 6543 es transaction. Para migrar, 5432.",
        ]
    return "\n".join(mensaje)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aplica las migraciones SQL pendientes.")
    parser.add_argument("--dsn", help="DATABASE_URL a usar; por defecto la del entorno o .env")
    parser.add_argument(
        "--estado",
        action="store_true",
        help="lista que migraciones estan aplicadas y sale sin tocar la base",
    )
    args = parser.parse_args()

    dsn = args.dsn or obtener_settings().database_url
    destino = dsn.split("@")[-1] if "@" in dsn else dsn
    print(f"Base: ...@{destino}\n")

    try:
        return asyncio.run(correr(dsn, solo_estado=args.estado))
    except socket.gaierror:
        print(explicar_dns(destino), file=sys.stderr)
        return 1
    except OSError as e:
        print(f"\nNo se pudo conectar a la base: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
