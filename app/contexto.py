"""Lo que el agente necesita saber en este turno, traido de la base.

Hasta el 15/9/2026 todos los casos vivian en el prompt —unos 12.600 tokens en
cada llamada— y varias respuestas las metia el codigo en el mismo mensaje que
escribia el modelo. Los dos autores chocaban y salian mensajes duplicados.

Ahora el modelo es el unico que escribe, y en cada turno recibe solo:

1. **El paso en el que esta la conversacion**, calculado aca con los datos que ya
   hay: el orden no lo decide el modelo.
2. **Las respuestas de ese paso**, de la tabla `respuestas`, mas las que se
   parecen a lo que escribio la persona (una pregunta tecnica, pedir un asesor).
3. **La informacion de MasterShield relacionada**, de la tabla `catalogo`.

La busqueda por similitud se hace antes de llamar al modelo y no como una
herramienta: como herramienta costaba una vuelta de modelo mas, varios segundos.
Si falla la busqueda, el turno sigue con las respuestas del paso.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app import db
from app.agente import herramientas
from app.config import obtener_settings
from app.telefono import para_mostrar

logger = logging.getLogger(__name__)

MODELO_EMBEDDINGS = "text-embedding-3-small"
# Cuantas respuestas de otros pasos se suman por parecido con el mensaje.
RESPUESTAS_POR_PARECIDO = 3
# Cuantos fragmentos de informacion de MasterShield se pasan.
FRAGMENTOS_DE_CONOCIMIENTO = 3
# Por debajo de este parecido (1 - distancia coseno) no se suma nada: con un
# "hola" no tiene sentido traer la garantia.
PARECIDO_MINIMO = 0.35

ETAPAS = {
    "nombre_ciudad": "Pedir o registrar el nombre y la ciudad",
    "pedido": "Saber qué quiere resolver",
    "superficie": "Saber si el vidrio es de ventanas o de un techo",
    "referencia": "Pedir los metros aproximados o fotos",
    "precio": "Dar los precios del mes",
    "llamada": "Ofrecer la llamada con un asesor y confirmar el número",
    "despues_del_cierre": "La conversación ya se cerró: el asesor ya tiene los datos",
}

_ETAPA_DEL_DATO = {
    "nombre": "nombre_ciudad",
    "zona": "nombre_ciudad",
    "linea": "pedido",
    "objetivo": "pedido",
    "modelo_vehiculo": "pedido",
    "superficie": "superficie",
    "referencia": "referencia",
}

# La lista de precios empieza siempre asi (app/textos.py).
INICIO_DE_LA_LISTA = "▪ Actualmente en el material que necesita"


@dataclass
class Estado:
    etapa: str
    faltan: list[str] = field(default_factory=list)
    apurada: bool = False
    ya_cerrada: bool = False
    # "imagen" o "video" si lo ultimo que mando la persona fue un archivo.
    adjunto: str = ""
    # La zona de precios: decide que se ofrece al cerrar. En Quito y valles la
    # visita tecnica es gratis y esta cerca; afuera se ofrece la llamada con un
    # asesor y es el asesor quien ve si la visita corresponde (29/9/2026).
    zona: str = ""


PASO_SIGUIENTE = {
    "superficie": ["referencia"],
    "referencia": ["precio"],
    "precio": ["llamada"],
}


def etapa_segun(faltan: list[str], lista_enviada: bool, ya_cerrada: bool) -> str:
    """El paso de la conversacion, sin tocar la base: facil de probar."""
    if ya_cerrada:
        return "despues_del_cierre"
    if lista_enviada:
        return "llamada"
    if faltan:
        return _ETAPA_DEL_DATO.get(faltan[0], "pedido")
    return "precio"


async def calcular_estado(conversacion_id: int, datos: dict[str, Any]) -> Estado:
    apurada = await herramientas.esta_apurada(conversacion_id)
    ya_cerrada = bool(await db.valor(
        "SELECT 1 FROM eventos WHERE conversacion_id = $1 "
        "AND tipo = 'calificacion_finalizada' LIMIT 1", conversacion_id))
    lista_enviada = bool(await db.valor(
        "SELECT 1 FROM mensajes WHERE conversacion_id = $1 AND rol = 'agente' "
        "AND contenido LIKE $2 LIMIT 1", conversacion_id, INICIO_DE_LA_LISTA + "%"))

    faltan = await herramientas.faltantes(
        conversacion_id, datos, herramientas.requisitos_del_precio(datos, apurada))
    # Vehicular no tiene lista de precios: con el modelo del vehiculo, lo que
    # sigue es la llamada.
    if datos.get("linea") == "vehicular" and not faltan:
        if datos.get("modelo_vehiculo"):
            lista_enviada = True
        else:
            faltan = ["modelo_vehiculo"]

    # Una foto completa la referencia, asi que el paso salta a los precios y la
    # respuesta que la agradece se quedaba afuera: el agente improvisaba un
    # "gracias" suelto y no decia que la mira el asesor (15/9/2026).
    ultimo_tipo = await db.valor(
        "SELECT tipo FROM mensajes WHERE conversacion_id = $1 AND rol = 'cliente' "
        "ORDER BY id DESC LIMIT 1", conversacion_id)
    adjunto = ultimo_tipo if ultimo_tipo in ("imagen", "video") else ""

    return Estado(etapa_segun(faltan, lista_enviada, ya_cerrada), faltan, apurada,
                  ya_cerrada, adjunto, datos.get("zona") or "")


_cliente_openai = None


async def _embedding(texto: str) -> list[float] | None:
    global _cliente_openai
    try:
        if _cliente_openai is None:
            from openai import AsyncOpenAI

            _cliente_openai = AsyncOpenAI(api_key=obtener_settings().openai_api_key)
        r = await _cliente_openai.embeddings.create(model=MODELO_EMBEDDINGS, input=texto[:2000])
        return r.data[0].embedding
    except Exception:
        logger.warning("no se pudo calcular el embedding del mensaje", exc_info=True)
        return None


async def _sin_embedding() -> None:
    return None


def vector_sql(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"


ADJUNTOS = {"imagen": ("recibio_fotos", "una foto"),
            "video": ("recibio_un_video", "un video")}


def claves_extra(estado: Estado) -> list[str]:
    """Respuestas que entran aunque no sean del paso actual."""
    claves = ["modo_rapido", "precio_sin_datos"] if estado.apurada else []
    if estado.adjunto in ADJUNTOS:
        claves.append(ADJUNTOS[estado.adjunto][0])
    return claves


def corresponde_a_la_zona(respuesta: dict, zona: str) -> bool:
    """Si esa respuesta se usa en esta zona.

    Una respuesta puede declarar `solo_zonas` en el YAML: la oferta del cierre
    es distinta en Quito —donde la visita tecnica es gratis— que en el resto del
    pais, donde se ofrece la llamada. Se filtra aca y no se le deja elegir al
    modelo: con las dos delante, elige cualquiera.
    """
    solo = respuesta.get("solo_zonas")
    if not solo:
        return True
    return bool(zona) and zona in solo


async def _respuestas(estado: Estado, vector: list[float] | None) -> list[dict]:
    # Tambien las del paso siguiente: con los metros el agente da los precios en
    # el mismo turno, y con los precios ofrece la llamada. Sin esto escribia la
    # oferta con sus palabras en vez del texto del cliente.
    etapas = [estado.etapa, *PASO_SIGUIENTE.get(estado.etapa, [])]
    extra = claves_extra(estado)
    filas = await db.consultar(
        "SELECT clave, situacion, respuesta, instrucciones, solo_zonas FROM respuestas "
        "WHERE activa AND (etapa = ANY($1::text[]) OR clave = ANY($2::text[])) ORDER BY id",
        etapas, extra)
    elegidas = [dict(f) for f in filas]

    if vector is not None:
        vistas = [r["clave"] for r in elegidas]
        parecidas = await db.consultar(
            "SELECT clave, situacion, respuesta, instrucciones, solo_zonas, "
            "1 - (embedding <=> $1::vector) AS parecido FROM respuestas "
            "WHERE activa AND embedding IS NOT NULL AND NOT (clave = ANY($2::text[])) "
            "ORDER BY embedding <=> $1::vector LIMIT $3",
            vector_sql(vector), vistas, RESPUESTAS_POR_PARECIDO)
        elegidas += [dict(f) for f in parecidas if f["parecido"] >= PARECIDO_MINIMO]
    return [r for r in elegidas if corresponde_a_la_zona(r, estado.zona)]


async def _conocimiento(vector: list[float] | None) -> list[dict]:
    if vector is None:
        return []
    filas = await db.consultar(
        "SELECT titulo, contenido, 1 - (embedding <=> $1::vector) AS parecido "
        "FROM catalogo ORDER BY embedding <=> $1::vector LIMIT $2",
        vector_sql(vector), FRAGMENTOS_DE_CONOCIMIENTO)
    return [dict(f) for f in filas if f["parecido"] >= PARECIDO_MINIMO]


def minimo_de(zona: str | None) -> float | None:
    """Los m2 minimos de instalacion de esa zona. Vive en `precios`, que es quien
    lee la configuracion de zonas; aca queda el nombre que ya usaban los
    llamadores."""
    from app.precios import minimo_de as desde_precios

    return desde_precios(zona)


def formatear(estado: Estado, respuestas: list[dict], conocimiento: list[dict],
              telefono: str | None, cuando_llaman: str = "",
              minimo_m2: float | None = None) -> str:
    """El bloque que se agrega al mensaje de sistema de este turno."""
    partes = [f"# Paso actual: {ETAPAS.get(estado.etapa, estado.etapa)}"]
    if minimo_m2:
        # El minimo se dice ANTES de pedir los metros, y en ese momento todavia
        # no se llamo a consultar_precio: sin este dato el agente escribia
        # "{minimo}" tal cual, porque no tenia con que completarlo (29/9/2026).
        partes.append(f"Mínimo de instalación en esta zona: {minimo_m2:g} m²")
    if cuando_llaman:
        # El agente atiende a cualquier hora; la oficina llama en la suya.
        partes.append(f"Cuándo lo llama el asesor, si cierran ahora: {cuando_llaman}")
    if estado.faltan:
        partes.append("Datos que faltan para el precio, en orden: " + ", ".join(estado.faltan))
    if estado.apurada:
        partes.append("Modo rápido: la persona insistió con el precio. Solo hacen falta "
                      "la ciudad y qué quiere resolver.")
    if estado.adjunto in ADJUNTOS:
        partes.append(f"Acaba de enviar {ADJUNTOS[estado.adjunto][1]}: agradézcaselo "
                      "antes de seguir con el paso.")
    if telefono:
        # "Como se escribe en Ecuador" invitaba a transformarlo: a un numero
        # argentino le puso un cero adelante y le saco el + (16/9/2026). Ya
        # viene con el formato que corresponde a su pais.
        partes.append(f"Número para confirmar la llamada, escríbalo tal cual: {telefono}")

    if respuestas:
        partes.append("\n# Respuestas para este momento")
        for r in respuestas:
            bloque = [f"## {r['clave']}", f"Cuándo: {r['situacion']}"]
            if r.get("respuesta"):
                bloque.append(f"Responda:\n{r['respuesta'].strip()}")
            if r.get("instrucciones"):
                bloque.append(f"Además: {r['instrucciones'].strip()}")
            partes.append("\n".join(bloque))

    if conocimiento:
        partes.append("\n# Información de MasterShield para este mensaje")
        for c in conocimiento:
            partes.append(f"## {c.get('titulo') or ''}\n{c['contenido'].strip()}")

    return "\n\n".join(partes)


async def armar(conversacion_id: int, datos: dict[str, Any] | None, texto_cliente: str,
                identificador: str | None = None) -> str:
    datos = datos or {}
    # En paralelo: cada consulta a la base es un viaje de red, y el turno ya
    # espera al modelo.
    estado, vector = await asyncio.gather(
        calcular_estado(conversacion_id, datos),
        _embedding(texto_cliente) if texto_cliente.strip() else _sin_embedding(),
    )
    try:
        respuestas, conocimiento = await asyncio.gather(
            _respuestas(estado, vector), _conocimiento(vector))
    except Exception:
        # Sin la tabla o sin pgvector, el agente sigue con el prompt: peor, pero
        # contesta. Queda en el log para arreglarlo.
        logger.exception("no se pudieron traer las respuestas de la base")
        respuestas, conocimiento = [], []

    telefono = para_mostrar(datos.get("telefono") or identificador) if (
        datos.get("telefono") or identificador) else None
    logger.info("contexto | conversacion=%s etapa=%s respuestas=%s conocimiento=%s",
                conversacion_id, estado.etapa, [r["clave"] for r in respuestas],
                [c.get("titulo") for c in conocimiento])
    return formatear(estado, respuestas, conocimiento, telefono,
                     obtener_settings().cuando_llaman(),
                     minimo_de(datos.get("zona")))
