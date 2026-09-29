"""Ciclo de tool use.

Arma el contexto, deja que el modelo llame herramientas y devuelve el texto de
la respuesta. No sabe de que canal vino la conversacion ni a donde va la
respuesta: eso es del worker.

Desde el 15/9/2026 el modelo es el unico que escribe la conversacion. En cada
turno recibe un prompt corto y, de la base, las respuestas del paso en el que
esta la conversacion y la informacion relacionada (app/contexto.py). Lo unico
que el codigo manda tal cual son bloques que el modelo nunca escribe: la lista
de precios, las fichas y el video.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from app import contexto, db, fichas, textos, verificacion
from app.agente import herramientas, proveedor as proveedores
from app.config import obtener_settings

logger = logging.getLogger(__name__)

PROMPTS = Path(__file__).resolve().parent.parent.parent / "prompts"

# Cuantos mensajes del historial se mandan. Una conversacion de venta no
# necesita mas, y el historial es lo que mas crece.
MAX_MENSAJES_HISTORIAL = 40

# Cada ficha es un bloque largo. Dos ya es mucho para leer de corrido en un
# telefono.
MAX_FICHAS_POR_TURNO = 2


@dataclass
class Respuesta:
    texto: str
    iteraciones: int = 0
    herramientas_usadas: list[str] = field(default_factory=list)
    tokens_entrada: int = 0
    tokens_salida: int = 0
    tokens_cache_leidos: int = 0
    # True si el mensaje mencionaba un precio que no salio de la herramienta y
    # hubo que sacarlo. Deberia ser siempre False.
    precio_bloqueado: bool = False
    # True si el agente decidio callarse. Sin esto, no mandar nada es
    # indistinguible de un turno que fallo.
    silencio_deliberado: bool = False
    # Los productos cuya descripcion oficial sale antes del texto.
    fichas: list[str] = field(default_factory=list)
    # La linea con la que el agente presenta la primera ficha del turno.
    introduccion_fichas: str = ""
    # La lista de precios de MasterShield, ya armada. Sale antes del texto.
    lista_de_precios: str | None = None
    # La linea con la que el agente presenta la lista.
    introduccion_precios: str = ""
    # True si el turno se corto porque la persona escribio mientras el agente
    # pensaba. No se manda nada: el turno se rehace con el mensaje nuevo.
    descartada: bool = False


@lru_cache
def _leer(nombre: str) -> str:
    return (PROMPTS / nombre).read_text(encoding="utf-8")


def _cliente():
    """El proveedor del modelo, segun la configuracion."""
    settings = obtener_settings()
    return proveedores.crear(
        settings.proveedor_modelo, settings.clave_del_modelo, settings.modelo_agente,
        settings.esfuerzo_razonamiento,
    )


def armar_sistema(
    datos: dict | None,
    identificador: str | None = None,
    hubo_asesor: bool = False,
    contexto_del_turno: str = "",
) -> list[str]:
    """El prompt del sistema, en dos partes.

    La primera es estatica —las instrucciones, cortas— e identica en cada turno:
    los proveedores cachean por prefijo. La segunda es el estado de esta
    conversacion y lo que se trajo de la base para este turno.
    """
    estatico = _leer("agente.md")

    if datos:
        # Aplastado y acotado: si un dato entro a la base por otro camino, no
        # puede fabricar una seccion falsa dentro del prompt del sistema.
        conocido = "\n".join(
            f"- {k}: {' '.join(str(v).split())[:200]}" for k, v in sorted(datos.items())
        )
        estado = f"{conocido}\n\nNo vuelva a preguntar nada de esto."
        # El telefono del canal se siembra solo, sin que nadie lo haya dicho: se
        # confirma antes de cerrar, que no es lo mismo que pedirlo.
        if identificador and datos.get("telefono") == identificador:
            estado += ("\n\nEl telefono de arriba es el numero desde el que le escriben: "
                       "se confirma antes de cerrar.")
    else:
        estado = "Todavia nada. Es el arranque de la conversacion."

    dinamico = (
        f"# Ahora\n\n{_momento()}\n\n"
        f"# Lo que ya sabe de esta conversacion\n\n{estado}"
    )

    # Un asesor escribio en esta conversacion. Sus mensajes estan en el
    # historial como si fueran del agente.
    if hubo_asesor:
        dinamico += (
            "\n\n# Un asesor MS estuvo en esta conversacion\n\n"
            "Parte de lo que figura como suyo lo escribio una persona del equipo. "
            "No lo repita, no lo corrija y no vuelva a preguntar lo que el asesor "
            "ya pregunto. Si lo ultimo del cliente no pide respuesta, use "
            "`cerrar_sin_responder`."
        )

    if contexto_del_turno:
        dinamico += "\n\n" + contexto_del_turno

    return [estatico, dinamico]


def _momento() -> str:
    """La hora de Ecuador, no la del servidor."""
    settings = obtener_settings()
    ahora = datetime.now(settings.zona)
    dias = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
    dentro = "dentro del horario de atencion" if settings.esta_en_horario(ahora) else (
        "FUERA del horario de atencion"
    )
    return f"{dias[ahora.weekday()]} {ahora:%d/%m/%Y %H:%M} en Ecuador, {dentro}."


async def armar_historial(conversacion_id: int) -> list[dict]:
    """Convierte `mensajes` al formato de la API.

    Los mensajes de un vendedor cuentan como del asistente, porque desde el lado
    del cliente vinieron del mismo numero; y los mensajes consecutivos del mismo
    rol se juntan, porque la API los quiere alternados.
    """
    filas = await db.consultar(
        """
        SELECT rol, tipo, contenido FROM (
            SELECT rol, tipo, contenido, id FROM mensajes
            WHERE conversacion_id = $1
            ORDER BY id DESC LIMIT $2
        ) AS ultimos ORDER BY id
        """,
        conversacion_id,
        MAX_MENSAJES_HISTORIAL,
    )

    historial: list[dict] = []
    for fila in filas:
        rol = "cliente" if fila["rol"] == "cliente" else "agente"
        texto = fila["contenido"]
        # Lo que manda el agente que no es texto —el video— ya trae su
        # descripcion en el contenido. La marca es para lo del cliente.
        if fila["tipo"] != "texto" and rol == "cliente":
            # Una nota de voz transcrita es lo que la persona dijo, no un
            # archivo que alguien tenga que abrir.
            marca = ("[nota de voz]" if fila["tipo"] == "audio" and texto
                     else f"[el cliente envio un archivo de tipo {fila['tipo']}]")
            texto = f"{marca} {texto}".strip() if texto else marca
        if not texto:
            continue

        if historial and historial[-1]["rol"] == rol:
            historial[-1]["texto"] += "\n" + texto
        else:
            historial.append({"rol": rol, "texto": texto})

    # La API exige que arranque y termine con un mensaje del usuario.
    while historial and historial[0]["rol"] != "cliente":
        historial.pop(0)
    while historial and historial[-1]["rol"] != "cliente":
        historial.pop()

    return historial


async def responder(conversacion_id: int, hasta_id: int | None = None) -> Respuesta:
    """Corre un turno completo y devuelve el texto a enviar."""
    settings = obtener_settings()

    fila = await db.consultar_una(
        "SELECT datos, identificador FROM conversaciones WHERE id = $1", conversacion_id
    )
    datos = fila["datos"] if fila else None
    identificador = fila["identificador"] if fila else None
    historial = await armar_historial(conversacion_id)

    hubo_asesor = bool(await db.valor(
        "SELECT 1 FROM mensajes WHERE conversacion_id = $1 AND rol = 'vendedor' LIMIT 1",
        conversacion_id,
    ))

    if not historial:
        logger.warning("turno sin historial | conversacion=%s", conversacion_id)
        return Respuesta(texto="")

    # Lo del cliente en este turno, para buscar en la base lo relacionado.
    texto_cliente = historial[-1]["texto"] if historial[-1]["rol"] == "cliente" else ""
    contexto_del_turno = await contexto.armar(
        conversacion_id, datos, texto_cliente, identificador)

    proveedor = _cliente()
    definiciones = herramientas.definiciones()
    mensajes = proveedor.mensajes_iniciales(
        armar_sistema(datos, identificador, hubo_asesor, contexto_del_turno), historial
    )

    respuesta = Respuesta(texto="")

    # El modelo contesta en el mismo turno en que llama herramientas: primero
    # el bloque de texto, despues los tool_use. Se juntan todas las partes.
    partes: list[str] = []
    # Los precios por m2 que devolvio consultar_precio en este turno: son los
    # unicos que el mensaje tiene permitido mencionar.
    montos_autorizados: set[float] = set()

    # El ultimo mensaje del cliente que entro en este turno, que pasa el worker.
    # Si llega otro mientras el agente piensa, se corta antes de la vuelta
    # siguiente en vez de terminar y descartar al enviar.
    ultimo_del_cliente = hasta_id or await db.valor(
        "SELECT max(id) FROM mensajes WHERE conversacion_id = $1 AND rol = 'cliente'",
        conversacion_id,
    )

    for iteracion in range(1, settings.max_iteraciones_herramientas + 1):
        if iteracion > 1 and ultimo_del_cliente and await db.valor(
                "SELECT 1 FROM mensajes WHERE conversacion_id = $1 AND rol = 'cliente' "
                "AND id > $2 LIMIT 1", conversacion_id, ultimo_del_cliente):
            respuesta.descartada = True
            return respuesta

        salida = await proveedor.completar(mensajes, definiciones)

        respuesta.iteraciones = iteracion
        respuesta.tokens_entrada += salida.tokens_entrada
        respuesta.tokens_salida += salida.tokens_salida
        respuesta.tokens_cache_leidos += salida.tokens_cache

        if not salida.llamadas:
            if salida.texto:
                partes.append(salida.texto)
            return await _terminar(respuesta, partes, conversacion_id, montos_autorizados)

        resultados = []
        for llamada in salida.llamadas:
            resultado = await herramientas.ejecutar(
                llamada.nombre, conversacion_id, llamada.argumentos
            )
            respuesta.herramientas_usadas.append(llamada.nombre)

            # Decidio no contestar: se corta aca.
            if llamada.nombre == "cerrar_sin_responder" and resultado.get("sin_respuesta"):
                respuesta.texto = ""
                respuesta.silencio_deliberado = True
                return respuesta

            if llamada.nombre == "enviar_ficha" and resultado.get("se_envia"):
                producto = llamada.argumentos.get("producto")
                if producto in respuesta.fichas:
                    pass  # la pidio dos veces en el mismo turno: sale una
                elif len(respuesta.fichas) >= MAX_FICHAS_POR_TURNO:
                    resultado = {
                        "se_envia": False,
                        "mensaje": (
                            f"Ya van {MAX_FICHAS_POR_TURNO} descripciones en este "
                            "mensaje. Esta no sale: si la persona quiere saber mas "
                            "de ese producto, que lo pida."
                        ),
                    }
                else:
                    respuesta.fichas.append(producto)
                    if not respuesta.introduccion_fichas:
                        respuesta.introduccion_fichas = resultado.get("introduccion") or ""

            if llamada.nombre == "consultar_precio" and resultado.get("puede_informar"):
                for calidad in resultado.get("calidades") or []:
                    montos_autorizados.update(
                        v for v in (calidad.get("precio_normal_m2_sin_iva"),
                                    calidad.get("precio_especial_m2_sin_iva"))
                        if v is not None
                    )

            if llamada.nombre == "consultar_precio" and resultado.get("se_envia_lista"):
                respuesta.lista_de_precios = textos.lista_de_precios(resultado)
                respuesta.introduccion_precios = resultado.get("introduccion") or ""
                # El modelo no ve los numeros: con ellos delante los reescribia
                # debajo de la lista oficial (simulacion del 15/9/2026).
                resultado = {k: v for k, v in resultado.items()
                             if k not in ("calidades", "incluye", "como_decirlo",
                                          "descuento_pago_contado_pct")}
            logger.info(
                "herramienta | conversacion=%s %s(%s)",
                conversacion_id, llamada.nombre,
                json.dumps(llamada.argumentos, ensure_ascii=False),
            )
            resultados.append((llamada, resultado))

        mensajes = proveedor.continuar(mensajes, salida, resultados)

        if salida.texto:
            partes.append(salida.texto)

    logger.warning(
        "se agotaron las iteraciones de herramientas | conversacion=%s", conversacion_id
    )

    # Si se agotaron guardando datos, el turno se queda sin texto y la persona
    # no recibe nada. Una llamada mas, sin herramientas, para que escriba la
    # respuesta con todo lo que ya averiguo.
    if not partes:
        try:
            salida = await proveedor.completar(mensajes, [])
            respuesta.iteraciones += 1
            respuesta.tokens_entrada += salida.tokens_entrada
            respuesta.tokens_salida += salida.tokens_salida
            respuesta.tokens_cache_leidos += salida.tokens_cache
            if salida.texto:
                partes.append(salida.texto)
        except Exception:
            logger.exception("tampoco se pudo cerrar el turno | conversacion=%s",
                             conversacion_id)

    return await _terminar(respuesta, partes, conversacion_id, montos_autorizados)


async def _terminar(
    respuesta: Respuesta, partes: list[str], conversacion_id: int, autorizados: set[float]
) -> Respuesta:
    """Los controles sobre lo que escribio el modelo, en orden, antes de enviar.

    Son pocos a proposito: con un solo autor ya no hay textos del codigo con los
    que choque. Quedan la red de seguridad de los precios y la de repetir.
    """
    respuesta.texto = "\n".join(partes)
    await _sacar_saludo(respuesta, conversacion_id)
    if respuesta.lista_de_precios:
        sin_lista = sin_la_lista_reescrita(respuesta.texto)
        if sin_lista != respuesta.texto:
            logger.warning("el agente reescribio la lista de precios, se saca | "
                           "conversacion=%s | %r", conversacion_id, respuesta.texto)
            respuesta.texto = sin_lista
    await _verificar_precios(respuesta, conversacion_id, autorizados)
    _revisar_introduccion(respuesta, conversacion_id, autorizados)
    await _sacar_repetidos(respuesta, conversacion_id)
    return respuesta


_ORACIONES = re.compile(r"(?<=[.!?…])\s+")

# Desde que tan parecida una oracion cuenta como repetida.
UMBRAL_REPETIDO = 0.8
# Las oraciones mas cortas que esto no se controlan (asentimientos).
LARGO_PARA_REPETIR = 30

# Desde que largo una frase contenida en un bloque de este turno cuenta como
# repeticion. Corto de mas daria falsos positivos con un "perfecto" suelto.
LARGO_PARA_CONTENIDA = 15

# Cuantos mensajes del agente se miran hacia atras. Los duplicados medidos
# estaban a uno o dos mensajes, asi que con esto sobra; mas historial solo
# agrega riesgo de cortar una repregunta legitima (16/9/2026).
MENSAJES_QUE_MIRA_EL_FILTRO = 8


def _parecido(a: str, b: str) -> float:
    def normal(t: str) -> str:
        return " ".join(re.sub(r"[^\w\s]", " ", t.lower()).split())
    return SequenceMatcher(None, normal(a), normal(b)).ratio()


def _oraciones(texto: str) -> list[str]:
    return [o for linea in texto.splitlines()
            for o in _ORACIONES.split(" ".join(linea.split())) if o.strip()]


def lineas_nuevas(texto: str, previos: list[str], del_turno: list[str] | None = None) -> str:
    """Lo de `texto` que no repite algo de `previos`, oracion por oracion.

    Tambien contra lo que ya quedo de esta misma respuesta: el modelo a veces
    escribe la respuesta antes de usar herramientas y la vuelve a escribir
    despues. Los renglones vacios y los separadores de globo se conservan. Si
    todo repite, queda la ultima linea: mandar nada dejaria a la persona sin
    respuesta.
    """
    lineas = texto.splitlines()
    previas = [p for previo in previos for p in _oraciones(previo)]
    # Lo que sale en ESTE turno —la introduccion de la lista, la ficha— se
    # compara sin la excepcion de las frases cortas: "Gracias por las fotos,
    # Daniela 📸" salio como introduccion y otra vez en el mensaje siguiente,
    # y por corta se colaba (Pablo, 23/9/2026).
    de_ahora = [p for bloque in (del_turno or []) for p in _oraciones(bloque)]
    # Ademas del parecido oracion contra oracion: una frase que aparece DENTRO
    # de un bloque de este turno tambien es repeticion. "Gracias por las fotos,
    # Daniela 📸" era el arranque de la linea que presenta los precios, asi que
    # comparada contra la oracion entera daba 0.63 y pasaba (23/9/2026).
    bloques_de_ahora = [textos.normal(b) for b in (del_turno or [])]
    nuevas: list[str] = []
    alguna = False
    for linea in lineas:
        if not linea.strip() or linea.strip() == "---":
            nuevas.append(linea.strip())
            continue
        quedan: list[str] = []
        anterior_quedo = False
        for oracion in _oraciones(linea):
            if not re.search(r"\w", oracion):
                # Un emoji suelto va con la oracion de antes.
                if anterior_quedo:
                    quedan.append(oracion)
                continue
            # Un asentimiento corto ("Perfecto, Pablo.") se repite en cualquier
            # conversacion; sacarlo dejaba mensajes que arrancaban de golpe.
            adentro = textos.normal(oracion)
            sale_ahora = (
                any(_parecido(oracion, p) >= UMBRAL_REPETIDO for p in de_ahora)
                or (len(adentro) >= LARGO_PARA_CONTENIDA
                    and any(adentro in bloque for bloque in bloques_de_ahora))
            )
            corta = len(oracion) < LARGO_PARA_REPETIR and "?" not in oracion
            anterior_quedo = not sale_ahora and (corta or not any(
                _parecido(oracion, p) >= UMBRAL_REPETIDO for p in previas))
            if anterior_quedo:
                quedan.append(oracion)
                previas.append(oracion)
        if quedan:
            nuevas.append(" ".join(quedan))
            alguna = True
    if not alguna:
        con_texto = [l for l in lineas if l.strip() and l.strip() != "---"]
        return con_texto[-1] if con_texto else ""
    resultado = _limpiar_renglones("\n".join(nuevas))
    # Un mensaje que pide un dato sin la pregunta queda trunco: la pregunta
    # vuelve aunque ya se haya hecho (la persona no la contesto).
    if "?" in texto and "?" not in resultado:
        preguntas = [o for l in lineas for o in _oraciones(l) if "?" in o]
        resultado = f"{resultado}\n\n{preguntas[-1]}"
    return resultado


def _limpiar_renglones(texto: str) -> str:
    """Sin renglones vacios de sobra ni separadores sueltos al principio o al final."""
    texto = re.sub(r"\n{3,}", "\n\n", texto).strip()
    texto = re.sub(r"^(---\s*\n)+", "", texto)
    texto = re.sub(r"(\n\s*---\s*)+$", "", texto)
    texto = re.sub(r"(\n\s*---\s*){2,}\n", "\n---\n", texto)
    return texto.strip()


def bloques_del_turno(respuesta: Respuesta) -> list[str]:
    """Todo lo que sale en este turno sin pasar por el filtro.

    Son los bloques que manda el codigo tal cual —la ficha oficial, su video, la
    lista de precios— mas las lineas con que el modelo los presenta. Ninguno
    esta en `mensajes` todavia cuando corre el filtro, asi que si no se listan
    aca el modelo puede repetirlos sin que nadie lo note.

    Cada bloque nuevo que el codigo aprenda a mandar va agregado aca. Es el
    agujero por el que se colaron los duplicados: el filtro solo miraba la base
    (Pablo, 16/9/2026).
    """
    bloques = [fichas.texto(p) for p in respuesta.fichas]
    if respuesta.fichas:
        bloques.append(fichas.VIDEO_PRODUCTOS.leyenda)
    if respuesta.lista_de_precios:
        bloques.append(respuesta.lista_de_precios)
    bloques += [respuesta.introduccion_fichas, respuesta.introduccion_precios]
    return [b for b in bloques if b]


async def _sacar_repetidos(respuesta: Respuesta, conversacion_id: int) -> None:
    """Saca lo que repite lo que el agente ya dijo, o lo que sale en este turno."""
    if not respuesta.texto:
        return
    previos = [f["contenido"] for f in await db.consultar(
        "SELECT contenido FROM mensajes WHERE conversacion_id = $1 AND rol <> 'cliente' "
        "ORDER BY id DESC LIMIT $2", conversacion_id, MENSAJES_QUE_MIRA_EL_FILTRO)]
    del_turno = bloques_del_turno(respuesta)
    limpio = lineas_nuevas(respuesta.texto, previos, del_turno)
    if limpio != respuesta.texto.strip():
        logger.warning("el agente repitio algo que ya habia dicho, se saca | "
                       "conversacion=%s | %r -> %r", conversacion_id, respuesta.texto, limpio)
        respuesta.texto = limpio


def con_respiro(texto: str) -> str:
    """Un renglon libre entre lo que asiente y lo que sigue, si la primera
    oracion es corta (que es cuando es un agradecimiento)."""
    oraciones = _ORACIONES.split(texto.strip(), maxsplit=1)
    if len(oraciones) == 2 and len(oraciones[0]) <= 60:
        return f"{oraciones[0]}\n\n{oraciones[1]}"
    return texto


def _revisar_introduccion(
    respuesta: Respuesta, conversacion_id: int, autorizados: set[float]
) -> None:
    """Las introducciones de la ficha y de la lista las escribe el modelo: sin
    saludo y sin precios. La de la lista no lleva ningun numero: los precios
    estan justo abajo."""
    for campo, permitidos in (("introduccion_fichas", autorizados),
                              ("introduccion_precios", set())):
        intro = getattr(respuesta, campo)
        if not intro:
            continue
        if verificacion.saluda(intro):
            intro = verificacion.sacar_saludo(intro)
        esta_bien, motivo = verificacion.verificar(intro, permitidos)
        if not esta_bien:
            logger.error("%s con un precio, se descarta | conversacion=%s: %s | %r",
                         campo, conversacion_id, motivo, intro)
            intro = ""
        setattr(respuesta, campo, con_respiro(intro))


async def _sacar_saludo(respuesta: Respuesta, conversacion_id: int) -> None:
    """La bienvenida ya salio; un saludo aca reabre una conversacion en curso."""
    if not verificacion.saluda(respuesta.texto):
        return

    limpio = verificacion.sacar_saludo(respuesta.texto)
    logger.warning(
        "el agente saludo en medio de la conversacion | conversacion=%s | %r -> %r",
        conversacion_id, respuesta.texto, limpio,
    )
    await db.ejecutar(
        "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
        "VALUES ($1, 'saludo_fuera_de_lugar', 'error', $2)",
        conversacion_id,
        {"original": respuesta.texto[:500], "enviado": limpio[:500]},
    )
    respuesta.texto = limpio


# Un precio (no "unos 12 m²", que es el pedido), lo que incluye, o el lugar de
# la lista que el modelo a veces marca entre corchetes.
_LINEA_DE_LA_LISTA = re.compile(
    r"(\d[\d.,]*\s*(\$|d[oó]lares|usd)?\s*(\+\s*iva|m[aá]s iva|por m²|por m2|el metro|por metro))"
    r"|(\$\s*\d)|(\bm[aá]s iva\b)|^\W*(incluye|descuento|precio especial|precio normal|garant[ií]a \d)"
    r"|^\s*\[[^\]]*\]",
    re.IGNORECASE)
_ANUNCIA_LA_LISTA = re.compile(
    r"\b(comparto|env[ií]o|dejo|paso|adjunto)\b.*\b(lista|precios|valores)\b", re.IGNORECASE)


def sin_la_lista_reescrita(texto: str) -> str:
    """Lo que escribio el modelo en un turno en que la lista oficial ya sale.

    La lista la manda el codigo tal cual; lo del modelo va despues. Si repite
    precios, lo que incluye o anuncia la lista ("le comparto los valores"),
    eso sobra: queda la oferta de la llamada. En la simulacion del 15/9/2026 los
    tres flujos reescribieron la lista debajo de la oficial.
    """
    salida = []
    for linea in texto.splitlines():
        if linea.strip() and (_LINEA_DE_LA_LISTA.search(linea) or _ANUNCIA_LA_LISTA.search(linea)):
            buenas = [o for o in _ORACIONES.split(linea)
                      if not (_LINEA_DE_LA_LISTA.search(o) or _ANUNCIA_LA_LISTA.search(o))]
            if buenas:
                salida.append(" ".join(buenas))
            continue
        salida.append(linea)
    limpio = _limpiar_renglones("\n".join(salida))
    # Separadores de globo que quedaron sin nada entre medio.
    limpio = re.sub(r"(^|\n)---\s*(?=\n---|\Z)", r"\1", limpio).strip()
    return limpio


def sin_precios_no_autorizados(texto: str, autorizados: set[float]) -> str:
    """El texto sin las oraciones que mencionan un precio que no salio de la
    herramienta. Antes se reemplazaba el mensaje entero por uno generico que
    pedia el telefono, aunque el resto estuviera bien (Pablo, 15/9/2026)."""
    salida = []
    for linea in texto.splitlines():
        if not linea.strip():
            salida.append("")
            continue
        buenas = [o for o in _ORACIONES.split(linea)
                  if verificacion.verificar(o, autorizados)[0]]
        if buenas:
            salida.append(" ".join(buenas))
    return _limpiar_renglones("\n".join(salida))


async def _verificar_precios(
    respuesta: Respuesta, conversacion_id: int, autorizados: set[float]
) -> None:
    """Ultimo control antes de enviar: ningun precio que no haya salido de
    `consultar_precio`. Un precio equivocado dicho a un cliente real es un
    problema comercial, no un detalle."""
    esta_bien, motivo = verificacion.verificar(respuesta.texto, autorizados)
    if esta_bien:
        return

    limpio = sin_precios_no_autorizados(respuesta.texto, autorizados)
    logger.error(
        "precio no verificado, se sacan esas oraciones | conversacion=%s: %s | texto=%r",
        conversacion_id, motivo, respuesta.texto,
    )
    await db.ejecutar(
        "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
        "VALUES ($1, 'precio_no_verificado', 'error', $2)",
        conversacion_id,
        {"motivo": motivo, "texto": respuesta.texto,
         "autorizados": sorted(autorizados)},
    )
    respuesta.texto = limpio or verificacion.MENSAJE_SEGURO
    respuesta.precio_bloqueado = True
