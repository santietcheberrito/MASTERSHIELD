"""Long polling de Telegram, para desarrollo local.

Evita tener que exponer una URL publica con un tunel mientras se desarrolla.
En produccion se usa el webhook, que es lo unico que soporta WhatsApp.

El offset se lleva en memoria a proposito. Si el proceso se reinicia, Telegram
vuelve a mandar los updates que no alcanzamos a confirmar, y el indice unico
sobre `mensajes.id_externo` los descarta solo. No hay estado que persistir.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app import ingesta
from app.canales import telegram

logger = logging.getLogger(__name__)

ESPERA_LARGA = 25  # segundos que Telegram retiene la peticion si no hay nada


class Poller:
    def __init__(self, token: str, ventana_seg: int) -> None:
        self.token = token
        self.ventana_seg = ventana_seg
        self._offset: int | None = None
        self._tarea: asyncio.Task | None = None
        self._corriendo = False

    @property
    def _url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def arrancar(self) -> None:
        if self._tarea is not None:
            return
        self._corriendo = True
        self._tarea = asyncio.create_task(self._loop(), name="poller-telegram")
        logger.info("poller de Telegram arrancado")

    async def detener(self) -> None:
        self._corriendo = False
        if self._tarea is not None:
            self._tarea.cancel()
            try:
                await self._tarea
            except asyncio.CancelledError:
                pass
            self._tarea = None
            logger.info("poller de Telegram detenido")

    async def _loop(self) -> None:
        async with httpx.AsyncClient(timeout=ESPERA_LARGA + 30) as cliente:
            while self._corriendo:
                try:
                    await self.una_vuelta(cliente)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("error en el poller de Telegram")
                    # Si Telegram o la red fallan, no martillar.
                    await asyncio.sleep(5)

    async def una_vuelta(self, cliente: httpx.AsyncClient) -> int:
        """Trae los updates pendientes y los registra. Devuelve cuantos proceso."""
        parametros: dict[str, object] = {
            "timeout": ESPERA_LARGA,
            "allowed_updates": '["message"]',
        }
        if self._offset is not None:
            parametros["offset"] = self._offset

        respuesta = await cliente.get(f"{self._url}/getUpdates", params=parametros)
        respuesta.raise_for_status()
        cuerpo = respuesta.json()

        if not cuerpo.get("ok"):
            logger.error("getUpdates devolvio error: %s", cuerpo.get("description"))
            return 0

        updates = cuerpo.get("result", [])
        for update in updates:
            # El offset se avanza aunque el update no nos interese o falle: si
            # no, Telegram lo repite para siempre y bloquea la cola.
            self._offset = update["update_id"] + 1
            mensaje = telegram.parsear(update)
            if mensaje is None:
                continue
            try:
                await ingesta.registrar(mensaje, self.ventana_seg)
            except Exception:
                logger.exception("no se pudo registrar un update de Telegram")

        return len(updates)
