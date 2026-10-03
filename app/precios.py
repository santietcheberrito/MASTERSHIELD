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
from datetime import date, datetime
from zoneinfo import ZoneInfo
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


# Los datos de la conversacion que eligen el producto, en el orden en que se
# desempata. La linea sola ya alcanza para vehicular; el objetivo, para
# privacidad y seguridad; control solar necesita ademas la superficie.
_CLAVES_DEL_PRODUCTO = ("linea", "objetivo", "superficie")


def producto_para(datos: dict[str, Any]) -> str | None:
    """Que producto corresponde a lo que el cliente necesita, o None si no alcanza.

    Se filtra el catalogo por cada dato hasta que queda uno solo. Con una tabla
    fija, sumar un producto era tocar codigo en tres lugares; asi es agregarlo
    al YAML. Vive aca y no en las herramientas porque lo usa tambien el
    documento del CRM, y tenerlo alla creaba un import circular.
    """
    candidatos = _candidatos(datos)
    return candidatos[0]["id"] if len(candidatos) == 1 else None


def producto_para_cotizar(datos: dict[str, Any]) -> str | None:
    """El producto cuyo precio corresponde, aunque no se sepa cual de dos es.

    Control solar para ventanas y para techos cuestan lo mismo, asi que para
    decir el precio no hace falta saber la superficie. Se compara el precio y no
    se asume: si algun dia dejan de costar lo mismo, esto devuelve None y el
    agente vuelve a preguntar antes de dar un numero.
    """
    candidatos = _candidatos(datos)
    if not candidatos:
        return None
    primero = candidatos[0]
    mismo_precio = all(
        (p["cotizable"], p.get("tabla_de_precios"))
        == (primero["cotizable"], primero.get("tabla_de_precios"))
        for p in candidatos
    )
    return primero["id"] if len(candidatos) == 1 or mismo_precio else None


def _candidatos(datos: dict[str, Any]) -> list[dict[str, Any]]:
    """Los productos que siguen en pie con lo que se sabe hasta ahora."""
    candidatos = configuracion()["productos"]
    for clave in _CLAVES_DEL_PRODUCTO:
        if len(candidatos) <= 1:
            break
        if datos.get(clave) is None:
            break  # sin ese dato no se puede desempatar mas
        candidatos = [p for p in candidatos if p.get(clave) == datos[clave]]
    return candidatos


def _producto(id_producto: str) -> dict[str, Any] | None:
    for p in configuracion()["productos"]:
        if p["id"] == id_producto:
            return p
    return None


def minimo_de(zona: str | None) -> float | None:
    """Los m2 minimos de instalacion de esa zona, de 5 a 25 segun el documento."""
    datos = (configuracion().get("zonas") or {}).get(zona or "")
    return (datos or {}).get("minimo_m2")


def opciones_de(producto: dict[str, Any], zona: str | None) -> list[dict[str, Any]]:
    """Las calidades con su precio, que desde el 29/9/2026 viven en la zona.

    Cada producto declara de que tabla come y cada zona trae esa tabla con sus
    propios valores: el mismo material cuesta 42 en Quito y 55 en Guayaquil, sin
    formula que los relacione.
    """
    datos_zona = (configuracion()["zonas"] or {}).get(zona or "")
    if not datos_zona:
        return []
    tabla = producto.get("tabla_de_precios")
    return ((datos_zona.get("precios") or {}).get(tabla)) or []


def _opcion(producto: dict[str, Any], zona: str | None,
            garantia_anios: int | None) -> dict[str, Any] | None:
    if garantia_anios is None:
        return None
    for opcion in opciones_de(producto, zona):
        if opcion["garantia_anios"] == garantia_anios:
            return opcion
    return None


def garantias_disponibles(id_producto: str, zona: str | None = None) -> list[int]:
    """Las garantias del producto. Son las mismas en todas las zonas: si no se
    sabe la zona, alcanza con mirar cualquiera que se atienda."""
    producto = _producto(id_producto)
    if not producto:
        return []
    if zona is None:
        zona = next((z for z, d in configuracion()["zonas"].items() if d.get("atiende")), None)
    return [o["garantia_anios"] for o in opciones_de(producto, zona)]


@dataclass
class Calidad:
    """Una de las calidades de un producto, con su precio por m2."""

    garantia_anios: int
    precio_normal: float
    precio_especial: float | None
    vida_util_anios: list[int] | None = None
    vida_util_hasta_anios: int | None = None


@dataclass
class Precios:
    """Los precios por m2 de un producto. No hay total: el cliente pidio que el
    agente diga cuanto vale el metro y nada mas."""

    puede_informar: bool
    motivo: str = ""
    producto: str = ""
    zona: str = ""
    # "exacto" o "desde": con seguridad arquitectonica el grosor lo define un
    # asesor, asi que el precio es un piso.
    tipo: str = "exacto"
    calidades: list[Calidad] = field(default_factory=list)
    minimo_m2: float | None = None
    descuento_pago_contado: int | None = None
    incluye: list[str] = field(default_factory=list)
    datos_faltantes: list[str] = field(default_factory=list)


def _especial_vigente(cfg: dict[str, Any], hoy: date | None = None) -> bool:
    """Si la promocion del mes sigue en pie.

    Sin fecha declarada no hay promocion: se cotiza al precio normal, que es el
    mas alto. Quedarse corto y que el asesor tenga que subir el numero es peor
    que arrancar arriba y que pueda mejorarlo.
    """
    vigencia = cfg.get("vigencia_precio_especial")
    if not vigencia:
        return False
    ahora = hoy or datetime.now(ZoneInfo("America/Guayaquil")).date()
    return f"{ahora.year:04d}-{ahora.month:02d}" == str(vigencia)


def informar_precios(
    id_producto: str, zona: str | None, hoy: date | None = None
) -> Precios:
    """Los precios por m2, sin cuentas.

    Es lo que reemplazo a cotizar un total: el cliente pidio dar el valor del
    metro cuadrado y que el calculo lo haga el asesor en la visita. Los metros
    se siguen relevando —hacen falta para el minimo de instalacion y para que
    el vendedor sepa el tamaño del trabajo— pero no se multiplican.
    """
    cfg = configuracion()

    producto = _producto(id_producto)
    if producto is None:
        return Precios(False, motivo=f"no existe el producto {id_producto!r}")

    if producto["cotizable"] == "ninguno":
        return Precios(
            False,
            motivo=producto.get("sin_precio_por_chat") or "este producto lo cotiza un asesor",
            producto=producto["nombre"],
            datos_faltantes=list(producto.get("datos_requeridos") or []),
        )

    # La zona define el precio Y el minimo: desde el 29/9/2026 cada una tiene su
    # propia tabla, sin formula que las relacione. Sin zona no hay precio que
    # dar: antes se podia porque el precio de lista era uno solo.
    datos_zona = cfg["zonas"].get(zona) if zona else None
    if zona and datos_zona is None:
        return Precios(False, motivo=f"zona desconocida: {zona!r}")
    if datos_zona is None:
        return Precios(
            False,
            motivo="sin la ciudad no se puede dar el precio: cada zona tiene el suyo",
            producto=producto["nombre"],
            datos_faltantes=["zona"],
        )
    if datos_zona is not None and not datos_zona["atiende"]:
        return Precios(
            False,
            motivo=f"no se atiende en {datos_zona['nombre'].lower()}",
            producto=producto["nombre"],
            zona=zona or "",
        )

    # El precio especial es una promocion del mes. Vencida, no se ofrece: un
    # agente prometiendo en octubre el precio de septiembre deja a la empresa
    # teniendo que sostenerlo o desdecirse delante del cliente.
    especial_vigente = _especial_vigente(cfg, hoy)

    # Cada producto dice de que tabla de la zona come: control solar y privacidad
    # comparten una, seguridad tiene la suya. Es como los agrupa el cliente en su
    # lista, y evita repetir cinco veces los mismos numeros.
    tabla = producto.get("tabla_de_precios")
    opciones = ((datos_zona.get("precios") or {}).get(tabla)) or []

    # Seguridad no tiene precio normal ni especial sino un piso —`precio_desde`—
    # porque el nivel lo define un asesor. Es la misma forma con otro nombre.
    calidades = [
        Calidad(
            garantia_anios=o["garantia_anios"],
            precio_normal=o.get("precio_normal", o.get("precio_desde")),
            precio_especial=o.get("precio_especial") if especial_vigente else None,
            vida_util_anios=o.get("vida_util_anios"),
            vida_util_hasta_anios=o.get("vida_util_hasta_anios"),
        )
        for o in opciones
        if o.get("precio_normal") is not None or o.get("precio_desde") is not None
    ]

    return Precios(
        puede_informar=bool(calidades),
        producto=producto["nombre"],
        zona=zona or "",
        tipo=producto["cotizable"],
        calidades=calidades,
        minimo_m2=datos_zona.get("minimo_m2"),
        descuento_pago_contado=cfg.get("descuento_efectivo_transferencia"),
        incluye=list(cfg.get("incluye") or []),
        motivo="" if calidades else "el producto no tiene precios cargados",
    )


def cotizar(
    id_producto: str,
    metros: float | None,
    zona: str | None,
    garantia_anios: int | None = None,
    hoy: date | None = None,
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
    opcion = _opcion(producto, zona, garantia_anios)
    if opcion is None:
        opciones = opciones_de(producto, zona)
        if len(opciones) == 1:
            opcion = opciones[0]
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
        # El especial es la promoción del mes. Vencida, rige el normal.
        #
        # Esta estimación es para el vendedor, no para el cliente: el agente ya
        # no da totales. Que use el mismo precio que el agente informó es lo que
        # hace que el número del CRM y el de la conversación cuenten la misma
        # historia.
        if _especial_vigente(cfg, hoy) and opcion.get("precio_especial") is not None:
            base = opcion["precio_especial"]
        else:
            base = opcion["precio_normal"]
        tipo = "exacto"

    # Ya no hay recargo que sumar: el precio de la zona ES el precio.
    precio_m2 = base

    return Cotizacion(
        puede_cotizar=True,
        producto=producto["nombre"],
        zona=zona,
        metros=metros,
        tipo=tipo,
        precio_m2=precio_m2,
        subtotal=precio_m2 * metros,
        garantia_anios=garantia_anios,
        descuento_pago_contado=cfg["descuento_efectivo_transferencia"],
        incluye=list(cfg["incluye"]),
    )
