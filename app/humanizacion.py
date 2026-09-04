"""Que la conversacion no se lea como un bot.

Dos escalas de tiempo distintas, y conviene no confundirlas:

1. **La demora de respuesta** (60-120s): desde el ultimo mensaje del cliente
   hasta el primero del agente. La pidio el cliente para dar realismo. No se
   implementa como una espera despues de generar: se implementa corriendo
   `procesar_despues` en el buffer, asi el agente ni siquiera empieza a pensar
   hasta que paso el tiempo. Eso hace que la supersesion salga gratis —si llega
   un mensaje nuevo, la ventana se corre y no hay nada generado que descartar—,
   que sobreviva a un reinicio, y que no bloquee al worker.

2. **La pausa entre mensajes partidos** (1.5-7s): el tiempo que tarda una
   persona en tipear la linea siguiente. Esa si es una espera real.
"""

from __future__ import annotations

import random
import re

# Velocidad de tipeo simulada, en caracteres por segundo. Una persona rapida en
# el teclado del telefono anda por ahi.
CPS_MIN = 20
CPS_MAX = 32

# El minimo no sale de cuanto tarda alguien en tipear: sale de cuanto tarda el
# indicador de "escribiendo" en aparecer del otro lado. Con 1.5 segundos, Meta
# no alcanzaba a propagarlo y el segundo y el tercer mensaje aparecian de la
# nada, que es justo lo que el cliente pidio evitar. Tres segundos le dan
# tiempo a mostrarse antes de que llegue el globo.
# Es un rango y no un valor porque el piso lo toca casi todo mensaje corto, y
# tres segundos exactos, mensaje tras mensaje, es un patron tan detectable como
# contestar al instante.
# El piso tiene que aguantar dos cosas: el respiro que se toma el worker antes
# de encender el indicador (~1.2s) y que despues el indicador se vea. Con 3.5
# quedan al menos 2.3 segundos de "escribiendo" visible.
PAUSA_MIN = 3.5
PAUSA_MIN_MAX = 5.0
PAUSA_MAX = 7.0

# Un mensaje mas largo que esto se parte aunque venga en una sola linea. El
# cliente pidio bloques cortos: en el telefono, un parrafo de 220 caracteres
# ocupa media pantalla y se lee como un folleto, no como alguien contestando.
LARGO_COMODO = 150
# Nunca mas de esto: tres globos seguidos ya es mucho.
MAX_PARTES = 3

# El punto y coma cuenta como fin de oracion a proposito: el modelo lo usa para
# encadenar dos ideas y asi esquiva el corte, dejando globos de 200 caracteres
# donde deberia haber dos.
_FIN_DE_ORACION = re.compile(r"(?<=[.!?…;])\s+")


def demora_de_respuesta(minimo: int, maximo: int) -> int:
    """Cuanto esperar antes de contestar. Sorteado, nunca fijo.

    Un retraso constante de 90 segundos es tan detectable como contestar al
    instante: es un patron.
    """
    return random.randint(minimo, maximo)


def demora_configurada() -> int:
    """La demora de respuesta segun la configuracion. La usan el webhook y el
    poller, que son los que agendan el turno."""
    from app.config import obtener_settings

    settings = obtener_settings()
    return demora_de_respuesta(
        settings.demora_respuesta_min_seg, settings.demora_respuesta_max_seg
    )


def demora_de_escritura(texto: str) -> float:
    """Cuanto tarda en 'escribir' un mensaje, segun su largo.

    El piso se sortea: un mensaje corto siempre lo toca, y si fuera fijo, cada
    "¿Con quien tengo el gusto?" saldria exactamente a los tres segundos.
    """
    segundos = len(texto) / random.uniform(CPS_MIN, CPS_MAX)
    piso = random.uniform(PAUSA_MIN, PAUSA_MIN_MAX)
    return max(piso, min(PAUSA_MAX, segundos))


def _partir_largo(parrafo: str) -> list[str]:
    """Corta un parrafo largo por oraciones, sin dejar restos minusculos."""
    if len(parrafo) <= LARGO_COMODO:
        return [parrafo]

    partes: list[str] = []
    actual = ""
    for oracion in _FIN_DE_ORACION.split(parrafo):
        if actual and len(actual) + len(oracion) + 1 > LARGO_COMODO:
            partes.append(actual.strip())
            actual = oracion
        else:
            actual = f"{actual} {oracion}".strip()
    if actual:
        partes.append(actual.strip())
    return partes


def partir(texto: str) -> list[str]:
    """Convierte la respuesta del agente en los mensajes que se van a enviar.

    El modelo separa con saltos de linea lo que son ideas distintas —tipicamente
    la respuesta a lo que preguntaron y despues la repregunta—, asi que ese es
    el primer corte natural.
    """
    texto = (texto or "").strip()
    if not texto:
        return []

    partes: list[str] = []
    for parrafo in (p.strip() for p in texto.split("\n")):
        if parrafo:
            partes.extend(_partir_largo(parrafo))

    if len(partes) <= MAX_PARTES:
        return partes

    # Mas de tres globos cansa. Lo que sobra se pega al ultimo.
    cabeza = partes[: MAX_PARTES - 1]
    cola = " ".join(partes[MAX_PARTES - 1:])
    return cabeza + [cola]
