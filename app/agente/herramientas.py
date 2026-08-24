"""Las herramientas del agente.

Cada una tiene dos partes: la definicion que ve el modelo y el ejecutor que
corre en Python. La separacion importa — todo lo que sea una regla de negocio
(el minimo de venta, el precio, que campos son obligatorios) se decide aca y no
en el prompt, para que el modelo no pueda hacer una excepcion porque el cliente
insistio.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app import db
from app.config import obtener_settings
from app.precios import cotizar, garantias_disponibles
from app.telefono import normalizar

logger = logging.getLogger(__name__)

RUTA_CALIFICACION = Path(__file__).resolve().parent.parent.parent / "config" / "calificacion.yaml"


@lru_cache
def calificacion() -> dict[str, Any]:
    return yaml.safe_load(RUTA_CALIFICACION.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# guardar_dato
# ---------------------------------------------------------------------------

async def guardar_dato(conversacion_id: int, campo: str, valor: Any) -> dict[str, Any]:
    """Persiste un dato apenas el cliente lo menciona, no al final.

    Si la conversacion se corta a la mitad, lo relevado hasta ahi ya esta
    guardado y el vendedor tiene algo con que llamar.
    """
    campos = calificacion()["campos"]
    definicion = campos.get(campo)
    if definicion is None:
        return {
            "error": f"el campo {campo!r} no existe",
            "campos_validos": sorted(campos),
        }

    valores = definicion.get("valores")
    if valores is not None and valor not in valores:
        return {
            "error": f"valor invalido para {campo!r}",
            "valores_validos": valores,
        }

    if definicion.get("tipo") == "numero":
        try:
            valor = float(valor)
        except (TypeError, ValueError):
            return {"error": f"{campo!r} tiene que ser un numero"}
        if valor <= 0:
            return {"error": f"{campo!r} tiene que ser mayor que cero"}

    # El telefono ademas sube a su propia columna, normalizado. Kommo busca el
    # contacto por telefono y esa columna es la que tiene indice: si queda solo
    # dentro del jsonb, la sincronizacion no lo encuentra y crea un contacto
    # nuevo por cada conversacion. El crudo se guarda igual en `datos`, para que
    # se pueda revisar si la normalizacion no pudo con el.
    normalizado = None
    if campo == "telefono":
        normalizado = normalizar(str(valor), obtener_settings().prefijo_telefonico)
        if normalizado is None:
            logger.warning(
                "telefono que no se pudo normalizar | conversacion=%s %r",
                conversacion_id,
                valor,
            )

    datos = await db.valor(
        """
        UPDATE conversaciones SET
            datos    = datos || $2::jsonb,
            telefono = COALESCE($3, telefono)
        WHERE id = $1
        RETURNING datos
        """,
        conversacion_id,
        {campo: valor},
        normalizado,
    )
    if datos is None:
        # El UPDATE no toco ninguna fila. Sin este chequeo la herramienta
        # informaria "guardado" de algo que no se guardo en ningun lado.
        logger.error("guardar_dato sobre una conversacion inexistente: %s", conversacion_id)
        return {"error": "no se pudo guardar: la conversacion no existe"}

    logger.info("dato guardado | conversacion=%s %s=%r", conversacion_id, campo, valor)
    return {"guardado": True, "campo": campo, "valor": valor, "datos_actuales": datos}


# ---------------------------------------------------------------------------
# calcular_precio
# ---------------------------------------------------------------------------

PRODUCTO_POR_OBJETIVO = {
    ("arquitectonico", "control_solar"): "control_solar_arquitectonico",
    ("arquitectonico", "privacidad"): "privacidad_arquitectonica",
    ("arquitectonico", "seguridad"): "seguridad_arquitectonica",
    ("vehicular", "seguridad"): "seguridad_vehicular",
}


async def calcular_precio(
    conversacion_id: int,
    metros_cuadrados: float | None = None,
    zona: str | None = None,
    garantia_anios: int | None = None,
) -> dict[str, Any]:
    """Cotiza con lo que ya esta relevado, salvo que se pase algo distinto.

    Los argumentos son opcionales a proposito: por defecto sale de
    `conversaciones.datos`, para que el numero sea consistente con lo que el
    cliente dijo. Pasarlos permite responder "¿y si fueran 30 metros?" sin
    pisar lo guardado.
    """
    datos = await db.valor("SELECT datos FROM conversaciones WHERE id = $1", conversacion_id) or {}

    linea = datos.get("linea")
    objetivo = datos.get("objetivo")
    metros = metros_cuadrados if metros_cuadrados is not None else datos.get("metros_cuadrados")
    zona = zona or datos.get("zona")
    garantia = garantia_anios if garantia_anios is not None else datos.get("garantia_anios")

    if not linea:
        return {"puede_cotizar": False, "falta": ["linea"],
                "mensaje": "todavia no se sabe si es arquitectonico o vehicular"}

    id_producto = PRODUCTO_POR_OBJETIVO.get((linea, objetivo))
    if id_producto is None:
        return {"puede_cotizar": False, "falta": ["objetivo"],
                "mensaje": "falta saber que necesita resolver: control solar, privacidad o seguridad"}

    resultado = cotizar(id_producto, metros, zona, garantia)

    if not resultado.puede_cotizar:
        respuesta: dict[str, Any] = {
            "puede_cotizar": False,
            "producto": resultado.producto,
            "mensaje": resultado.motivo,
        }
        if resultado.datos_faltantes:
            respuesta["falta"] = resultado.datos_faltantes
        if resultado.minimo_m2 is not None:
            respuesta["minimo_m2"] = resultado.minimo_m2
            respuesta["metros_del_pedido"] = resultado.metros
            respuesta["sugerencia"] = (
                "preguntar si hay otro sector para sumar y llegar al minimo, "
                "en vez de cortar la conversacion"
            )
        if resultado.datos_faltantes == ["garantia_anios"]:
            respuesta["garantias_disponibles"] = garantias_disponibles(id_producto)
        return respuesta

    return {
        "puede_cotizar": True,
        "producto": resultado.producto,
        "tipo": resultado.tipo,  # "exacto" o "desde"
        "metros": resultado.metros,
        "precio_m2_sin_iva": resultado.precio_m2,
        "subtotal_sin_iva": resultado.subtotal,
        "garantia_anios": resultado.garantia_anios,
        "descuento_pago_contado_pct": resultado.descuento_pago_contado,
        "incluye": resultado.incluye,
        "como_decirlo": (
            "El precio se dice SIN IVA, con la frase 'mas IVA'. "
            "No sumarle el IVA ni calcular el total con impuestos."
        ),
    }


# ---------------------------------------------------------------------------
# finalizar_calificacion
# ---------------------------------------------------------------------------

async def finalizar_calificacion(conversacion_id: int) -> dict[str, Any]:
    """Cierra el relevamiento. Se niega si falta algo sin lo cual no sirve.

    El scoring y la carga a Kommo se enganchan aca (sesiones 5 y 3). Por ahora
    marca la conversacion y deja el evento para auditoria.
    """
    datos = await db.valor("SELECT datos FROM conversaciones WHERE id = $1", conversacion_id) or {}

    linea = datos.get("linea")
    requeridos_por_linea = calificacion()["requeridos_por_linea"]
    requeridos = requeridos_por_linea.get(linea)
    if requeridos is None:
        return {"finalizada": False, "falta": ["linea"],
                "mensaje": "sin saber la linea no se puede cerrar"}

    faltan = [c for c in requeridos if datos.get(c) in (None, "")]
    if faltan:
        return {
            "finalizada": False,
            "falta": faltan,
            "mensaje": "faltan datos sin los cuales el vendedor no puede llamar",
        }

    if datos.get("zona") == "fuera_del_pais":
        return await escalar_a_humano(
            conversacion_id, "consulta desde fuera de Ecuador", estado="cerrada"
        ) | {"finalizada": False, "mensaje": "fuera del area de trabajo"}

    await db.ejecutar(
        "UPDATE conversaciones SET estado = 'calificada' WHERE id = $1", conversacion_id
    )
    await db.ejecutar(
        "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
        "VALUES ($1, 'calificacion_finalizada', 'ok', $2)",
        conversacion_id,
        {"datos": datos},
    )
    logger.info("calificacion finalizada | conversacion=%s", conversacion_id)

    # TODO sesion 5: scoring desde config/calificacion.yaml.
    # TODO sesion 3: sincronizacion con Kommo.
    return {
        "finalizada": True,
        "datos": datos,
        "mensaje": "calificacion cerrada. Un asesor MS va a llamar para coordinar la visita",
    }


# ---------------------------------------------------------------------------
# escalar_a_humano
# ---------------------------------------------------------------------------

async def escalar_a_humano(
    conversacion_id: int, motivo: str, estado: str = "derivada"
) -> dict[str, Any]:
    """Pausa al agente y deja registro para que lo tome una persona."""
    await db.ejecutar(
        "UPDATE conversaciones SET estado = $2 WHERE id = $1", conversacion_id, estado
    )
    await db.ejecutar(
        "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
        "VALUES ($1, 'escalado_a_humano', 'ok', $2)",
        conversacion_id,
        {"motivo": motivo},
    )
    logger.info("escalado a humano | conversacion=%s motivo=%s", conversacion_id, motivo)
    # TODO: notificar al equipo. Falta definir a quien y por que medio.
    return {"escalado": True, "motivo": motivo, "estado": estado}


# ---------------------------------------------------------------------------
# Definiciones que ve el modelo
# ---------------------------------------------------------------------------

def definiciones() -> list[dict[str, Any]]:
    """Se generan desde `config/calificacion.yaml` para que no se desincronicen.

    Si alguien agrega un campo al YAML, el modelo lo ve en la proxima llamada
    sin que haya que tocar codigo.
    """
    campos = calificacion()["campos"]
    descripcion_campos = "\n".join(
        f"- {nombre}: {d['descripcion']}"
        + (f" (valores: {', '.join(str(v) for v in d['valores'])})" if d.get("valores") else "")
        for nombre, d in campos.items()
    )

    return [
        {
            "name": "guardar_dato",
            "description": (
                "Guarda un dato del cliente apenas lo menciona, sin esperar al final "
                "de la conversacion. Llamala cada vez que el cliente diga algo nuevo, "
                "aunque sea de pasada.\n\nCampos:\n" + descripcion_campos
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "campo": {"type": "string", "enum": sorted(campos)},
                    "valor": {
                        "description": "El valor. Para campos con lista de valores, uno de esos.",
                    },
                },
                "required": ["campo", "valor"],
            },
        },
        {
            "name": "calcular_precio",
            "description": (
                "Calcula el precio del trabajo. Usala SIEMPRE que haya que dar un "
                "numero: nunca hagas la cuenta vos. Tambien aplica el minimo de venta "
                "y te dice si el pedido no llega.\n\n"
                "Por defecto usa lo que ya esta guardado de la conversacion. Pasa "
                "argumentos solo para responder un supuesto ('¿y si fueran 30 metros?')."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "metros_cuadrados": {
                        "type": "number",
                        "description": "Solo para simular otra cantidad distinta a la guardada",
                    },
                    "zona": {
                        "type": "string",
                        "enum": ["quito_y_valles", "otra_ciudad", "fuera_del_pais"],
                    },
                    "garantia_anios": {"type": "integer", "enum": [10, 5]},
                },
                "required": [],
            },
        },
        {
            "name": "finalizar_calificacion",
            "description": (
                "Cierra el relevamiento cuando ya tenes todo lo necesario para que un "
                "asesor llame. Si falta algo, te lo dice y seguis preguntando. "
                "No la llames antes de tener el telefono."
            ),
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "escalar_a_humano",
            "description": (
                "Pasa la conversacion a una persona. Usala si el cliente lo pide, si "
                "se enoja, si pregunta algo que no esta en tu informacion, o si la "
                "consulta no es de venta (reclamo, garantia de un trabajo hecho, "
                "facturacion)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "motivo": {"type": "string", "description": "Por que se deriva, en una linea"},
                },
                "required": ["motivo"],
            },
        },
    ]


async def ejecutar(nombre: str, conversacion_id: int, argumentos: dict[str, Any]) -> dict[str, Any]:
    """Despacha una llamada del modelo. Nunca propaga excepciones.

    Si una herramienta falla, el modelo tiene que enterarse y poder seguir la
    conversacion, no cortarla. Un turno que explota deja al cliente sin
    respuesta, que es lo peor que puede pasar.
    """
    try:
        if nombre == "guardar_dato":
            return await guardar_dato(
                conversacion_id, argumentos["campo"], argumentos["valor"]
            )
        if nombre == "calcular_precio":
            return await calcular_precio(
                conversacion_id,
                metros_cuadrados=argumentos.get("metros_cuadrados"),
                zona=argumentos.get("zona"),
                garantia_anios=argumentos.get("garantia_anios"),
            )
        if nombre == "finalizar_calificacion":
            return await finalizar_calificacion(conversacion_id)
        if nombre == "escalar_a_humano":
            # `estado` no se expone al modelo a proposito: 'cerrada' la decide
            # el codigo, no el agente.
            return await escalar_a_humano(conversacion_id, argumentos["motivo"])
        return {"error": f"no existe la herramienta {nombre!r}"}
    except KeyError as e:
        return {"error": f"falta el argumento {e}"}
    except Exception as e:
        logger.exception("fallo la herramienta %s | conversacion=%s", nombre, conversacion_id)
        return {"error": f"{type(e).__name__}: {e}"}
