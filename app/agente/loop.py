"""Ciclo de tool use.

Arma el contexto, deja que el modelo llame herramientas y devuelve el texto de
la respuesta. No sabe de que canal vino la conversacion ni a donde va la
respuesta: eso es del worker.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app import db, verificacion
from app.agente import herramientas, proveedor as proveedores
from app.config import obtener_settings

logger = logging.getLogger(__name__)

PROMPTS = Path(__file__).resolve().parent.parent.parent / "prompts"

# Cuantos mensajes del historial se mandan. Una conversacion de venta no
# necesita mas, y el historial es lo que mas crece: el prefijo estatico se
# cachea, esto no.
MAX_MENSAJES_HISTORIAL = 40



@dataclass
class Respuesta:
    texto: str
    iteraciones: int = 0
    herramientas_usadas: list[str] = field(default_factory=list)
    tokens_entrada: int = 0
    tokens_salida: int = 0
    tokens_cache_leidos: int = 0
    # True si el mensaje mencionaba un precio que no coincidia con lo calculado
    # y hubo que reemplazarlo. Deberia ser siempre False: si aparece, hay algo
    # que investigar.
    precio_bloqueado: bool = False


@lru_cache
def _leer(nombre: str) -> str:
    return (PROMPTS / nombre).read_text(encoding="utf-8")


def _cliente():
    """El proveedor del modelo, segun la configuracion."""
    settings = obtener_settings()
    return proveedores.crear(
        settings.proveedor_modelo, settings.clave_del_modelo, settings.modelo_agente
    )


def armar_sistema(datos: dict | None) -> list[str]:
    """El prompt del sistema, en dos partes.

    La primera es estatica —instrucciones y conocimiento tecnico— e identica en
    cada turno de cada conversacion. La segunda es el estado de esta
    conversacion, que cambia siempre.

    El orden importa para el costo: los dos proveedores cachean por prefijo, y
    poner primero lo que no cambia es lo que hace que el descuento aplique.
    Anthropic ademas lo marca explicitamente; OpenAI lo hace solo.
    """
    estatico = (
        _leer("agente.md")
        + "\n\n---\n\n# Su informacion tecnica\n\n"
        + "Esto es todo lo que usted sabe de los productos. Lo que no este aca, "
        + "lo confirma un asesor MS.\n\n"
        + _leer("conocimiento.md")
    )

    if datos:
        # Aplastado y acotado tambien aca, ademas de en guardar_dato: si un dato
        # entro a la base por otro camino, no puede fabricar una seccion falsa
        # dentro del prompt del sistema.
        conocido = "\n".join(
            f"- {k}: {' '.join(str(v).split())[:200]}" for k, v in sorted(datos.items())
        )
        estado = f"{conocido}\n\nNo vuelva a preguntar nada de esto."
    else:
        estado = "Todavia nada. Es el arranque de la conversacion."

    dinamico = (
        f"# Ahora\n\n{_momento()}\n\n"
        f"# Lo que ya sabe de esta conversacion\n\n{estado}"
    )
    return [estatico, dinamico]


def _momento() -> str:
    """La hora de Ecuador, no la del servidor.

    Sin esto el agente no puede saludar bien —no sabe si es la manana o la
    tarde— ni sabe que esta contestando fuera de horario.
    """
    settings = obtener_settings()
    ahora = datetime.now(settings.zona)
    dias = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
    dentro = "dentro del horario de atencion" if settings.esta_en_horario(ahora) else (
        "FUERA del horario de atencion"
    )
    return f"{dias[ahora.weekday()]} {ahora:%d/%m/%Y %H:%M} en Ecuador, {dentro}."


async def armar_historial(conversacion_id: int) -> list[dict]:
    """Convierte `mensajes` al formato de la API.

    Dos detalles: los mensajes de un vendedor cuentan como del asistente,
    porque desde el lado del cliente vinieron del mismo numero; y los mensajes
    consecutivos del mismo rol se juntan, porque la API los quiere alternados y
    una rafaga son varios seguidos del cliente.
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
        if fila["tipo"] != "texto":
            marca = f"[el cliente envio un archivo de tipo {fila['tipo']}]"
            texto = f"{marca} {texto}".strip() if texto else marca
        if not texto:
            continue

        if historial and historial[-1]["rol"] == rol:
            historial[-1]["texto"] += "\n" + texto
        else:
            historial.append({"rol": rol, "texto": texto})

    # La API exige que arranque y termine con un mensaje del usuario. Lo de
    # atras importa mas de lo que parece: si un vendedor escribio ultimo, la
    # llamada devuelve 400 y la conversacion queda sin respuesta.
    while historial and historial[0]["rol"] != "cliente":
        historial.pop(0)
    while historial and historial[-1]["rol"] != "cliente":
        historial.pop()

    return historial


async def responder(conversacion_id: int) -> Respuesta:
    """Corre un turno completo y devuelve el texto a enviar."""
    settings = obtener_settings()

    datos = await db.valor("SELECT datos FROM conversaciones WHERE id = $1", conversacion_id)
    historial = await armar_historial(conversacion_id)

    if not historial:
        logger.warning("turno sin historial | conversacion=%s", conversacion_id)
        return Respuesta(texto="")

    proveedor = _cliente()
    definiciones = herramientas.definiciones()
    mensajes = proveedor.mensajes_iniciales(armar_sistema(datos), historial)

    respuesta = Respuesta(texto="")

    # El modelo contesta en el mismo turno en que llama herramientas: primero
    # el bloque de texto, despues los tool_use. Si nos quedaramos solo con el
    # texto de la ultima vuelta, esa respuesta se pierde y al cliente le llega
    # unicamente la repregunta que vino despues de los resultados.
    partes: list[str] = []
    # Los montos que devolvio calcular_precio en este turno. Son los unicos que
    # el mensaje tiene permitido mencionar.
    montos_autorizados: set[float] = set()

    for iteracion in range(1, settings.max_iteraciones_herramientas + 1):
        salida = await proveedor.completar(mensajes, definiciones)

        respuesta.iteraciones = iteracion
        respuesta.tokens_entrada += salida.tokens_entrada
        respuesta.tokens_salida += salida.tokens_salida
        respuesta.tokens_cache_leidos += salida.tokens_cache

        if not salida.llamadas:
            if salida.texto:
                partes.append(salida.texto)
            respuesta.texto = "\n".join(partes)
            await _verificar_precios(respuesta, conversacion_id, montos_autorizados)
            return respuesta

        resultados = []
        for llamada in salida.llamadas:
            resultado = await herramientas.ejecutar(
                llamada.nombre, conversacion_id, llamada.argumentos
            )
            respuesta.herramientas_usadas.append(llamada.nombre)
            if llamada.nombre == "calcular_precio" and resultado.get("puede_cotizar"):
                montos_autorizados.update(
                    v for v in (resultado.get("subtotal_sin_iva"),
                                resultado.get("precio_m2_sin_iva")) if v is not None
                )
            logger.info(
                "herramienta | conversacion=%s %s(%s)",
                conversacion_id, llamada.nombre,
                json.dumps(llamada.argumentos, ensure_ascii=False),
            )
            resultados.append((llamada, resultado))

        mensajes = proveedor.continuar(mensajes, salida, resultados)

        if salida.texto:
            partes.append(salida.texto)

        # El estado pudo cambiar: `guardar_dato` escribio, y el bloque de
        # contexto tiene que reflejarlo. Se reconstruye el mensaje de sistema.
        datos = await db.valor("SELECT datos FROM conversaciones WHERE id = $1", conversacion_id)

    logger.warning(
        "se agotaron las iteraciones de herramientas | conversacion=%s", conversacion_id
    )
    respuesta.texto = "\n".join(partes)
    await _verificar_precios(respuesta, conversacion_id, montos_autorizados)
    return respuesta


async def _verificar_precios(
    respuesta: Respuesta, conversacion_id: int, autorizados: set[float]
) -> None:
    """Ultimo control antes de enviar: que el numero del mensaje sea el calculado.

    El precio lo decide Python, pero el mensaje lo escribe el modelo. Si no
    coinciden, no se manda: un precio equivocado dicho a un cliente real es un
    problema comercial, no un detalle.
    """
    esta_bien, motivo = verificacion.verificar(respuesta.texto, autorizados)
    if esta_bien:
        return

    logger.error(
        "precio no verificado, mensaje reemplazado | conversacion=%s: %s | texto=%r",
        conversacion_id, motivo, respuesta.texto,
    )
    await db.ejecutar(
        "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
        "VALUES ($1, 'precio_no_verificado', 'error', $2)",
        conversacion_id,
        {"motivo": motivo, "texto": respuesta.texto,
         "autorizados": sorted(autorizados)},
    )
    respuesta.texto = verificacion.MENSAJE_SEGURO
    respuesta.precio_bloqueado = True
