"""Las herramientas del agente.

Cada una tiene dos partes: la definicion que ve el modelo y el ejecutor que
corre en Python. La separacion importa — todo lo que sea una regla de negocio
(el minimo de venta, el precio, que campos son obligatorios) se decide aca y no
en el prompt, para que el modelo no pueda hacer una excepcion porque el cliente
insistio.

Desde el 15/9/2026 ninguna herramienta escribe texto de la conversacion: eso lo
hace solo el modelo, con las respuestas por situacion que recibe de la base
(app/contexto.py). Lo unico que el codigo manda tal cual son bloques que el
modelo nunca escribe: la lista de precios, las fichas y el video.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app import db, fichas, textos, ubicaciones
from app.calificacion import calificacion
from app.config import obtener_settings
from app.crm.sincronizacion import sincronizar
from app.precios import informar_precios, producto_para_cotizar
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


def _comparable(valor: Any) -> str:
    return str(valor).strip().lower().replace("ñ", "n")


# ---------------------------------------------------------------------------
# Lo que falta, en orden
# ---------------------------------------------------------------------------

# Como se nombra cada paso cuando el agente tiene que preguntarlo.
QUE_PREGUNTAR = {
    "nombre": "el nombre de la persona, y la ciudad en la misma pregunta si tampoco la dijo",
    "linea": "que necesita: si es para vidrios de un inmueble o para un vehiculo",
    "objetivo": "que quiere resolver: calor, privacidad o seguridad",
    "referencia": (
        "una referencia del tamaño del trabajo, en metros aproximados o con fotos, "
        "diciendo el minimo de instalacion de su ciudad"
    ),
    "zona": "la ciudad donde se haria la instalacion",
    "superficie": "si el vidrio es de ventanas o de un techo, como una pergola o una claraboya",
    "modelo_vehiculo": "el modelo del vehiculo",
}

# Como pide precio alguien por WhatsApp. Contar mensajes y no confiar en que el
# modelo "note" la insistencia: la prueba de Pablo mostro que no la nota.
_PIDE_PRECIO = re.compile(
    r"\b(precios?|cu[aá]nto\s+(?:cuesta|sale|vale|cobran|es)|costos?|valor(?:es)?|"
    r"cotiza\w*|tarifas?)\b",
    re.IGNORECASE,
)
# Desde cuantos mensajes pidiendo precio la persona pasa a modo rapido.
VECES_PARA_MODO_RAPIDO = 2


def pide_precio(texto: str | None) -> bool:
    return bool(_PIDE_PRECIO.search(texto or ""))


async def esta_apurada(conversacion_id: int) -> bool:
    """Si la persona pidio el precio en dos mensajes o mas.

    Una vez que entra en modo rapido se queda: los mensajes no se borran.
    """
    filas = await db.consultar(
        "SELECT contenido FROM mensajes WHERE conversacion_id = $1 AND rol = 'cliente'",
        conversacion_id,
    )
    return sum(1 for f in filas if pide_precio(f["contenido"])) >= VECES_PARA_MODO_RAPIDO


def requisitos_del_precio(datos: dict[str, Any], apurada: bool) -> list[str]:
    """Lo que tiene que estar para dar el precio, en el orden en que se pide.

    Lo usan `consultar_precio` para negarse y app/contexto.py para saber en que
    paso esta la conversacion: si los dos no leyeran lo mismo, el agente podria
    estar en un paso que la herramienta no reconoce.
    """
    antes = list(calificacion()["antes_del_precio_rapido" if apurada else "antes_del_precio"])
    if datos.get("linea") == "vehicular":
        # No se cotiza por chat: ni el objetivo ni los metros cambian eso.
        antes = [c for c in antes if c not in ("objetivo", "referencia")]
    # Control solar son dos productos: ventanas y techos. La superficie es parte
    # del pedido y va antes de los metros (15/9/2026). En modo rapido no: la
    # define el asesor.
    if not apurada and datos.get("objetivo") == "control_solar" and "objetivo" in antes:
        i = antes.index("objetivo") + 1
        antes = [*antes[:i], "superficie", *antes[i:]]
    return antes


_MANDO_FOTOS = """
    SELECT 1 FROM mensajes
    WHERE conversacion_id = $1 AND rol = 'cliente' AND tipo IN ('imagen', 'documento')
    LIMIT 1
"""


async def faltantes(conversacion_id: int, datos: dict[str, Any], campos: list[str]) -> list[str]:
    """Los campos de `campos` que todavia no estan, en el mismo orden.

    `referencia` no es un campo guardado: se cumple con los metros o con una
    foto del cliente. La foto se busca en `mensajes` y no en `datos` porque es
    lo que de verdad llego, y se consulta solo si no hay metros.
    """
    faltan = []
    for campo in campos:
        if campo == "referencia":
            if datos.get("metros_cuadrados") in (None, "") and not await db.valor(
                    _MANDO_FOTOS, conversacion_id):
                faltan.append(campo)
        elif datos.get(campo) in (None, ""):
            faltan.append(campo)
    return faltan


# ---------------------------------------------------------------------------
# guardar_dato
# ---------------------------------------------------------------------------

def linea_deducida(campo: str, valor: Any, datos: dict[str, Any]) -> str | None:
    """La linea (inmueble o vehiculo) que se desprende de otro dato.

    El modelo tenia que guardarla aparte y con razonamiento "minimal" no lo
    hacia: Pablo dijo privacidad, ventanas de su casa y 13 m², y el precio se
    trababa porque faltaba la linea (15/9/2026). No se le pide mas: se deduce.
    """
    if datos.get("linea"):
        return None
    if campo == "aplicacion":
        return "vehicular" if valor == "vehiculo" else "arquitectonico"
    if campo == "modelo_vehiculo":
        return "vehicular"
    if campo == "superficie":
        return "arquitectonico"
    if campo == "objetivo" and valor in ("control_solar", "privacidad"):
        return "arquitectonico"
    return None


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
        # El modelo a veces manda "10" donde la lista dice 10, o "Mañana" donde
        # dice manana. Es el mismo dato: rechazarlo le cuesta una vuelta y a
        # veces no lo vuelve a intentar.
        valor = next((v for v in valores if _comparable(v) == _comparable(valor)), None)
        if valor is None:
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

    # La ciudad trae la zona puesta: son cinco, cada una con su minimo y su
    # tabla de precios, y saber que Gualaceo es zona verde es un dato duro, no
    # criterio del modelo (29/9/2026).
    if campo == "ciudad":
        zona = ubicaciones.zona_de(str(valor))
        if zona and zona != datos.get("zona"):
            datos = await db.valor(
                "UPDATE conversaciones SET datos = datos || $2::jsonb "
                "WHERE id = $1 RETURNING datos",
                conversacion_id, {"zona": zona},
            )
            logger.info("zona deducida de la ciudad | conversacion=%s %s -> %s",
                        conversacion_id, valor, zona)
        elif not zona:
            logger.warning("ciudad que no esta en la tabla de zonas | conversacion=%s %r",
                           conversacion_id, valor)

    linea = linea_deducida(campo, valor, datos)
    if linea:
        datos = await db.valor(
            "UPDATE conversaciones SET datos = datos || $2::jsonb WHERE id = $1 RETURNING datos",
            conversacion_id, {"linea": linea},
        )
        logger.info("linea deducida | conversacion=%s linea=%s", conversacion_id, linea)

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
    introduccion: str | None = None,
) -> dict[str, Any]:
    """Los precios por m2 del producto que el cliente necesita.

    No hace cuentas y no da totales: el agente informa cuanto vale el metro
    cuadrado y el calculo lo hace el asesor en la visita tecnica.

    **El precio va al final.** Mientras falte algo del orden que definio
    MasterShield la herramienta no lo da, aunque la persona lo pida primero:
    devuelve lo que falta, en ese orden, y el agente lo pregunta con la
    respuesta de la base que corresponde.

    Con los datos completos, la lista oficial sale tal cual antes del mensaje
    del modelo. El modelo nunca escribe un precio.
    """
    datos = await db.valor("SELECT datos FROM conversaciones WHERE id = $1", conversacion_id) or {}

    objetivo = datos.get("objetivo")
    zona = zona or datos.get("zona")

    apurada = await esta_apurada(conversacion_id)
    faltan = await faltantes(
        conversacion_id, {**datos, "zona": zona}, requisitos_del_precio(datos, apurada))
    if faltan:
        return {
            "puede_informar": False,
            "falta": faltan,
            "modo_rapido": apurada,
            "mensaje": (
                "Todavia no se dan los precios. Lo siguiente es preguntar "
                f"{QUE_PREGUNTAR.get(faltan[0], faltan[0])}. Si la persona pidio el "
                "precio, use la respuesta precio_sin_datos (o modo_rapido): la "
                "explicacion va una sola vez en la conversacion."
            ),
        }

    id_producto = producto_para_cotizar(datos)
    if id_producto is None:
        if objetivo == "control_solar":
            return {"puede_informar": False, "falta": ["superficie"],
                    "mensaje": "falta saber si es para ventanas o para un techo de vidrio"}
        return {"puede_informar": False, "falta": ["objetivo"],
                "mensaje": "falta saber que necesita resolver: calor, privacidad o seguridad"}

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
            "vida_util_hasta_anios": c.vida_util_hasta_anios,
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
            "sume el IVA."
        ),
    }

    # El minimo es lo primero que hay que decir en provincias, donde es cuatro
    # veces mas alto y decide si la persona es cliente o no.
    metros = datos.get("metros_cuadrados")
    if r.minimo_m2 is not None:
        respuesta["minimo_m2_de_la_zona"] = r.minimo_m2
        if metros is not None and metros < r.minimo_m2:
            respuesta["no_llega_al_minimo"] = True
            respuesta["metros_del_pedido"] = metros

    # Por debajo del minimo no se dan los precios: primero se ve si suma
    # superficie. Hasta el 16/9/2026 la lista salia igual y despues venia el
    # aviso de que no llegaba, que es darle un valor que todavia no le sirve y
    # despedirse; el cliente pidio conversarlo antes (Santiago, 16/9/2026).
    if respuesta.get("no_llega_al_minimo"):
        return {
            "puede_informar": False,
            "no_llega_al_minimo": True,
            "producto": r.producto,
            "minimo_m2_de_la_zona": r.minimo_m2,
            "metros_del_pedido": metros,
            "mensaje": (
                f"Todavia NO se dan los precios: el minimo en esa zona es de "
                f"{r.minimo_m2:g} m2 y el pedido es de {metros:g} m2. Use la respuesta "
                "no_llega_al_minimo y pregunte si suma otro ambiente. Si suma y llega "
                "al minimo, guarde los metros nuevos y vuelva a llamar a "
                "consultar_precio: ahi si salen los precios."
            ),
        }

    oferta = await db.valor(
        "SELECT respuesta FROM respuestas WHERE clave = 'dar_precios' AND activa") or ""
    respuesta["siguiente_paso"] = (
        "Despues de la lista, su mensaje es solo la oferta de la llamada, tal cual: "
        f"{oferta.strip()!r}. Ni precios ni lo que incluye: eso ya esta en la lista."
    )

    # Si esta misma lista ya le llego, no se repite: quien pregunta de nuevo por
    # un precio quiere el dato, no el bloque entero.
    if await textos.ya_enviado(conversacion_id, textos.lista_de_precios(respuesta)):
        respuesta["lista_ya_enviada"] = True
        respuesta["mensaje"] = (
            "La persona ya recibio la lista de precios con estos valores y no se "
            "vuelve a mandar. Si pregunta algo puntual, conteste con estos numeros."
        )
    else:
        respuesta["se_envia_lista"] = True
        respuesta["introduccion"] = " ".join(str(introduccion or "").split())[:LARGO_MAXIMO_TEXTO]
        respuesta["mensaje"] = (
            "Salen en este orden: su introduccion, la lista oficial y su mensaje. Su "
            "mensaje va despues de la lista: no la anuncie, no la comente y no repita "
            "ningun precio."
        )

    return respuesta


# ---------------------------------------------------------------------------
# enviar_ficha
# ---------------------------------------------------------------------------

async def enviar_ficha(
    conversacion_id: int, producto: str, introduccion: str | None = None
) -> dict[str, Any]:
    """Pide que salga la descripcion oficial de un producto.

    No manda nada: la ficha la manda el worker, tal cual, antes de lo que el
    modelo escriba. Aca solo se valida y se evita repetirla — alguien que ya
    leyo el bloque entero y pregunta un detalle quiere el detalle, no el bloque
    otra vez.
    """
    validos = fichas.disponibles()
    if producto not in validos:
        return {"error": f"no hay ficha para {producto!r}", "productos_validos": validos}

    if await fichas.ya_enviada(conversacion_id, producto):
        return {
            "se_envia": False,
            "ya_la_recibio": True,
            "mensaje": (
                "La persona ya recibio esta descripcion en esta conversacion. No "
                "se manda de nuevo: conteste lo puntual con sus palabras, en una "
                "o dos lineas."
            ),
        }

    # La introduccion la escribe el modelo y sale antes del bloque: asiente lo
    # que dijo la persona y presenta lo que viene. Se acota como cualquier
    # texto libre.
    introduccion = " ".join(str(introduccion or "").split())[:LARGO_MAXIMO_TEXTO]

    return {
        "se_envia": True,
        "producto": fichas.nombre(producto),
        "introduccion": introduccion,
        "mensaje": (
            "La introduccion sale primero y la descripcion oficial despues, tal "
            "cual. No repita ni resuma la descripcion. Lo que usted escriba va "
            "despues de ella: una linea corta con el paso siguiente."
        ),
    }


# ---------------------------------------------------------------------------
# finalizar_calificacion
# ---------------------------------------------------------------------------

async def finalizar_calificacion(conversacion_id: int) -> dict[str, Any]:
    """Cierra el relevamiento. Se niega si falta algo sin lo cual no sirve."""
    fila = await db.consultar_una(
        "SELECT datos, telefono, estado FROM conversaciones WHERE id = $1", conversacion_id
    )
    datos = (fila["datos"] if fila else None) or {}
    telefono = fila["telefono"] if fila else None

    # Ya se cerro antes. Se pregunta por el evento y no por el estado: una
    # conversacion `calificada` que recibe un mensaje vuelve a `activa` —tiene
    # que hacerlo, o el cliente que pregunta algo despues se queda sin
    # respuesta— y entonces el estado deja de servir como marca.
    ya_cerrada = await db.valor(
        "SELECT detalle->'datos' FROM eventos WHERE conversacion_id = $1 "
        "AND tipo = 'calificacion_finalizada' ORDER BY id DESC LIMIT 1",
        conversacion_id,
    )

    if ya_cerrada is not None:
        # Si la persona siguio hablando y cambio algo —otro numero, mas metros—
        # el CRM tiene que enterarse: el vendedor va a llamar con eso.
        if ya_cerrada != datos:
            logger.info("la calificacion cambio despues de cerrada, se actualiza "
                        "el CRM | conversacion=%s", conversacion_id)
            await db.ejecutar(
                "UPDATE conversaciones SET estado = 'calificada' WHERE id = $1",
                conversacion_id,
            )
            await db.ejecutar(
                "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
                "VALUES ($1, 'calificacion_finalizada', 'ok', $2)",
                conversacion_id,
                {"datos": datos, "actualizacion": True},
            )
            await sincronizar(conversacion_id)
            return {
                "finalizada": True,
                "ya_estaba_cerrada": True,
                "datos_actualizados": True,
                "mensaje": (
                    "Ya estaba cerrada y el dato nuevo se le paso al asesor. "
                    "Confirme solo lo que cambio, en una linea. No repita la "
                    "confirmacion ni la despedida."
                ),
            }

        logger.info("ya estaba calificada y sin cambios | conversacion=%s",
                    conversacion_id)
        return {
            "finalizada": True,
            "ya_estaba_cerrada": True,
            "mensaje": (
                "Esta conversacion ya se cerro y el asesor ya tiene los datos. "
                "No repita la confirmacion. Si la persona pregunto algo, contestelo "
                "en una o dos lineas; si solo agradecio, use la despedida final una "
                "sola vez, y si ya salio, cerrar_sin_responder."
            ),
        }

    linea = datos.get("linea")
    # En modo rapido se cierra con lo minimo: el asesor completa en la llamada.
    apurada = await esta_apurada(conversacion_id)
    requeridos_por_linea = calificacion()[
        "requeridos_por_linea_rapido" if apurada else "requeridos_por_linea"]
    requeridos = requeridos_por_linea.get(linea)
    if requeridos is None:
        return {"finalizada": False, "falta": ["linea"],
                "mensaje": "sin saber la linea no se puede cerrar"}

    por_objetivo = {} if apurada else (
        (calificacion().get("requeridos_por_objetivo") or {}).get(linea) or {})
    requeridos = [*requeridos, *(por_objetivo.get(datos.get("objetivo")) or [])]

    faltan = await faltantes(conversacion_id, datos, requeridos)
    if faltan:
        return {
            "finalizada": False,
            "falta": faltan,
            "mensaje": (
                "faltan datos sin los cuales el vendedor no puede llamar. Estan en "
                "el orden en que se piden: pregunte primero el primero de la lista"
            ),
        }

    # El telefono esta en `datos` pero no se pudo normalizar. Sin numero valido
    # la calificacion no sirve: el asesor llama por telefono.
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
    # reintento y el agente cierra igual.
    await sincronizar(conversacion_id)

    return {
        "finalizada": True,
        "datos": datos,
        "telefono_confirmado": telefono,
        "mensaje": (
            "Calificacion cerrada. Escriba la confirmacion (respuesta "
            "confirmo_el_numero) y nada mas."
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
    return {"escalado": True, "motivo": motivo, "estado": estado}


# ---------------------------------------------------------------------------
# cerrar_sin_responder
# ---------------------------------------------------------------------------

async def cerrar_sin_responder(conversacion_id: int, motivo: str) -> dict[str, Any]:
    """El agente decide que no hay nada que contestar, y eso queda registrado.

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
    """Se generan desde `config/calificacion.yaml` para que no se desincronicen."""
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
                "Guarda un dato del cliente apenas lo menciona. Si en un mensaje da "
                "varios datos, guardalos todos en el mismo turno.\n\nCampos:\n"
                + descripcion_campos
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
                "Si faltan datos, te dice cuales y en que orden. Si estan, manda la "
                "lista oficial de precios del mes, tal cual, antes de tu mensaje. Nunca "
                "escribas un precio vos: salen solo de aca. No calcula totales."
            ),
            "esquema": {
                "type": "object",
                "properties": {
                    "zona": {
                        "type": "string",
                        # Cinco zonas desde el 29/9/2026, cada una con su minimo y
                        # su tabla de precios. El modelo no tiene que elegirla: la
                        # deduce el codigo de la ciudad (app/ubicaciones.py).
                        "enum": ["quito_y_valles", "pichincha_cercana", "zona_azul",
                                 "zona_verde", "zona_roja", "galapagos", "fuera_del_pais"],
                        "description": "Solo si querés consultar por una zona distinta a la guardada",
                    },
                    "introduccion": {
                        "type": "string",
                        "description": (
                            "La linea que sale ANTES de la lista: asiente lo ultimo que "
                            "dijo la persona y presenta la lista. Sin numeros."
                        ),
                    },
                },
                "required": [],
            },
        },
        {
            "nombre": "enviar_ficha",
            "descripcion": (
                "Manda la descripcion oficial de un producto, escrita por MasterShield, "
                "y el video de los trabajos. Usala cuando pregunten que es o como "
                "funciona un producto. Primero sale tu `introduccion`, despues la "
                "descripcion y despues tu mensaje, corto, con el paso siguiente. No "
                "la uses para una duda puntual. Maximo dos por mensaje."
            ),
            "esquema": {
                "type": "object",
                "properties": {
                    "producto": {"type": "string", "enum": fichas.disponibles()},
                    "introduccion": {
                        "type": "string",
                        "description": (
                            "La linea que sale ANTES de la descripcion: asiente lo que "
                            "dijo la persona y presenta lo que viene. Sin precios."
                        ),
                    },
                },
                "required": ["producto", "introduccion"],
            },
        },
        {
            "nombre": "finalizar_calificacion",
            "descripcion": (
                "Cierra el relevamiento cuando la persona confirmo el numero para la "
                "llamada. Si falta algo, te lo dice y seguis preguntando."
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
                "que dijo el cliente no pide respuesta y contestar seria hablar por "
                "hablar, o cuando ya se mando la despedida final. En la duda, contesta."
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
    conversacion, no cortarla.
    """
    try:
        if nombre == "guardar_dato":
            return await guardar_dato(
                conversacion_id, argumentos["campo"], argumentos["valor"]
            )
        if nombre == "consultar_precio":
            return await consultar_precio(
                conversacion_id, zona=argumentos.get("zona"),
                introduccion=argumentos.get("introduccion"),
            )
        if nombre == "enviar_ficha":
            return await enviar_ficha(
                conversacion_id, argumentos["producto"], argumentos.get("introduccion")
            )
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
