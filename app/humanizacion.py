"""Que la conversacion no se lea como un bot.

Dos escalas de tiempo distintas, y conviene no confundirlas:

1. **La demora de respuesta**: desde el ultimo mensaje del cliente hasta que el
   agente empieza a pensar. Desde el 11/9/2026 es 0 por pedido del cliente, y
   el tiempo de respuesta es solo lo que tarda el razonamiento. Si se vuelve a
   usar, no es una espera despues de generar: corre `procesar_despues` en el
   buffer, asi el agente ni siquiera empieza hasta que paso el tiempo, sobrevive
   a un reinicio y no bloquea al worker.

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
# de encender el indicador (~1.2s) y que despues el indicador se vea.
#
# El 14/9/2026 se bajo de 3.5–7 a 2.5–4 para acortar el tiempo de respuesta:
# con el modelo tardando 15 a 25 segundos, sumar 5 a 8 mas por globo hacia que
# la conversacion se sintiera lenta. No se bajo a 1.5, que es donde el indicador
# dejaba de verse. Con 2.5 quedan al menos 1.3 segundos de "escribiendo"
# visible: si en WhatsApp real el segundo globo vuelve a aparecer sin aviso,
# este es el numero a subir.
PAUSA_MIN = 2.5
PAUSA_MIN_MAX = 3.0
PAUSA_MAX = 4.0

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


def partir_globos(texto: str) -> list[str]:
    """Los globos de WhatsApp de un mensaje del agente.

    Desde el 15/9/2026 el agente decide donde corta: una linea que dice solo
    `---` separa un globo del siguiente. Todo lo demas —incluidos los renglones
    libres— queda dentro del mismo globo, que es como MasterShield escribe sus
    mensajes. Mas de tres globos se juntan en el ultimo.
    """
    globos: list[list[str]] = [[]]
    for linea in (texto or "").strip().splitlines():
        if linea.strip() == "---":
            globos.append([])
        else:
            globos[-1].append(linea)
    salida = [re.sub(r"\n{3,}", "\n\n", "\n".join(g)).strip() for g in globos]
    salida = [g for g in salida if g]
    if len(salida) <= MAX_PARTES:
        return salida
    return salida[:MAX_PARTES - 1] + ["\n\n".join(salida[MAX_PARTES - 1:])]


# --- emojis en los mensajes largos -------------------------------------------

# Desde que largo un mensaje del agente lleva al menos un emoji. En un telefono,
# unos 90 caracteres ya son tres lineas. Pedido de MasterShield (15/9/2026):
# alivianan la lectura, y tienen que tener que ver con lo que dice el mensaje.
LARGO_PARA_EMOJI = 90

_TIENE_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2705]")

# El primer tema que aparece en el texto decide el emoji. El orden importa: un
# mensaje sobre el precio del control solar habla de precio antes que de sol.
_EMOJI_POR_TEMA = (
    (r"precio|valor|costo|pago|presupuesto|\$", "💰"),
    (r"llam|asesor|contact", "📞"),
    (r"calor|solar|\bsol\b", "☀️"),
    (r"metro|m²|m2|medida", "📏"),
    (r"foto|imagen", "📸"),
    (r"seguridad", "🔒"),
    (r"privacidad", "👀"),
    (r"ubicaci|ciudad", "📍"),
    (r"casa|domicilio|hogar", "🏠"),
    (r"gracias|gusto", "🙌"),
)


def con_emoji(texto: str) -> str:
    """Si un mensaje largo no trae ningun emoji, le agrega uno que tenga que ver.

    Va al final de la primera oracion, en el medio del mensaje, que es donde
    corta el bloque. Un mensaje corto, o uno que ya trae emoji, sale igual.
    """
    if not texto or len(texto) < LARGO_PARA_EMOJI or _TIENE_EMOJI.search(texto):
        return texto
    emoji = next((e for patron, e in _EMOJI_POR_TEMA
                  if re.search(patron, texto, re.IGNORECASE)), "✅")
    corte = re.search(r"[.!?…:](\s+)\S", texto)
    if corte:
        i = corte.start(1)
        return f"{texto[:i]} {emoji}{texto[i:]}"
    return f"{texto} {emoji}"
