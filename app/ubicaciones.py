"""De que zona de precios es una ciudad ecuatoriana.

Desde el 29/9/2026 MasterShield cotiza por cinco zonas, cada una con su minimo
de instalacion y su tabla de precios. Antes eran dos —Quito o no Quito— y eso el
modelo lo resolvia solo; con cinco no puede, porque saber que Gualaceo es Azuay
y que Azuay es zona verde no es criterio sino un dato duro.

Asi que la zona la decide el codigo a partir de la ciudad, igual que la linea se
deduce del objetivo. La tabla vive en `config/ubicaciones.yaml`.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RUTA = Path(__file__).resolve().parent.parent / "config" / "ubicaciones.yaml"


@lru_cache
def configuracion(ruta: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((ruta or RUTA).read_text(encoding="utf-8"))


def normal(texto: str) -> str:
    """Sin tildes, sin mayusculas y sin espacios de sobra.

    La gente escribe "cumbaya", "Cumbayá" y "CUMBAYA": las tres son la misma.
    """
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto or "")
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(sin_tildes.lower().split())


@lru_cache
def _indice() -> dict[str, str]:
    """Nombre normalizado -> zona. Provincias y ciudades en la misma tabla.

    Una ciudad que se llama igual que su provincia (Loja, Esmeraldas, Santa
    Elena, Zamora, Cañar) cae en la misma zona por las dos vias, asi que no hay
    conflicto que resolver.
    """
    tabla: dict[str, str] = {}
    for zona, datos in configuracion()["zonas"].items():
        for provincia, ciudades in (datos.get("provincias") or {}).items():
            tabla.setdefault(normal(provincia), zona)
            for ciudad in ciudades or []:
                tabla.setdefault(normal(ciudad), zona)
    return tabla


def zona_de(lugar: str | None) -> str | None:
    """La zona de precios de un lugar, o None si no esta en la tabla.

    Reconoce la ciudad o la provincia, y tambien cuando viene dentro de una
    frase ("la instalacion es en Cuenca", "estoy en el norte de Quito"). No
    inventa: si no aparece, devuelve None y el agente pregunta.
    """
    if not lugar:
        return None
    texto = normal(lugar)
    indice = _indice()

    if texto in indice:
        return indice[texto]

    # Dentro de una frase. Se prueban primero los nombres largos, para que
    # "San Miguel de los Bancos" gane sobre "San Miguel", que es otra zona.
    for nombre in sorted(indice, key=len, reverse=True):
        if _aparece(nombre, texto):
            return indice[nombre]
    return None


def _aparece(nombre: str, texto: str) -> bool:
    """Si `nombre` esta en `texto` como palabra completa.

    Sin esto "Mira" (Carchi) aparece dentro de "mirador" y "Baba" (Los Rios)
    dentro de "Babahoyo", que es la misma zona pero por casualidad.
    """
    desde = 0
    while (i := texto.find(nombre, desde)) != -1:
        antes = texto[i - 1] if i else " "
        despues = texto[i + len(nombre)] if i + len(nombre) < len(texto) else " "
        if not antes.isalnum() and not despues.isalnum():
            return True
        desde = i + 1
    return False


def se_atiende(zona: str | None) -> bool:
    """Si esa zona se vende. Galapagos y el exterior son descarte."""
    from app.precios import configuracion as productos

    datos = (productos().get("zonas") or {}).get(zona or "")
    return bool(datos and datos.get("atiende"))
