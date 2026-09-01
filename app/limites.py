"""Deteccion de uso anomalo.

Cada mensaje que entra es una llamada al modelo, y cada llamada cuesta. Sin un
tope, cualquiera con el numero puede hacernos una factura: no hace falta mala
intencion, alcanza con un script mal escrito.

Lo que hace esto no es rechazar al cliente: es dejar de gastar y avisarle a una
persona para que mire quien es y que esta pasando. Si resulta ser un cliente
legitimo, un asesor retoma la conversacion.
"""

from __future__ import annotations

import logging

from app import db

logger = logging.getLogger(__name__)

_CONTEOS = """
    SELECT
        count(*) FILTER (WHERE creado_en > now() - interval '1 hour')  AS ultima_hora,
        count(*) FILTER (WHERE creado_en > now() - interval '1 minute') AS ultimo_minuto,
        count(*)                                                        AS total,
        max(length(contenido)) FILTER (WHERE creado_en > now() - interval '1 hour')
                                                                        AS mas_largo
    FROM mensajes
    WHERE conversacion_id = $1 AND rol = 'cliente'
"""


async def revisar(
    conversacion_id: int,
    *,
    por_hora: int,
    por_minuto: int,
    largo_maximo: int,
    total_maximo: int,
) -> str | None:
    """Devuelve el motivo si algo se ve raro, o None si esta todo bien."""
    fila = await db.consultar_una(_CONTEOS, conversacion_id)
    if fila is None:
        return None

    if fila["ultimo_minuto"] >= por_minuto:
        return (
            f"{fila['ultimo_minuto']} mensajes en un minuto: parece automatizado, "
            "no alguien escribiendo"
        )
    if fila["ultima_hora"] >= por_hora:
        return f"{fila['ultima_hora']} mensajes en la ultima hora"
    if (fila["mas_largo"] or 0) >= largo_maximo:
        return (
            f"un mensaje de {fila['mas_largo']} caracteres: nadie escribe eso por chat"
        )
    if fila["total"] >= total_maximo:
        return f"{fila['total']} mensajes en total: la conversacion no termina nunca"

    return None
