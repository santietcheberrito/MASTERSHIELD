"""Puntaje de calificacion.

El modelo extrae, Python puntua. El score no lo decide el LLM: sale de reglas
en `config/calificacion.yaml`, para que recalibrar sea editar un YAML y no
tocar codigo ni volver a desplegar.

Ademas del puntaje hay reglas duras que no se negocian con puntos: una consulta
de fuera de Ecuador no se atiende por mas alto que puntue todo lo demas, y una
que no llega al minimo de instalacion no se puede vender.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.calificacion import calificacion
from app.precios import configuracion as configuracion_productos

# Etapas del embudo. Las tres de calificado son las que un asesor tiene que
# llamar; las otras no requieren llamado.
CONVERSANDO = "Conversando"
ALTA = "Calificado alto"
MEDIA = "Calificado medio"
BAJA = "Calificado bajo"
BAJO_MINIMO = "No llega al mínimo"
FUERA = "Fuera de Ecuador"
DERIVADA = "Derivado a un asesor"


@dataclass
class Puntaje:
    score: int
    clasificacion: str
    etapa: str
    desglose: list[dict[str, Any]] = field(default_factory=list)
    motivo: str = ""

    def resumen(self) -> str:
        lineas = [f"{d['campo']}: {d['valor']} ({d['puntos']:+d})" for d in self.desglose]
        return " · ".join(lineas)


def _puntos_por_tramo(tramos: list[dict], valor: float | None) -> int:
    if valor is None:
        return 0
    for tramo in tramos:
        if valor >= tramo["desde"]:
            return tramo["puntos"]
    return 0


def puntuar(datos: dict[str, Any]) -> Puntaje:
    """Recibe los datos relevados y devuelve score, clasificacion y etapa."""
    pesos = calificacion()["pesos"]
    umbrales = calificacion()["umbrales"]

    # --- reglas duras, antes de contar puntos -------------------------------
    zona = datos.get("zona")
    if zona == "fuera_del_pais":
        return Puntaje(0, "descartada", FUERA, motivo="la consulta es de fuera de Ecuador")

    metros = datos.get("metros_cuadrados")
    linea = datos.get("linea")
    if linea == "arquitectonico" and zona and metros is not None:
        minimo = configuracion_productos()["zonas"][zona]["minimo_m2"]
        if metros < minimo:
            return Puntaje(
                0,
                "descartada",
                BAJO_MINIMO,
                motivo=f"{metros:g} m² contra un mínimo de {minimo:g} m² en esa zona",
            )

    # --- puntaje ------------------------------------------------------------
    desglose: list[dict[str, Any]] = []

    def sumar(campo: str, valor: Any, puntos: int) -> None:
        desglose.append({"campo": campo, "valor": valor, "puntos": puntos})

    score = 0
    for campo in ("zona", "urgencia", "tipo_cliente", "linea"):
        valor = datos.get(campo)
        if valor is None:
            continue
        puntos = pesos[campo].get(valor, 0)
        score += puntos
        sumar(campo, valor, puntos)

    if metros is not None:
        puntos = _puntos_por_tramo(pesos["metros_cuadrados"], metros)
        score += puntos
        sumar("metros_cuadrados", f"{metros:g} m²", puntos)

    if datos.get("disponibilidad"):
        puntos = pesos["disponibilidad_presente"]
        score += puntos
        sumar("disponibilidad", datos["disponibilidad"], puntos)

    score = max(0, min(100, score))

    if score >= umbrales["alta"]:
        return Puntaje(score, "alta", ALTA, desglose)
    if score >= umbrales["media"]:
        return Puntaje(score, "media", MEDIA, desglose)
    return Puntaje(score, "baja", BAJA, desglose)
