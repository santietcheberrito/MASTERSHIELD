"""Configuracion del servicio, leida de variables de entorno."""

from __future__ import annotations

import re
from datetime import datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Credenciales que todavia no tenemos al arrancar el proyecto: se completan
# cuando el cliente las entrega. El servicio levanta igual, pero avisa cuales
# faltan para que no se descubra recien cuando falla una llamada.
CREDENCIALES_OPCIONALES = (
    "anthropic_api_key",
    "kommo_subdomain",
    "kommo_access_token",
    "bsp_api_url",
    "bsp_token",
    "bsp_webhook_secret",
)

_HORARIO = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str

    anthropic_api_key: str = ""

    kommo_subdomain: str = ""
    kommo_access_token: str = ""

    bsp_api_url: str = ""
    bsp_token: str = ""
    bsp_webhook_secret: str = ""

    ventana_buffer_seg: int = Field(default=6, gt=0, le=120)
    horario_atencion: str = "09:00-18:00"
    tz: str = "America/Argentina/Buenos_Aires"
    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def _validar_database_url(cls, valor: str) -> str:
        if not valor.startswith(("postgresql://", "postgres://")):
            raise ValueError(
                "DATABASE_URL tiene que empezar con postgresql:// o postgres://"
            )
        return valor

    @field_validator("horario_atencion")
    @classmethod
    def _validar_horario(cls, valor: str) -> str:
        if not _HORARIO.match(valor):
            raise ValueError("HORARIO_ATENCION tiene que tener el formato HH:MM-HH:MM")
        inicio, fin = (time.fromisoformat(p) for p in valor.split("-"))
        if inicio >= fin:
            raise ValueError("HORARIO_ATENCION: la hora de inicio tiene que ser menor a la de fin")
        return valor

    @field_validator("log_level")
    @classmethod
    def _validar_log_level(cls, valor: str) -> str:
        nivel = valor.upper()
        if nivel not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"LOG_LEVEL invalido: {valor}")
        return nivel

    @model_validator(mode="after")
    def _validar_tz(self) -> "Settings":
        try:
            ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"TZ invalida: {self.tz}") from e
        return self

    @property
    def zona(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def horario(self) -> tuple[time, time]:
        inicio, fin = self.horario_atencion.split("-")
        return time.fromisoformat(inicio), time.fromisoformat(fin)

    @property
    def credenciales_faltantes(self) -> list[str]:
        return [c.upper() for c in CREDENCIALES_OPCIONALES if not getattr(self, c)]

    def esta_en_horario(self, momento: datetime | None = None) -> bool:
        """Horario comercial: lunes a viernes dentro de la franja configurada.

        El agente atiende igual fuera de horario; esto se usa para decidir
        cuando se agenda la tarea de llamado en Kommo.
        """
        momento = (momento or datetime.now(self.zona)).astimezone(self.zona)
        if momento.weekday() >= 5:
            return False
        inicio, fin = self.horario
        return inicio <= momento.time() < fin


@lru_cache
def obtener_settings() -> Settings:
    return Settings()
