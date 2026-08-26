"""Los campos a relevar y los pesos del puntaje, leidos de YAML.

Vive aparte de las herramientas del agente porque lo leen tambien el scoring y
el documento del CRM, y tenerlo alla creaba un import circular.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RUTA = Path(__file__).resolve().parent.parent / "config" / "calificacion.yaml"


@lru_cache
def calificacion() -> dict[str, Any]:
    return yaml.safe_load(RUTA.read_text(encoding="utf-8"))
