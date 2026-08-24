"""Loop que procesa el buffer de mensajes.

Corre en el mismo proceso que FastAPI, como tarea de arranque. Cada vuelta
toma las conversaciones cuya ventana de debounce ya vencio, arma un unico turno
con todos los mensajes acumulados y lo procesa.

Por ahora lo unico que hace con el turno es loguearlo. El agente se conecta en
la sesion 4.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from app import db
from app.agente import loop
from app.canales import telegram
from app.config import obtener_settings

logger = logging.getLogger(__name__)

# Cuanto se reserva una conversacion mientras se procesa. Tiene que ser mayor
# que el turno mas lento imaginable (el agente puede encadenar varias
# herramientas) y menor que la paciencia de una persona esperando respuesta.
LOCK_SEGUNDOS = 120

# Un turno que falla se reintenta, pero no para siempre: despues de esto se
# posterga una hora para no gastar el loop en algo que esta roto.
MAX_INTENTOS = 3
POSTERGACION_SEGUNDOS = 3600

_TOMAR = """
    UPDATE pendientes SET
        bloqueado_hasta = now() + make_interval(secs => $1),
        intentos        = intentos + 1
    WHERE conversacion_id IN (
        SELECT conversacion_id FROM pendientes
        WHERE procesar_despues <= now()
          AND (bloqueado_hasta IS NULL OR bloqueado_hasta < now())
        ORDER BY procesar_despues
        -- SKIP LOCKED porque durante un deploy de Railway conviven el
        -- contenedor viejo y el nuevo unos segundos, y los dos miran esta tabla.
        FOR UPDATE SKIP LOCKED
        LIMIT $2
    )
    RETURNING conversacion_id, procesar_despues, intentos
"""

_MENSAJES_DEL_TURNO = """
    SELECT id, tipo, contenido
    FROM mensajes
    WHERE conversacion_id = $1 AND NOT procesado AND rol = 'cliente'
    ORDER BY id
"""

# Solo los que entraron en este turno. Si llego uno mientras procesabamos,
# queda sin marcar y lo levanta el turno siguiente.
_MARCAR_PROCESADOS = """
    UPDATE mensajes SET procesado = true
    WHERE conversacion_id = $1 AND id <= $2 AND NOT procesado AND rol = 'cliente'
"""

# Solo borra si nadie corrio la ventana mientras trabajabamos. Si llego un
# mensaje nuevo, procesar_despues avanzo y el DELETE no afecta ninguna fila:
# la conversacion queda pendiente y se procesa de nuevo con el mensaje nuevo.
_BORRAR_PENDIENTE = """
    DELETE FROM pendientes
    WHERE conversacion_id = $1 AND procesar_despues <= $2
"""

_LIBERAR = """
    UPDATE pendientes SET bloqueado_hasta = NULL, intentos = 0, ultimo_error = NULL
    WHERE conversacion_id = $1
"""

_POSTERGAR = """
    UPDATE pendientes SET
        bloqueado_hasta  = NULL,
        procesar_despues = now() + make_interval(secs => $2),
        ultimo_error     = $3
    WHERE conversacion_id = $1
"""

_REGISTRAR_ERROR = """
    UPDATE pendientes SET bloqueado_hasta = NULL, ultimo_error = $2
    WHERE conversacion_id = $1
"""


@dataclass
class Turno:
    conversacion_id: int
    ids_mensajes: list[int]
    texto: str
    canal: str = ""
    identificador: str = ""

    @property
    def ultimo_id(self) -> int:
        return self.ids_mensajes[-1]


_DATOS_CONVERSACION = "SELECT canal, identificador FROM conversaciones WHERE id = $1"

_GUARDAR_RESPUESTA = """
    INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo)
    VALUES ($1, 'agente', $2, $3)
"""


def armar_turno(conversacion_id: int, filas: list[asyncpg.Record]) -> Turno | None:
    """Junta la rafaga en un solo texto.

    "buenas" / "necesito lamina" / "para una oficina en Cumbaya" llegan como
    tres mensajes y el agente los tiene que ver como una sola intervencion.
    """
    if not filas:
        return None

    partes = []
    for fila in filas:
        if fila["tipo"] == "texto":
            if fila["contenido"]:
                partes.append(fila["contenido"])
        elif fila["contenido"]:
            # Una foto con epigrafe: el texto importa y el adjunto tambien.
            partes.append(f"[{fila['tipo']}] {fila['contenido']}")
        else:
            partes.append(f"[{fila['tipo']}]")

    return Turno(
        conversacion_id=conversacion_id,
        ids_mensajes=[f["id"] for f in filas],
        texto="\n".join(partes),
    )


async def procesar_turno(turno: Turno) -> None:
    """Corre el agente y manda la respuesta.

    Sin delays ni partido de mensajes: eso es de la sesion 6. El cliente pidio
    ademas un retraso de 60 a 120 segundos antes de responder, que tambien va
    ahi junto con la supersesion.
    """
    logger.info(
        "turno armado | conversacion=%s mensajes=%s | %s",
        turno.conversacion_id,
        len(turno.ids_mensajes),
        turno.texto.replace("\n", " / "),
    )

    respuesta = await loop.responder(turno.conversacion_id)

    logger.info(
        "respuesta | conversacion=%s iteraciones=%s herramientas=%s "
        "tokens_in=%s tokens_out=%s cache=%s | %s",
        turno.conversacion_id,
        respuesta.iteraciones,
        respuesta.herramientas_usadas or "-",
        respuesta.tokens_entrada,
        respuesta.tokens_salida,
        respuesta.tokens_cache_leidos,
        respuesta.texto.replace("\n", " / "),
    )

    if not respuesta.texto:
        # Puede pasar si el modelo solo llamo herramientas y se agotaron las
        # iteraciones. No se manda nada, pero queda el log para investigarlo.
        logger.warning("el agente no produjo texto | conversacion=%s", turno.conversacion_id)
        return

    id_externo = await _enviar(turno, respuesta.texto)
    await db.ejecutar(_GUARDAR_RESPUESTA, turno.conversacion_id, respuesta.texto, id_externo)


async def _enviar(turno: Turno, texto: str) -> str | None:
    """Manda la respuesta por el canal de la conversacion."""
    settings = obtener_settings()
    if turno.canal == "telegram":
        if not settings.telegram_bot_token:
            logger.error("sin TELEGRAM_BOT_TOKEN: no se puede responder")
            return None
        return await telegram.enviar(
            settings.telegram_bot_token, turno.identificador, texto
        )
    logger.error("canal sin implementar: %s", turno.canal)
    return None


class Worker:
    def __init__(self, intervalo: float = 1.0, lote: int = 10) -> None:
        self.intervalo = intervalo
        self.lote = lote
        self._corriendo = False
        self._tarea: asyncio.Task | None = None

    def arrancar(self) -> None:
        if self._tarea is not None:
            return
        self._corriendo = True
        self._tarea = asyncio.create_task(self._loop(), name="worker")
        logger.info("worker arrancado")

    async def detener(self) -> None:
        self._corriendo = False
        if self._tarea is not None:
            self._tarea.cancel()
            try:
                await self._tarea
            except asyncio.CancelledError:
                pass
            self._tarea = None
            logger.info("worker detenido")

    async def _loop(self) -> None:
        while self._corriendo:
            try:
                await self.una_vuelta()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Una vuelta que falla no puede matar el loop: si el worker
                # muere, el sistema deja de contestar y nadie se entera.
                logger.exception("error en la vuelta del worker")
            await asyncio.sleep(self.intervalo)

    async def una_vuelta(self) -> int:
        """Procesa las conversaciones vencidas. Devuelve cuantas tomo."""
        try:
            tomadas = await db.consultar(_TOMAR, LOCK_SEGUNDOS, self.lote)
        except asyncpg.PostgresError:
            logger.exception("no se pudieron tomar conversaciones pendientes")
            return 0

        for fila in tomadas:
            await self._procesar_una(
                fila["conversacion_id"], fila["procesar_despues"], fila["intentos"]
            )
        return len(tomadas)

    async def _procesar_una(
        self, conversacion_id: int, procesar_despues: datetime, intentos: int
    ) -> None:
        try:
            filas = await db.consultar(_MENSAJES_DEL_TURNO, conversacion_id)
            turno = armar_turno(conversacion_id, filas)

            if turno is not None:
                conversacion = await db.consultar_una(_DATOS_CONVERSACION, conversacion_id)
                turno.canal = conversacion["canal"]
                turno.identificador = conversacion["identificador"]

            if turno is None:
                # No hay nada que contestar: la fila quedo huerfana.
                await db.ejecutar(_BORRAR_PENDIENTE, conversacion_id, procesar_despues)
                return

            await procesar_turno(turno)

            await db.ejecutar(_MARCAR_PROCESADOS, conversacion_id, turno.ultimo_id)
            borrado = await db.ejecutar(_BORRAR_PENDIENTE, conversacion_id, procesar_despues)
            if borrado == "DELETE 0":
                # Llego un mensaje mientras procesabamos y corrio la ventana.
                # Se libera el lock y se procesa de nuevo cuando venza.
                await db.ejecutar(_LIBERAR, conversacion_id)
                logger.info(
                    "llego un mensaje durante el turno | conversacion=%s", conversacion_id
                )
        except Exception as e:
            logger.exception("fallo el turno | conversacion=%s", conversacion_id)
            detalle = f"{type(e).__name__}: {e}"[:500]
            if intentos >= MAX_INTENTOS:
                await db.ejecutar(
                    _POSTERGAR, conversacion_id, POSTERGACION_SEGUNDOS, detalle
                )
                logger.error(
                    "conversacion postergada tras %s intentos | conversacion=%s",
                    intentos,
                    conversacion_id,
                )
            else:
                await db.ejecutar(_REGISTRAR_ERROR, conversacion_id, detalle)
