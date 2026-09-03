"""Las herramientas del agente.

Cada una tiene dos partes: la definicion que ve el modelo y el ejecutor que
corre en Python. La separacion importa — todo lo que sea una regla de negocio
(el minimo de venta, el precio, que campos son obligatorios) se decide aca y no
en el prompt, para que el modelo no pueda hacer una excepcion porque el cliente
insistio.
"""

from __future__ import annotations

import logging
from typing import Any

from app import db
from app.calificacion import calificacion
from app.config import obtener_settings
from app.crm.sincronizacion import sincronizar
from app.precios import PRODUCTO_POR_OBJETIVO, cotizar, garantias_disponibles
from app.telefono import normalizar

logger = logging.getLogger(__name__)

# Los campos de texto libre terminan renderizados DENTRO del prompt del sistema
# ("lo que ya sabe de esta conversacion"), asi que son un vector de inyeccion de
# segundo orden: el cliente escribe instrucciones, se persisten, y en el turno
# siguiente el modelo las lee como si vinieran del sistema.
#
# Hoy el modelo se niega a guardar payloads obvios, pero eso es criterio suyo y
# no una garantia. El limite de largo y el aplastado de saltos de linea si lo
# son: sin saltos no se pueden fabricar encabezados falsos, y con 200 caracteres
# no entra una instruccion elaborada.
LARGO_MAXIMO_TEXTO = 200

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

    if definicion.get("tipo") == "texto":
        valor = " ".join(str(valor).split())[:LARGO_MAXIMO_TEXTO]

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
    resultado: dict[str, Any] = {"guardado": True, "campo": campo, "valor": valor, "datos_actuales": datos}
    if campo == "telefono" and normalizado is not None:
        resultado["telefono_normalizado"] = normalizado
    return resultado


# ---------------------------------------------------------------------------
# calcular_precio
# ---------------------------------------------------------------------------



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
    fila = await db.consultar_una(
        "SELECT datos, telefono, estado FROM conversaciones WHERE id = $1", conversacion_id
    )
    datos = (fila["datos"] if fila else None) or {}
    telefono = fila["telefono"] if fila else None

    # Ya estaba cerrada. Pasa siempre igual: el agente cierra, manda la
    # confirmacion, el cliente contesta "si, gracias" y el agente vuelve a
    # llamar aca. Sin esto se sincronizaba de nuevo —dos notas identicas en el
    # lead— y el mensaje de abajo lo hacia repetir la despedida entera, que es
    # justo lo que el prompt le prohibe.
    if fila and fila["estado"] == "calificada":
        logger.info("ya estaba calificada, no se cierra de nuevo | conversacion=%s",
                    conversacion_id)
        return {
            "finalizada": True,
            "ya_estaba_cerrada": True,
            "mensaje": (
                "Esta conversacion ya se cerro y el asesor ya tiene los datos. "
                "No repita la confirmacion ni el numero ni el horario: "
                "despidase con una linea corta y nada mas."
            ),
        }

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

    # El telefono esta en `datos` pero no se pudo normalizar. Sin numero valido
    # la calificacion no sirve: el asesor llama por telefono. Que el cliente lo
    # confirme antes de cerrar, en vez de descubrirlo cuando alguien marque.
    if telefono is None:
        return {
            "finalizada": False,
            "falta": ["telefono"],
            "mensaje": (
                f"el numero {datos.get('telefono')!r} no parece un telefono "
                "ecuatoriano valido. Pidale que lo confirme."
            ),
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

    # Si el CRM falla, la conversacion no se pierde: queda marcada para
    # reintento y el agente cierra igual. El cliente no se tiene que enterar de
    # que una integracion esta caida.
    await sincronizar(conversacion_id)

    return {
        "finalizada": True,
        "datos": datos,
        "telefono_confirmado": telefono,
        "disponibilidad": datos.get("disponibilidad"),
        "mensaje": (
            "Calificacion cerrada. Al despedirse, envie un unico mensaje de confirmacion "
            f"indicando que un asesor MS lo llamara ({datos.get('disponibilidad') or 'proximamente'}) "
            # El numero se le repite como lo escribio la persona, no en E.164: el
            # normalizado es para la base y para Kommo. Decirle "+59321234567" a
            # alguien que escribio "2 1234567" suena a maquina leyendo un campo.
            f"al numero {datos.get('telefono') or telefono}, tal como esta escrito aca, "
            "y pida al cliente confirmar si el numero y el horario son correctos."
        ),
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

    # Tambien sube al CRM: una derivacion tiene que aparecer en el tablero, o el
    # equipo no se entera de que alguien esta esperando.
    await sincronizar(conversacion_id)
    # TODO: ademas notificar al equipo. Falta definir a quien y por que medio.
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
            "nombre": "guardar_dato",
            "descripcion": (
                "Guarda un dato del cliente apenas lo menciona, sin esperar al final "
                "de la conversacion. Llamala cada vez que el cliente diga algo nuevo, "
                "aunque sea de pasada.\n\nCampos:\n" + descripcion_campos
            ),
            "esquema": {
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
            "nombre": "calcular_precio",
            "descripcion": (
                "Calcula el precio del trabajo. Usala SIEMPRE que haya que dar un "
                "numero: nunca hagas la cuenta vos. Tambien aplica el minimo de venta "
                "y te dice si el pedido no llega.\n\n"
                "Por defecto usa lo que ya esta guardado de la conversacion. Pasa "
                "argumentos solo para responder un supuesto ('¿y si fueran 30 metros?')."
            ),
            "esquema": {
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
            "nombre": "finalizar_calificacion",
            "descripcion": (
                "Cierra el relevamiento cuando ya tenes todo lo necesario para que un "
                "asesor llame. Si falta algo, te lo dice y seguis preguntando. "
                "No la llames antes de tener el telefono."
            ),
            "esquema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "nombre": "escalar_a_humano",
            "descripcion": (
                "Pasa la conversacion a una persona. Usala si el cliente lo pide, si "
                "se enoja, si pregunta algo que no esta en tu informacion, o si la "
                "consulta no es de venta (reclamo, garantia de un trabajo hecho, "
                "facturacion)."
            ),
            "esquema": {
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
