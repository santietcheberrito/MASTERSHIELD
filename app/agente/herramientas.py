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
from app.precios import PRODUCTO_POR_OBJETIVO, informar_precios
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

    # El nombre sube igual que el telefono, y por la misma razon: es lo que
    # Kommo usa para el contacto y para el titulo del lead. El del perfil de
    # WhatsApp es un valor inicial —a veces un apodo, a veces el nombre de un
    # negocio—; el que la persona dice cuando se le pregunta, gana.
    nombre_dicho = str(valor) if campo == "nombre" else None

    datos = await db.valor(
        """
        UPDATE conversaciones SET
            datos    = datos || $2::jsonb,
            telefono = COALESCE($3, telefono),
            nombre   = COALESCE($4, nombre)
        WHERE id = $1
        RETURNING datos
        """,
        conversacion_id,
        {campo: valor},
        normalizado,
        nombre_dicho,
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
# consultar_precio
# ---------------------------------------------------------------------------



async def consultar_precio(
    conversacion_id: int,
    zona: str | None = None,
) -> dict[str, Any]:
    """Los precios por m2 del producto que el cliente necesita.

    No hace cuentas y no da totales. El cliente pidio expresamente que el
    agente informe cuanto vale el metro cuadrado y que el calculo lo haga el
    asesor en la visita tecnica, donde ademas se toman las medidas exactas.

    Los metros se siguen relevando: hacen falta para el minimo de instalacion y
    para que el vendedor sepa el tamaño del trabajo antes de llamar.
    """
    datos = await db.valor("SELECT datos FROM conversaciones WHERE id = $1", conversacion_id) or {}

    linea = datos.get("linea")
    objetivo = datos.get("objetivo")
    zona = zona or datos.get("zona")

    if not linea:
        return {"puede_informar": False, "falta": ["linea"],
                "mensaje": "todavia no se sabe si es arquitectonico o vehicular"}

    id_producto = PRODUCTO_POR_OBJETIVO.get((linea, objetivo))
    if id_producto is None:
        return {"puede_informar": False, "falta": ["objetivo"],
                "mensaje": "falta saber que necesita resolver: control solar, privacidad o seguridad"}

    r = informar_precios(id_producto, zona)

    if not r.puede_informar:
        respuesta: dict[str, Any] = {
            "puede_informar": False,
            "producto": r.producto,
            "mensaje": r.motivo,
        }
        if r.datos_faltantes:
            respuesta["falta"] = r.datos_faltantes
        return respuesta

    calidades = [
        {
            "garantia_anios": c.garantia_anios,
            "precio_normal_m2_sin_iva": c.precio_normal,
            "precio_especial_m2_sin_iva": c.precio_especial,
            "vida_util_anios": c.vida_util_anios,
        }
        for c in r.calidades
    ]

    respuesta = {
        "puede_informar": True,
        "producto": r.producto,
        "tipo": r.tipo,  # "exacto" o "desde"
        "calidades": calidades,
        "descuento_pago_contado_pct": r.descuento_pago_contado,
        "incluye": r.incluye,
        "como_decirlo": (
            "Son precios POR METRO CUADRADO y SIN IVA: se dicen con la frase "
            "'mas IVA'. NO multiplique por los metros, NO de un total y NO le "
            "sume el IVA. El calculo lo hace el asesor en la visita, con las "
            "medidas exactas."
        ),
    }

    # El minimo es lo primero que hay que decir en provincias, donde es cuatro
    # veces mas alto y decide si la persona es cliente o no.
    if r.minimo_m2 is not None:
        respuesta["minimo_m2_de_la_zona"] = r.minimo_m2
        metros = datos.get("metros_cuadrados")
        if metros is not None and metros < r.minimo_m2:
            respuesta["no_llega_al_minimo"] = True
            respuesta["metros_del_pedido"] = metros
            respuesta["sugerencia"] = (
                "preguntar si hay otro sector para sumar y llegar al minimo, "
                "en vez de cortar la conversacion"
            )
    if r.recargo_m2:
        respuesta["recargo_m2_por_la_zona"] = r.recargo_m2

    return respuesta


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
# cerrar_sin_responder
# ---------------------------------------------------------------------------

async def cerrar_sin_responder(conversacion_id: int, motivo: str) -> dict[str, Any]:
    """El agente decide que no hay nada que contestar, y eso queda registrado.

    Existe para un caso puntual: un asesor estuvo conversando a mano, la pausa
    vencio, y quedo un mensaje del cliente sin contestar. "Sin contestar" no
    alcanza como criterio —un "gracias, perfecto" no es una consulta— y esa
    diferencia no se puede escribir en SQL. La decide el agente.

    Sin esto, la unica forma de no responder era devolver texto vacio, que es
    indistinguible de un turno que fallo.
    """
    await db.ejecutar(
        "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
        "VALUES ($1, 'silencio_deliberado', 'ok', $2)",
        conversacion_id,
        {"motivo": (motivo or "")[:LARGO_MAXIMO_TEXTO]},
    )
    logger.info("el agente decide no contestar | conversacion=%s: %s",
                conversacion_id, motivo)
    return {"sin_respuesta": True, "motivo": motivo}


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
            "nombre": "consultar_precio",
            "descripcion": (
                "Te da el precio POR METRO CUADRADO del material que necesita el "
                "cliente, con sus calidades. Usala SIEMPRE antes de mencionar "
                "cualquier numero: no inventes precios ni los saques de memoria.\n\n"
                "No calcula totales a proposito. Nunca multipliques por los metros "
                "ni des un valor final: el calculo lo hace el asesor en la visita, "
                "con las medidas exactas tomadas en el lugar."
            ),
            "esquema": {
                "type": "object",
                "properties": {
                    "zona": {
                        "type": "string",
                        "enum": ["quito_y_valles", "otra_ciudad", "fuera_del_pais"],
                        "description": "Solo si querés consultar por una zona distinta a la guardada",
                    },
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
        {
            "nombre": "cerrar_sin_responder",
            "descripcion": (
                "Termina el turno sin mandar ningun mensaje. Usala cuando lo ultimo "
                "que dijo el cliente no pide respuesta —un 'gracias', un 'perfecto', "
                "un 'dale'— y contestar seria hablar por hablar. Tambien cuando un "
                "asesor ya se hizo cargo del tema y agregar algo seria pisarlo. "
                "En la duda, contesta: el silencio solo es correcto cuando es obvio."
            ),
            "esquema": {
                "type": "object",
                "properties": {
                    "motivo": {
                        "type": "string",
                        "description": "Por que no hace falta contestar, en una linea",
                    },
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
        if nombre == "consultar_precio":
            return await consultar_precio(conversacion_id, zona=argumentos.get("zona"))
        if nombre == "finalizar_calificacion":
            return await finalizar_calificacion(conversacion_id)
        if nombre == "escalar_a_humano":
            # `estado` no se expone al modelo a proposito: 'cerrada' la decide
            # el codigo, no el agente.
            return await escalar_a_humano(conversacion_id, argumentos["motivo"])
        if nombre == "cerrar_sin_responder":
            return await cerrar_sin_responder(conversacion_id, argumentos["motivo"])
        return {"error": f"no existe la herramienta {nombre!r}"}
    except KeyError as e:
        return {"error": f"falta el argumento {e}"}
    except Exception as e:
        logger.exception("fallo la herramienta %s | conversacion=%s", nombre, conversacion_id)
        return {"error": f"{type(e).__name__}: {e}"}
