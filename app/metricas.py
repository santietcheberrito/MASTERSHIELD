"""El estado del sistema ahora, no lo que paso.

Los logs sirven despues: algo salio mal y uno lo reconstruye. Esto responde otra
pregunta, la que importa cuando el sistema queda andando solo: **hay algo roto
en este momento?**

Cinco numeros, y el primero es el que justifica el resto. Un lead que agoto los
reintentos del CRM deja una fila marcada a proposito, esperando que algo la
muestre. Sin esto, ese "algo" no existe y el lead se pierde igual, solo que con
registro.
"""

from __future__ import annotations

import logging
from typing import Any

from calendar import monthrange
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app import db
from app.precios import configuracion

logger = logging.getLogger(__name__)

# Cada consulta es un escalar o un puñado de filas sobre indices que ya existen.
# El endpoint se puede pegar cada un minuto sin que se note.
_CRM = """
    SELECT
        count(*) FILTER (WHERE crm_pendiente)                                AS pendientes,
        count(*) FILTER (WHERE crm_pendiente AND crm_reintentar_en IS NULL)   AS agotadas,
        count(*) FILTER (WHERE crm_pendiente AND crm_reintentar_en IS NOT NULL
                           AND crm_reintentar_en <= now())                    AS vencidas,
        max(crm_intentos) FILTER (WHERE crm_pendiente)                        AS mas_intentos
    FROM conversaciones
"""

_ESTADOS = "SELECT estado, count(*) AS cuantas FROM conversaciones GROUP BY estado"

_TURNOS = """
    SELECT
        count(*)                                                    AS en_cola,
        count(*) FILTER (WHERE procesar_despues <= now())            AS vencidos,
        count(*) FILTER (WHERE intentos >= $1)                       AS trabados,
        max(intentos)                                                AS mas_intentos
    FROM pendientes
"""

# El dia se cuenta en hora de Ecuador, no del servidor: si se cortara a
# medianoche UTC, el corte caeria a las 19h de un dia laboral.
_ACTIVIDAD = """
    SELECT
        count(*) FILTER (WHERE rol = 'cliente')  AS mensajes_recibidos,
        count(*) FILTER (WHERE rol = 'agente')   AS mensajes_enviados,
        count(DISTINCT conversacion_id)          AS conversaciones
    FROM mensajes
    WHERE creado_en >= date_trunc('day', now() AT TIME ZONE $1) AT TIME ZONE $1
"""

_EVENTOS_HOY = """
    SELECT tipo, estado, count(*) AS cuantos
    FROM eventos
    WHERE creado_en >= date_trunc('day', now() AT TIME ZONE $1) AT TIME ZONE $1
    GROUP BY tipo, estado
    ORDER BY cuantos DESC
"""


async def reunir(zona: str = "America/Guayaquil", turnos_trabados: int = 3) -> dict[str, Any]:
    """Junta todo en un diccionario. No falla: informa que no pudo."""
    try:
        crm = await db.consultar_una(_CRM)
        estados = await db.consultar(_ESTADOS)
        turnos = await db.consultar_una(_TURNOS, turnos_trabados)
        actividad = await db.consultar_una(_ACTIVIDAD, zona)
        eventos = await db.consultar(_EVENTOS_HOY, zona)
    except Exception as e:
        logger.exception("no se pudieron reunir las metricas")
        return {"estado": "sin datos", "error": f"{type(e).__name__}: {e}"}

    datos = {
        # Lo primero porque es lo unico que se mira cuando algo anda mal.
        "crm": {
            "pendientes": crm["pendientes"],
            "agotadas": crm["agotadas"],
            "vencidas_sin_reintentar": crm["vencidas"],
            "mas_intentos": crm["mas_intentos"] or 0,
        },
        "conversaciones": {f["estado"]: f["cuantas"] for f in estados},
        "turnos": {
            "en_cola": turnos["en_cola"],
            "vencidos": turnos["vencidos"],
            "trabados": turnos["trabados"],
            "mas_intentos": turnos["mas_intentos"] or 0,
        },
        "hoy": {
            "conversaciones": actividad["conversaciones"],
            "mensajes_recibidos": actividad["mensajes_recibidos"],
            "mensajes_enviados": actividad["mensajes_enviados"],
            "eventos": {f"{f['tipo']}:{f['estado']}": f["cuantos"] for f in eventos},
        },
    }
    datos["precios"] = estado_de_los_precios()
    datos["alertas"] = alertas(datos)
    datos["estado"] = "atencion" if datos["alertas"] else "ok"
    return datos


# Con cuantos dias de anticipacion avisar que la promocion se termina. Cinco
# alcanzan para preguntarle a MasterShield los valores del mes que viene sin
# apurar a nadie.
DIAS_DE_AVISO = 5


def estado_de_los_precios(hoy: date | None = None) -> dict[str, Any]:
    """De que mes son los precios especiales cargados.

    El precio especial es una promocion mensual. Cuando el mes termina el
    codigo vuelve solo al precio normal —el comportamiento seguro— pero deja de
    ofrecer la promocion sin que nadie se entere. Esto lo hace visible.
    """
    vigencia = configuracion().get("vigencia_precio_especial")
    ahora = hoy or datetime.now(ZoneInfo("America/Guayaquil")).date()
    mes_actual = f"{ahora.year:04d}-{ahora.month:02d}"

    if not vigencia:
        return {"vigencia": None, "vigente": False, "mes_actual": mes_actual}

    vigente = str(vigencia) == mes_actual
    # Cuantos dias faltan para que termine el mes de la promocion.
    ultimo = monthrange(ahora.year, ahora.month)[1]
    quedan = ultimo - ahora.day if vigente else None

    return {
        "vigencia": str(vigencia),
        "vigente": vigente,
        "mes_actual": mes_actual,
        "dias_que_quedan": quedan,
    }


def alertas(datos: dict[str, Any]) -> list[str]:
    """Lo que hay que mirar, en castellano.

    Un tablero de numeros crudos obliga a saber cual esta mal, y eso lo sabe
    quien escribio el sistema, no quien lo mira un martes a la mañana.
    """
    avisos = []

    agotadas = datos["crm"]["agotadas"]
    if agotadas:
        avisos.append(
            f"{agotadas} conversacion(es) agotaron los reintentos del CRM: el lead "
            "no llego a Kommo y ya no se reintenta solo"
        )

    vencidas = datos["crm"]["vencidas_sin_reintentar"]
    if vencidas:
        avisos.append(
            f"{vencidas} sincronizacion(es) del CRM estan vencidas y sin tomar: "
            "puede que el loop de reintentos no este corriendo"
        )

    trabados = datos["turnos"]["trabados"]
    if trabados:
        avisos.append(
            f"{trabados} conversacion(es) con turnos que fallan y se reintentan: "
            "hay gente esperando respuesta"
        )

    precios = datos.get("precios") or {}
    if precios.get("vigencia") and not precios["vigente"]:
        avisos.append(
            f"los precios especiales cargados son de {precios['vigencia']} y "
            f"estamos en {precios['mes_actual']}: el agente esta cotizando al "
            "precio normal. Pedirle a MasterShield los valores del mes y "
            "actualizar config/productos.yaml"
        )
    elif precios.get("dias_que_quedan") is not None and \
            precios["dias_que_quedan"] <= DIAS_DE_AVISO:
        avisos.append(
            f"la promocion de {precios['vigencia']} termina en "
            f"{precios['dias_que_quedan']} dia(s): conviene pedir los valores "
            "del mes que viene antes de que venza"
        )

    derivadas = datos["conversaciones"].get("derivada", 0)
    if derivadas:
        avisos.append(f"{derivadas} conversacion(es) esperando a una persona")

    return avisos
