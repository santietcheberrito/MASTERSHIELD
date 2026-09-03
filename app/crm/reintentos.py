"""El loop que retoma las sincronizaciones que fallaron.

`sincronizar` deja la conversacion marcada cuando el CRM no responde, y hasta
ahora nadie leia esa marca: una caida de Kommo de cinco minutos hacia
desaparecer el lead en silencio, con la fila diciendo "pendiente" para siempre.
Esto es la otra mitad de esa promesa.

Va aparte del worker a proposito. El worker atiende gente que esta escribiendo
y corre cada segundo; esto habla con un servicio de terceros que puede tardar,
y no tiene por que competir por el mismo turno.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from app import db
from app.crm.sincronizacion import sincronizar

logger = logging.getLogger(__name__)

# Cada cuanto se mira si hay algo vencido. Los reintentos se cuentan en minutos
# y horas, asi que mirar seguido no aporta nada.
INTERVALO_SEGUNDOS = 60.0

# Cuantas se toman por vuelta. Cada una son varias llamadas HTTP a Kommo y a
# Notion; un lote grande solo sirve para que el CRM nos limite.
LOTE = 5

# Mientras se intenta, la fila se corre hacia adelante para que otro proceso no
# la tome. Tiene que ser mayor que lo que tarda una sincronizacion completa
# —contacto, lead, nota, tarea, en dos destinos— con sus reintentos HTTP.
RESERVA_SEGUNDOS = 300

# Se reserva y se devuelve el id en una sola sentencia: entre el SELECT y el
# UPDATE por separado hay lugar para que dos procesos tomen la misma fila, y
# durante un deploy de Railway conviven el contenedor viejo y el nuevo.
_TOMAR = """
    UPDATE conversaciones SET
        crm_reintentar_en = now() + make_interval(secs => $1)
    WHERE id IN (
        SELECT id FROM conversaciones
        WHERE crm_pendiente
          AND crm_reintentar_en IS NOT NULL
          AND crm_reintentar_en <= now()
        ORDER BY crm_reintentar_en
        FOR UPDATE SKIP LOCKED
        LIMIT $2
    )
    RETURNING id, crm_intentos
"""


class Reintentos:
    def __init__(self, intervalo: float = INTERVALO_SEGUNDOS, lote: int = LOTE) -> None:
        self.intervalo = intervalo
        self.lote = lote
        self._corriendo = False
        self._tarea: asyncio.Task | None = None

    def arrancar(self) -> None:
        if self._tarea is not None:
            return
        self._corriendo = True
        self._tarea = asyncio.create_task(self._loop(), name="reintentos-crm")
        logger.info("loop de reintentos del CRM arrancado")

    async def detener(self) -> None:
        self._corriendo = False
        if self._tarea is not None:
            self._tarea.cancel()
            try:
                await self._tarea
            except asyncio.CancelledError:
                pass
            self._tarea = None
            logger.info("loop de reintentos del CRM detenido")

    async def _loop(self) -> None:
        while self._corriendo:
            try:
                await self.una_vuelta()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Igual que en el worker: una vuelta que falla no puede matar
                # el loop, o volvemos exactamente al problema que esto arregla.
                logger.exception("error en la vuelta de reintentos del CRM")
            await asyncio.sleep(self.intervalo)

    async def una_vuelta(self) -> int:
        """Reintenta las que estan vencidas. Devuelve cuantas tomo."""
        try:
            tomadas = await db.consultar(_TOMAR, RESERVA_SEGUNDOS, self.lote)
        except asyncpg.PostgresError:
            logger.exception("no se pudieron tomar conversaciones para reintentar")
            return 0

        for fila in tomadas:
            await self._reintentar_una(fila["id"], fila["crm_intentos"])

        return len(tomadas)

    async def _reintentar_una(self, conversacion_id: int, intentos: int) -> None:
        logger.info(
            "reintentando el CRM | conversacion=%s intento=%s",
            conversacion_id, intentos + 1,
        )
        # `sincronizar` no propaga: si sale bien limpia la marca, y si vuelve a
        # fallar agenda el reintento siguiente con el backoff que corresponda.
        if await sincronizar(conversacion_id):
            logger.info(
                "el CRM acepto la conversacion en el reintento %s | conversacion=%s",
                intentos + 1, conversacion_id,
            )
