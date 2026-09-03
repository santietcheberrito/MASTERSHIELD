"""El agente vuelve solo despues de que un vendedor intervino.

Cuando alguien del equipo contesta a mano, el agente se calla: dos voces sobre
el mismo numero es lo peor que puede pasarle a una conversacion de venta. Pero
callarse para siempre tampoco sirve. A la semana siguiente el mismo cliente
escribe por otra cosa, la conversacion sigue en `pausada`, y no le contesta
nadie.

Esto le pone final a la pausa. Lo que **no** hace es escribir: al despertar, la
conversacion vuelve a `activa` y solo se agenda un turno si el cliente dejo algo
sin contestar. Si el asesor cerro el tema, el agente no reaparece a saludar.

Y "dejo algo sin contestar" no alcanza como criterio, porque un "gracias" no es
una consulta. Esa parte no se decide aca: se le pasa al agente con el contexto
de que hubo un asesor en el medio, y el agente decide si contesta o se queda
callado con `cerrar_sin_responder`.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from app import db
from app.config import obtener_settings

logger = logging.getLogger(__name__)

# Las pausas se miden en horas: mirar cada minuto es de sobra.
INTERVALO_SEGUNDOS = 60.0

# Vuelve a `activa` y limpia el vencimiento en la misma sentencia, asi dos
# procesos no pueden despertar la misma conversacion durante un deploy.
_DESPERTAR = """
    UPDATE conversaciones SET
        estado        = 'activa',
        pausada_hasta = NULL
    WHERE id IN (
        SELECT id FROM conversaciones
        WHERE estado = 'pausada' AND pausada_hasta IS NOT NULL
          AND pausada_hasta <= now()
        ORDER BY pausada_hasta
        FOR UPDATE SKIP LOCKED
        LIMIT $1
    )
    RETURNING id
"""

# Solo se agenda un turno si el cliente escribio algo que nadie contesto. El
# mensaje del vendedor entra a `mensajes` ya marcado como procesado, asi que lo
# que quede sin procesar es del cliente y llego despues.
_AGENDAR_SI_QUEDO_ALGO = """
    INSERT INTO pendientes (conversacion_id, procesar_despues)
    SELECT $1, now() + make_interval(secs => $2)
    WHERE EXISTS (
        SELECT 1 FROM mensajes
        WHERE conversacion_id = $1 AND rol = 'cliente' AND NOT procesado
    )
    ON CONFLICT (conversacion_id) DO NOTHING
    RETURNING conversacion_id
"""


class Despertador:
    def __init__(self, intervalo: float = INTERVALO_SEGUNDOS, lote: int = 20) -> None:
        self.intervalo = intervalo
        self.lote = lote
        self._corriendo = False
        self._tarea: asyncio.Task | None = None

    def arrancar(self) -> None:
        if self._tarea is not None:
            return
        self._corriendo = True
        self._tarea = asyncio.create_task(self._loop(), name="despertador")
        logger.info("despertador de pausas arrancado")

    async def detener(self) -> None:
        self._corriendo = False
        if self._tarea is not None:
            self._tarea.cancel()
            try:
                await self._tarea
            except asyncio.CancelledError:
                pass
            self._tarea = None
            logger.info("despertador de pausas detenido")

    async def _loop(self) -> None:
        while self._corriendo:
            try:
                await self.una_vuelta()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("error en la vuelta del despertador")
            await asyncio.sleep(self.intervalo)

    async def una_vuelta(self) -> int:
        """Despierta las pausas vencidas. Devuelve cuantas despertó."""
        try:
            filas = await db.consultar(_DESPERTAR, self.lote)
        except asyncpg.PostgresError:
            logger.exception("no se pudieron despertar las conversaciones pausadas")
            return 0

        demora = obtener_settings().demora_respuesta_min_seg

        for fila in filas:
            agendada = await db.valor(_AGENDAR_SI_QUEDO_ALGO, fila["id"], demora)
            logger.info(
                "pausa vencida | conversacion=%s %s",
                fila["id"],
                "el cliente dejo algo sin contestar, se evalua"
                if agendada else "nadie quedo esperando, el agente no reaparece",
            )

        return len(filas)
