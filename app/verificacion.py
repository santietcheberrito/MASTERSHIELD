"""Verifica que lo que el agente dice coincida con lo que calculo el sistema.

El precio lo decide Python, pero el mensaje lo escribe el modelo. Entre una
cosa y la otra hay lugar para que un numero salga distinto: por un error del
modelo, o porque alguien lo convencio de decir otra cosa. Esto es lo ultimo que
pasa antes de enviar.

La regla: si el mensaje menciona plata, `calcular_precio` tuvo que haberse
llamado en este mismo turno y el numero tiene que coincidir. Repetir un precio
de memoria no vale — hay que volver a calcularlo.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Un numero es "plata" si viene con signo de peso adelante o con la moneda
# detras. Asi no se confunde con metros cuadrados, años de garantia ni
# porcentajes, que aparecen todo el tiempo y son legitimos.
_MONTO = re.compile(
    r"(?:\$\s*(?P<simbolo>[\d.,]+))"
    r"|(?:(?P<palabra>[\d.,]+)\s*(?:dolares|dólares|usd))",
    re.IGNORECASE,
)

MENSAJE_SEGURO = (
    "Prefiero que un asesor MS le confirme el valor exacto. "
    "¿Me facilita un número de contacto para que lo llamen?"
)


def _a_numero(texto: str) -> float | None:
    """Interpreta 1050, 1.050 y 1,050 como el mismo numero."""
    limpio = texto.strip().rstrip(".,").replace(".", "").replace(",", "")
    try:
        return float(limpio)
    except ValueError:
        return None


def montos_mencionados(texto: str) -> list[float]:
    montos = []
    for coincidencia in _MONTO.finditer(texto or ""):
        crudo = coincidencia.group("simbolo") or coincidencia.group("palabra")
        numero = _a_numero(crudo)
        if numero is not None:
            montos.append(numero)
    return montos


def verificar(texto: str, autorizados: set[float]) -> tuple[bool, str]:
    """Devuelve (esta_bien, motivo).

    `autorizados` son los montos que `calcular_precio` devolvio en este turno:
    el subtotal y el precio por m2.
    """
    montos = montos_mencionados(texto)
    if not montos:
        return True, ""

    if not autorizados:
        return False, f"menciona {montos} sin haber calculado ningun precio en el turno"

    inventados = [m for m in montos if m not in autorizados]
    if inventados:
        return False, f"menciona {inventados}, calculados {sorted(autorizados)}"

    return True, ""
