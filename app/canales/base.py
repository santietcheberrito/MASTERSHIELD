"""Forma normalizada de un mensaje entrante, comun a todos los canales.

El worker, el agente, el scoring y Kommo trabajan sobre esto y no saben de que
canal vino. Es la unica pieza que hay que reimplementar para agregar WhatsApp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TIPOS = ("texto", "imagen", "audio", "video", "documento", "ubicacion", "otro")


@dataclass(frozen=True)
class MensajeEntrante:
    canal: str
    # Con quien es la conversacion: el chat_id en Telegram, el telefono en
    # WhatsApp. Es la mitad de la clave de `conversaciones`.
    identificador: str
    # Id del mensaje, ya calificado por canal y chat. Ver migracion 003: el
    # message_id de Telegram es unico por chat, no globalmente.
    id_externo: str
    texto: str = ""
    tipo: str = "texto"
    nombre: str | None = None
    # WhatsApp lo trae siempre; en Telegram aparece solo si la persona comparte
    # su contacto. Sin telefono no hay a quien llamar, asi que sobre Telegram
    # se convierte en un dato a relevar.
    telefono: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.tipo not in TIPOS:
            raise ValueError(f"tipo de mensaje desconocido: {self.tipo}")
        if not self.identificador or not self.id_externo:
            raise ValueError("identificador e id_externo son obligatorios")
