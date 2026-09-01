"""Configuracion del servicio, leida de variables de entorno."""

from __future__ import annotations

import re
from datetime import datetime, time
from functools import lru_cache
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Credenciales que todavia no tenemos al arrancar el proyecto: se completan
# cuando el cliente las entrega. El servicio levanta igual, pero avisa cuales
# faltan para que no se descubra recien cuando falla una llamada.
CREDENCIALES_OPCIONALES = (
    "anthropic_api_key",
    "telegram_bot_token",
    "notion_token",
    "kommo_subdomain",
    "kommo_access_token",
    "bsp_api_url",
    "bsp_token",
    "bsp_webhook_secret",
)

_HORARIO = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")
_PREFIJO = re.compile(r"^\+\d{1,3}$")


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

    # Telegram es el canal de la etapa de pruebas, antes de conectar la Cloud
    # API de WhatsApp.
    telegram_bot_token: str = ""
    # Solo hace falta en modo webhook: es el valor que Telegram devuelve en la
    # cabecera X-Telegram-Bot-Api-Secret-Token, tal cual se registro en setWebhook.
    telegram_webhook_secret: str = ""
    # polling: getUpdates, sin URL publica, para desarrollo local.
    # webhook: Telegram nos pega, hace falta URL publica. Es lo de produccion.
    # off: no se escucha Telegram.
    telegram_modo: str = "polling"

    # CRM de prueba mientras no haya acceso a Kommo. La base de Notion tiene
    # que estar compartida con la integracion o la API devuelve 404.
    notion_token: str = ""
    notion_data_source_id: str = ""

    # El cliente pidio que el agente no conteste al instante sino que espere
    # entre 1 y 2 minutos. Ese retraso ES la ventana del buffer: se sortea por
    # mensaje y corre `procesar_despues`, asi el agente no empieza a pensar
    # hasta que paso el tiempo. Ver app/humanizacion.py.
    demora_respuesta_min_seg: int = Field(default=60, gt=0, le=600)
    demora_respuesta_max_seg: int = Field(default=120, gt=0, le=600)
    horario_atencion: str = "09:00-18:00"

    # El cliente opera en Quito. La zona horaria no es un detalle de formato:
    # el horario de atencion y el agendado de la tarea de llamado se evaluan
    # siempre en hora de Ecuador, nunca en la del servidor (Railway corre en UTC).
    tz: str = "America/Guayaquil"
    pais: str = "EC"
    prefijo_telefonico: str = "+593"

    # Sonnet para el agente: la latencia importa en un chat y el razonamiento
    # aca es acotado. Se cambia por entorno para comparar contra Opus.
    modelo_agente: str = "claude-sonnet-5"
    max_iteraciones_herramientas: int = Field(default=6, gt=0, le=20)

    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def _validar_database_url(cls, valor: str) -> str:
        if not valor.startswith(("postgresql://", "postgres://")):
            raise ValueError(
                "DATABASE_URL tiene que empezar con postgresql:// o postgres://"
            )
        # El dashboard de Supabase muestra la clave como [YOUR-PASSWORD]. Si se
        # reemplaza el texto pero quedan los corchetes, o se pierde el ':' entre
        # usuario y clave, urlparse revienta con un ValueError sobre direcciones
        # IPv6 que no tiene nada que ver con el problema real.
        try:
            partes = urlparse(valor)
            host = partes.hostname
        except ValueError as e:
            raise ValueError(
                "DATABASE_URL mal formada. Revisar que no hayan quedado los "
                "corchetes de [YOUR-PASSWORD] y que haya ':' entre usuario y clave"
            ) from e
        if not host:
            raise ValueError("DATABASE_URL no tiene host")
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

    @field_validator("pais")
    @classmethod
    def _validar_pais(cls, valor: str) -> str:
        codigo = valor.upper()
        if len(codigo) != 2 or not codigo.isalpha():
            raise ValueError("PAIS tiene que ser un codigo ISO de dos letras, por ejemplo EC")
        return codigo

    @field_validator("prefijo_telefonico")
    @classmethod
    def _validar_prefijo(cls, valor: str) -> str:
        if not _PREFIJO.match(valor):
            raise ValueError("PREFIJO_TELEFONICO tiene que ser + seguido de 1 a 3 digitos, por ejemplo +593")
        return valor

    @model_validator(mode="after")
    def _validar_demoras(self) -> "Settings":
        if self.demora_respuesta_min_seg > self.demora_respuesta_max_seg:
            raise ValueError("DEMORA_RESPUESTA_MIN_SEG no puede ser mayor que la maxima")
        return self

    @field_validator("telegram_modo")
    @classmethod
    def _validar_telegram_modo(cls, valor: str) -> str:
        modo = valor.lower()
        if modo not in {"polling", "webhook", "off"}:
            raise ValueError("TELEGRAM_MODO tiene que ser polling, webhook u off")
        return modo

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
