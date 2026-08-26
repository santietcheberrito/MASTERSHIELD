"""Cotización: reglas de negocio en Python, no en el prompt.

El modelo releva, Python calcula. Es el mismo principio que el scoring, y acá
importa más todavía: el número que salga de acá es un precio que un cliente
real va a leer y recordar. Un LLM haciendo regla de tres no es confiable, y
tampoco lo es un LLM decidiendo si puede hacer una excepción al mínimo de
venta porque el cliente insistió.

Todo sale de `config/productos.yaml`. Cambiar un precio es editar el YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RUTA = Path(__file__).resolve().parent.parent / "config" / "productos.yaml"


class ConfiguracionIncompleta(RuntimeError):
    """Falta un dato del cliente sin el cual el número saldría mal."""


@lru_cache
def configuracion(ruta: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((ruta or RUTA).read_text(encoding="utf-8"))


@dataclass
class Cotizacion:
    """Resultado del cálculo. `puede_cotizar` es lo primero que hay que mirar."""

    puede_cotizar: bool
    motivo: str = ""
    producto: str = ""
    zona: str = ""
    metros: float = 0.0

    # "exacto" o "desde": con seguridad arquitectónica el grosor lo define un
    # asesor, así que el agente da un piso y nunca un total cerrado.
    tipo: str = "exacto"

    precio_m2: float | None = None
    recargo_m2: float = 0.0
    subtotal: float | None = None
    garantia_anios: int | None = None

    # Lo que el agente tiene que relevar antes de poder avanzar.
    datos_faltantes: list[str] = field(default_factory=list)
    # Cuánto le falta para llegar al mínimo, si no llega.
    minimo_m2: float | None = None

    descuento_pago_contado: int | None = None
    incluye: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Los precios del cliente son todos sin IVA y el agente los dice con la
        # frase "más IVA", no sumándolo por su cuenta: así el número que sale
        # por chat es el mismo que figura en la lista de la empresa.
        if self.subtotal is not None:
            self.subtotal = round(self.subtotal, 2)
        if self.precio_m2 is not None:
            self.precio_m2 = round(self.precio_m2, 2)


# Que producto corresponde a lo que el cliente necesita. Vive aca y no en las
# herramientas del agente porque lo usan tambien el documento del CRM y el
# scoring, y tenerlo alla creaba un import circular.
PRODUCTO_POR_OBJETIVO = {
    ("arquitectonico", "control_solar"): "control_solar_arquitectonico",
    ("arquitectonico", "privacidad"): "privacidad_arquitectonica",
    ("arquitectonico", "seguridad"): "seguridad_arquitectonica",
    ("vehicular", "seguridad"): "seguridad_vehicular",
}


def _producto(id_producto: str) -> dict[str, Any] | None:
    for p in configuracion()["productos"]:
        if p["id"] == id_producto:
            return p
    return None


def _opcion(producto: dict[str, Any], garantia_anios: int | None) -> dict[str, Any] | None:
    opciones = producto.get("opciones") or []
    if not opciones:
        return None
    if garantia_anios is None:
        return None
    for opcion in opciones:
        if opcion["garantia_anios"] == garantia_anios:
            return opcion
    return None


def garantias_disponibles(id_producto: str) -> list[int]:
    producto = _producto(id_producto)
    if not producto:
        return []
    return [o["garantia_anios"] for o in producto.get("opciones") or []]


def cotizar(
    id_producto: str,
    metros: float | None,
    zona: str | None,
    garantia_anios: int | None = None,
) -> Cotizacion:
    """Calcula el precio de un pedido. No decide nada que no esté en el YAML."""
    cfg = configuracion()

    producto = _producto(id_producto)
    if producto is None:
        return Cotizacion(False, motivo=f"no existe el producto {id_producto!r}")

    # --- la zona define el mínimo y el recargo, así que va primero -----------
    if not zona:
        return Cotizacion(
            False,
            motivo="falta saber dónde está el cliente",
            producto=producto["nombre"],
            datos_faltantes=["zona"],
        )

    datos_zona = cfg["zonas"].get(zona)
    if datos_zona is None:
        return Cotizacion(False, motivo=f"zona desconocida: {zona!r}")

    if not datos_zona["atiende"]:
        return Cotizacion(
            False,
            motivo=f"no se atiende en {datos_zona['nombre'].lower()}",
            producto=producto["nombre"],
            zona=zona,
        )

    # --- productos que no se cotizan por chat -------------------------------
    if producto["cotizable"] == "ninguno":
        return Cotizacion(
            False,
            motivo="este producto lo cotiza un asesor",
            producto=producto["nombre"],
            zona=zona,
            datos_faltantes=list(producto.get("datos_requeridos") or []),
        )

    if metros is None:
        return Cotizacion(
            False,
            motivo="faltan los metros cuadrados",
            producto=producto["nombre"],
            zona=zona,
            datos_faltantes=["medidas"],
        )

    # --- el mínimo de venta lo aplica el código, no el modelo ---------------
    minimo = datos_zona["minimo_m2"]
    if metros < minimo:
        return Cotizacion(
            False,
            motivo="el pedido no llega al mínimo de instalación",
            producto=producto["nombre"],
            zona=zona,
            metros=metros,
            minimo_m2=minimo,
        )

    # --- precio -------------------------------------------------------------
    opcion = _opcion(producto, garantia_anios)
    if opcion is None:
        disponibles = garantias_disponibles(id_producto)
        if len(disponibles) == 1:
            opcion = producto["opciones"][0]
            garantia_anios = opcion["garantia_anios"]
        else:
            return Cotizacion(
                False,
                motivo="falta elegir la garantía del material",
                producto=producto["nombre"],
                zona=zona,
                metros=metros,
                datos_faltantes=["garantia_anios"],
            )

    if producto["cotizable"] == "desde":
        base = opcion["precio_desde"]
        tipo = "desde"
    else:
        # Mientras no esté definido qué activa el precio especial, se usa el
        # normal, que es el más alto. Quedarse corto y que el asesor tenga que
        # subir el número después es peor que arrancar arriba y poder mejorarlo.
        if cfg["criterio_precio_especial"] is None:
            base = opcion["precio_normal"]
        else:
            raise ConfiguracionIncompleta(
                "hay criterio_precio_especial definido pero cotizar() no lo implementa"
            )
        tipo = "exacto"

    recargo = datos_zona["recargo_m2"]
    if recargo and datos_zona.get("recargo_aplica_a_todos") is None and tipo == "desde":
        # No está confirmado si el recargo de otras ciudades aplica también a
        # seguridad arquitectónica. Mientras no se sepa, no se inventa el número.
        return Cotizacion(
            False,
            motivo="falta confirmar el recargo de otras ciudades para este producto",
            producto=producto["nombre"],
            zona=zona,
            metros=metros,
        )

    precio_m2 = base + recargo

    return Cotizacion(
        puede_cotizar=True,
        producto=producto["nombre"],
        zona=zona,
        metros=metros,
        tipo=tipo,
        precio_m2=precio_m2,
        recargo_m2=recargo,
        subtotal=precio_m2 * metros,
        garantia_anios=garantia_anios,
        descuento_pago_contado=cfg["descuento_efectivo_transferencia"],
        incluye=list(cfg["incluye"]),
    )
