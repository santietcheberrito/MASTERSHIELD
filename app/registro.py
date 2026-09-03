"""Logging estructurado.

En la terminal los logs se leen; en produccion se consultan. Son dos cosas
distintas y por eso hay dos formatos: coloreado y alineado cuando se desarrolla,
una linea de JSON por evento cuando corre en Railway.

Lo que cambia de verdad no es el formato sino el contexto. `con_conversacion`
mete el id en un contextvar, y desde ahi **todas** las lineas del turno lo
llevan solas: el worker, el loop, las herramientas, la sincronizacion con el
CRM. Sin eso hay que acordarse de pasarlo en cada `logger.info`, y donde uno se
olvida esa linea queda huerfana, que es justo la que despues hace falta.

Los modulos siguen usando `logging.getLogger(__name__)` de siempre. structlog
se engancha como formateador del logging estandar, asi que tambien salen
estructurados los logs de httpx, uvicorn y asyncpg, que son los que avisan
cuando algo se rompe abajo.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Iterator
from typing import Any

import structlog

# Lo que comparten todas las lineas de un mismo turno. Se limpia al terminar.
_CONTEXTO = structlog.contextvars


def configurar(nivel: str, formato: str, flujo: Any = None) -> None:
    """Deja el logging estandar saliendo por structlog.

    `formato` es "json" o "texto". En Railway va json; en la terminal, texto.
    `flujo` es a donde se escribe: por defecto stderr. Se pasa en los tests,
    que es la unica forma de mirar la salida real sin pelearse con la captura
    de pytest.
    """
    compartidos: list[Any] = [
        _CONTEXTO.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        # Sin esto, un `logger.exception` pierde el traceback al pasar a JSON.
        structlog.processors.format_exc_info,
    ]

    if formato == "json":
        final: Any = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        final = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=compartidos + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formateador = structlog.stdlib.ProcessorFormatter(
        # `positional_arguments` resuelve los "%s" de los logs que ya existen,
        # que son casi todos: sin esto saldrian con los marcadores sin llenar.
        foreign_pre_chain=compartidos + [
            structlog.stdlib.PositionalArgumentsFormatter(),
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            final,
        ],
    )

    manejador = logging.StreamHandler(flujo)
    manejador.setFormatter(formateador)

    raiz = logging.getLogger()
    raiz.handlers.clear()
    raiz.addHandler(manejador)
    raiz.setLevel(getattr(logging, nivel))

    # uvicorn trae sus propios handlers y duplicaria cada linea.
    for nombre in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        ruidoso = logging.getLogger(nombre)
        ruidoso.handlers.clear()
        ruidoso.propagate = True


@contextlib.contextmanager
def con_conversacion(conversacion_id: int, **extra: Any) -> Iterator[None]:
    """Ata el id de la conversacion a todo lo que se loguee adentro.

    Es lo que permite reconstruir un turno entero: las quince lineas que genera
    —el turno tomado, las herramientas, los tokens, los mensajes enviados—
    quedan unidas por un campo, en vez de por la hora en que salieron.
    """
    tokens = _CONTEXTO.bind_contextvars(conversacion_id=conversacion_id, **extra)
    try:
        yield
    finally:
        _CONTEXTO.reset_contextvars(**tokens)
