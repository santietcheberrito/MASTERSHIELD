"""Loop que procesa el buffer de mensajes.

Corre en el mismo proceso que FastAPI, como tarea de arranque. Cada vuelta
toma las conversaciones cuya ventana de debounce ya vencio, arma un unico turno
con todos los mensajes acumulados y lo procesa.

Por ahora lo unico que hace con el turno es loguearlo. El agente se conecta en
la sesion 4.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path
import logging
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from app import audio, db, fichas, humanizacion, limites, registro
from app.agente import herramientas, loop
from app.canales import telegram, whatsapp
from app.config import obtener_settings
from app.fichas import Audio, Video

logger = logging.getLogger(__name__)

# Cuanto se reserva una conversacion mientras se procesa. Tiene que ser mayor
# que el turno mas lento imaginable (el agente puede encadenar varias
# herramientas) y menor que la paciencia de una persona esperando respuesta.
LOCK_SEGUNDOS = 120

# Cuanto se espera despues de enviar un mensaje antes de volver a encender el
# indicador de "escribiendo". Medido contra la API real: el envio apaga el
# indicador, y las dos ordenes salen tan juntas que el apagado gana.
RESPIRO_ANTES_DEL_INDICADOR = 1.2

# Lo que espera el primer globo de la respuesta. Poco, porque el indicador de
# "escribiendo" se enciende al empezar el turno y la persona ya lo vio.
PAUSA_PRIMER_MENSAJE = 1.0

# Lo que se espera antes de mandar el video. No depende de un largo como el
# texto: es el rato de buscar el archivo y adjuntarlo.
PAUSA_ANTES_DEL_VIDEO = 2.5

# Un turno que falla se reintenta, pero no para siempre: despues de esto se
# posterga una hora para no gastar el loop en algo que esta roto.
MAX_INTENTOS = 3
POSTERGACION_SEGUNDOS = 3600

# Lo que se le dice a alguien cuya conversacion se derivo por uso anomalo. Es
# neutro a proposito: puede ser un cliente entusiasta y no un atacante.
MENSAJE_LIMITE = (
    "Prefiero que siga con usted un asesor MS. En un momento se comunican."
)

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
    SELECT id, tipo, contenido, id_externo, payload
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

# Un mensaje del cliente posterior a los que entraron en este turno. Es lo que
# convierte a la respuesta en vieja: fue escrita sin saber lo que la persona
# acababa de decir.
_LLEGO_ALGO_NUEVO = """
    SELECT 1 FROM mensajes
    WHERE conversacion_id = $1 AND rol = 'cliente' AND id > $2
    LIMIT 1
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
    # El ultimo mensaje del cliente, que WhatsApp necesita para el indicador.
    ultimo_id_externo: str = ""

    @property
    def ultimo_id(self) -> int:
        return self.ids_mensajes[-1]


_DATOS_CONVERSACION = "SELECT canal, identificador FROM conversaciones WHERE id = $1"

# `procesado` en true desde el vamos: la marca significa "ya entro en un turno"
# y solo tiene sentido para los mensajes del cliente. Si los del agente quedan
# en false, el indice parcial `mensajes_sin_procesar_ix` acumula cada respuesta
# que el bot dio en su vida y la consulta del worker se va poniendo mas cara.
_GUARDAR_RESPUESTA = """
    INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo, procesado, tipo)
    VALUES ($1, 'agente', $2, $3, true, $4)
"""


async def transcribir_audios(
    conversacion_id: int, filas: list[asyncpg.Record]
) -> list[dict]:
    """Las notas de voz del turno, pasadas a texto antes de armarlo.

    La transcripcion se guarda en el mensaje: el historial de los turnos
    siguientes la lleva sola, el asesor la lee en la nota del CRM y no se
    vuelve a bajar el audio. Si no se pudo, el mensaje queda como estaba y el
    agente ve el aviso de que llego un audio.
    """
    mensajes = [dict(f) for f in filas]
    for mensaje in mensajes:
        if mensaje["tipo"] != "audio" or (mensaje["contenido"] or "").strip():
            continue
        texto = await audio.texto_de_la_nota(mensaje.get("payload") or {})
        if not texto:
            logger.warning("nota de voz sin transcribir | conversacion=%s mensaje=%s",
                           conversacion_id, mensaje["id"])
            continue
        mensaje["contenido"] = texto
        await db.ejecutar(
            "UPDATE mensajes SET contenido = $2 WHERE id = $1", mensaje["id"], texto
        )
    return mensajes


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
        elif fila["tipo"] == "audio" and fila["contenido"]:
            # Ya transcrita: lo que dijo es el mensaje, no un adjunto.
            partes.append(f"[nota de voz] {fila['contenido']}")
        elif fila["contenido"]:
            # Una foto con epigrafe: el texto importa y el adjunto tambien.
            partes.append(f"[{fila['tipo']}] {fila['contenido']}")
        else:
            partes.append(f"[{fila['tipo']}]")

    return Turno(
        conversacion_id=conversacion_id,
        ids_mensajes=[f["id"] for f in filas],
        texto="\n".join(partes),
        ultimo_id_externo=filas[-1]["id_externo"] or "",
    )


BIENVENIDA = Path(__file__).resolve().parent.parent / "prompts" / "bienvenida.txt"


@lru_cache
def bienvenida() -> list[str]:
    """Los mensajes de apertura, uno por linea del archivo.

    Se lee una sola vez por proceso: cambiarla es editar el archivo y
    reiniciar, igual que el prompt.
    """
    lineas = [
        linea.strip()
        for linea in BIENVENIDA.read_text(encoding="utf-8").splitlines()
        if linea.strip() and not linea.lstrip().startswith("#")
    ]
    if not lineas:
        raise RuntimeError(f"{BIENVENIDA} no tiene ningun mensaje")
    return lineas


async def es_primer_turno(conversacion_id: int) -> bool:
    """Si el agente todavia no dijo nada en esta conversacion.

    Se pregunta por los mensajes del agente y no por los del cliente: una
    conversacion reabierta despues de meses ya fue saludada, y saludarla de
    nuevo la trataria como si fuera la primera vez.
    """
    return not await db.valor(
        "SELECT 1 FROM mensajes WHERE conversacion_id = $1 AND rol <> 'cliente' LIMIT 1",
        conversacion_id,
    )


async def procesar_turno(turno: Turno) -> None:
    """Corre el agente y manda la respuesta como la mandaria una persona.

    El retraso de 60 a 120 segundos que pidio el cliente NO esta aca: ya paso
    antes de que este turno se tomara, porque es la ventana del buffer. Ver
    app/humanizacion.py. Lo que si pasa aca es el partido en varios mensajes y
    las pausas entre ellos.
    """
    logger.info(
        "turno armado | conversacion=%s mensajes=%s | %s",
        turno.conversacion_id,
        len(turno.ids_mensajes),
        turno.texto.replace("\n", " / "),
    )

    # Antes de gastar una llamada al modelo: ¿esta conversacion se ve normal?
    # El control va aca y no en el webhook porque aca es donde esta el costo, y
    # porque el webhook tiene un presupuesto de 500ms que no conviene gastar en
    # una consulta mas.
    if await _uso_anomalo(turno):
        return

    # "Escribiendo..." desde que el turno arranca, mientras el agente piensa. La
    # persona ve que alguien le esta contestando en vez de un chat quieto, y el
    # primer globo puede salir sin otra pausa encima (15/9/2026: el cliente pidio
    # respuestas mas rapidas).
    await _mostrar_escribiendo(turno, 0.01)

    # La bienvenida es texto de marca y sale igual siempre, asi que no pasa por
    # el modelo. Con dos ejemplos parecidos en el prompt, una de cada dos veces
    # se comia la linea de bienvenida, y es lo primero que lee un cliente.
    #
    # El primer turno se resuelve entero aca: saludar, presentar la empresa y
    # preguntar el nombre. La consulta que la persona haya traido se contesta en
    # el turno siguiente, que es el orden que pidio el cliente —en Ecuador se
    # saluda antes de entrar en tema—. De paso, ahorra una llamada al modelo.
    if await es_primer_turno(turno.conversacion_id):
        await _enviar_partes(turno, bienvenida())
        return

    respuesta = await loop.responder(turno.conversacion_id, turno.ultimo_id)

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

    if respuesta.descartada:
        # Llego otro mensaje mientras pensaba: el turno se rehace con todo.
        logger.info("turno cortado antes de terminar: llego otro mensaje | "
                    "conversacion=%s", turno.conversacion_id)
        return

    if respuesta.silencio_deliberado:
        # No es lo mismo que un turno vacio: el agente miro lo que quedo sin
        # contestar y decidio que no pedia respuesta. Queda en `eventos`.
        logger.info("el agente decidio no contestar | conversacion=%s",
                    turno.conversacion_id)
        return

    if not (respuesta.texto or respuesta.fichas or respuesta.lista_de_precios):
        # Puede pasar si el modelo solo llamo herramientas y se agotaron las
        # iteraciones. No se manda nada, pero queda el log para investigarlo.
        logger.warning("el agente no produjo texto | conversacion=%s", turno.conversacion_id)
        return

    # Primero la descripcion oficial —que es la respuesta a lo que preguntaron—
    # y despues lo del modelo, que es el paso siguiente. La ficha no pasa por
    # `partir`: cortada en pedazos deja de ser el bloque que escribio la empresa.
    partes: list[str | Video] = []
    # Antes de la ficha, la linea del agente que asiente y presenta lo que viene:
    # un bloque largo que aparece sin aviso se lee como un folleto.
    if respuesta.fichas and respuesta.introduccion_fichas:
        partes.append(humanizacion.con_emoji(respuesta.introduccion_fichas))
    partes += await fichas.armar_envio(turno.conversacion_id, respuesta.fichas)
    # La lista de precios va despues de la ficha y antes del modelo, por lo
    # mismo: es la respuesta, y lo que escribe el modelo es el paso siguiente.
    if respuesta.lista_de_precios:
        if respuesta.introduccion_precios:
            partes.append(humanizacion.con_emoji(respuesta.introduccion_precios))
        partes.append(respuesta.lista_de_precios)
        # Pegada a los precios, la nota de voz del asesor: dice lo mismo que la
        # oferta escrita pero con una persona hablando, y es distinta segun la
        # zona —visita tecnica en Quito, llamada afuera— (30/9/2026). Una vez
        # por conversacion: quien vuelve a preguntar el precio no la repite.
        audio = await _audio_de_los_precios(turno.conversacion_id)
        if audio:
            partes.append(audio)
    # Lo que escribe el modelo: una linea con --- separa un globo del siguiente.
    # Los globos largos llevan un emoji del tema. Las fichas y la lista no se
    # tocan: ya los escribio MasterShield.
    if respuesta.texto:
        partes += [humanizacion.con_emoji(g) for g in humanizacion.partir_globos(respuesta.texto)]
    # Al cerrar, el video de MasterShield: la persona ya dejo sus datos y lo
    # ultimo que ve es como trabajan y como quedan las terminaciones (pedido del
    # cliente, 15/9/2026). Si ya salio con una ficha, no se repite.
    if await _cierra_la_conversacion(turno.conversacion_id, respuesta):
        partes.append(fichas.VIDEO_PRODUCTOS)
    await _enviar_partes(turno, partes)


async def _cierra_la_conversacion(conversacion_id: int, respuesta: loop.Respuesta) -> bool:
    """Si este es el turno en que la conversacion se despide y falta el video."""
    if not fichas.VIDEO_PRODUCTOS.ruta.exists():
        return False
    cierra = "finalizar_calificacion" in respuesta.herramientas_usadas or bool(await db.valor(
        "SELECT 1 FROM eventos WHERE conversacion_id = $1 "
        "AND tipo = 'calificacion_finalizada' LIMIT 1", conversacion_id))
    return cierra and not await fichas.video_ya_enviado(conversacion_id)


async def _quedo_vieja(turno: Turno) -> bool:
    """Si el cliente escribio algo despues de que se armo este turno.

    Mandar el resto de una respuesta escrita sin saber lo que la persona acaba
    de decir es peor que no mandar nada: contesta a una conversacion que ya
    cambio, y en la practica termina preguntando dos veces lo mismo.

    Ante un error de base se sigue enviando: cortar por una consulta que fallo
    dejaria a la persona sin respuesta por un problema nuestro.
    """
    try:
        return bool(await db.valor(_LLEGO_ALGO_NUEVO, turno.conversacion_id, turno.ultimo_id))
    except Exception:
        logger.exception("no se pudo comprobar si el turno quedo viejo")
        return False


async def _enviar_partes(turno: Turno, partes: list[str | Video]) -> None:
    """Manda los mensajes con sus pausas, como los mandaria una persona.

    Se corta apenas la persona escribe algo nuevo: los mensajes que faltan
    fueron escritos sin eso y el worker va a rehacer el turno completo. Antes
    salian igual, y por eso el agente llegaba a preguntar dos veces lo mismo.
    """
    logger.info(
        "enviando %s mensaje(s) | conversacion=%s", len(partes), turno.conversacion_id
    )

    for numero, parte in enumerate(partes, 1):
        if await _quedo_vieja(turno):
            logger.info(
                "llego un mensaje nuevo: se descartan %s de %s mensaje(s) | "
                "conversacion=%s", len(partes) - numero + 1, len(partes),
                turno.conversacion_id,
            )
            return

        # "Escribiendo..." y despues la pausa: el indicador solo tiene sentido
        # mientras se supone que se esta tipeando, no durante toda la espera.
        es_video = isinstance(parte, Video)
        es_audio = isinstance(parte, Audio)
        espera = (PAUSA_ANTES_DEL_VIDEO if es_video or es_audio
                  else humanizacion.demora_de_escritura(parte))
        # El primer globo no espera como si recien empezara a tipear: el
        # indicador ya estuvo encendido mientras el agente pensaba.
        if numero == 1 and not es_video and not es_audio:
            espera = min(espera, PAUSA_PRIMER_MENSAJE)

        # Enviar un mensaje apaga el indicador. Si el del mensaje siguiente se
        # dispara en el mismo instante en que sale el anterior, Meta recibe
        # "apagar" y "encender" casi juntos y el apagado se come al encendido:
        # el segundo globo aparece sin aviso. Este respiro deja que el apagado
        # se procese primero. Sale de la espera, no se suma, para que el ritmo
        # de la conversacion no cambie.
        if numero > 1:
            respiro = min(RESPIRO_ANTES_DEL_INDICADOR, espera / 2)
            await asyncio.sleep(respiro)
            espera -= respiro

        await _mostrar_escribiendo(turno, espera, numero)

        # Otra vez, ahora pegado al envio: la pausa dura varios segundos y es
        # justo cuando la persona esta escribiendo. El chequeo de arriba evita
        # gastar la pausa; este es el que de verdad frena el mensaje.
        if await _quedo_vieja(turno):
            logger.info(
                "llego un mensaje durante la pausa: se descarta el mensaje %s "
                "de %s | conversacion=%s", numero, len(partes), turno.conversacion_id,
            )
            return

        if es_audio:
            id_externo = await _enviar_audio(turno, parte)
            if id_externo is None:
                continue
            contenido, tipo = parte.contenido, "audio"
        elif es_video:
            id_externo = await _enviar_video(turno, parte)
            if id_externo is None:
                # No salio. No se guarda, asi el proximo producto lo intenta de
                # nuevo, y el resto de la respuesta sigue: sin video se entiende
                # igual.
                continue
            contenido, tipo = parte.contenido, "video"
        else:
            id_externo = await _enviar(turno, parte)
            contenido, tipo = parte, "texto"

        await db.ejecutar(_GUARDAR_RESPUESTA, turno.conversacion_id, contenido, id_externo, tipo)
        logger.info(
            "mensaje %s/%s enviado tras %.1fs | conversacion=%s | %s",
            numero, len(partes), espera, turno.conversacion_id, contenido,
        )


async def _uso_anomalo(turno: Turno) -> bool:
    """Corta y avisa si la conversacion se ve rara. Devuelve si hubo que cortar.

    No rechaza al cliente: deja de gastar y deriva a una persona para que mire
    quien es. Si resulta legitimo, un asesor retoma.
    """
    settings = obtener_settings()
    motivo = await limites.revisar(
        turno.conversacion_id,
        por_hora=settings.mensajes_por_hora_max,
        por_minuto=settings.mensajes_por_minuto_max,
        largo_maximo=settings.largo_maximo_mensaje,
        total_maximo=settings.mensajes_totales_max,
    )
    if motivo is None:
        return False

    logger.error(
        "uso anomalo, se deriva sin llamar al modelo | conversacion=%s: %s",
        turno.conversacion_id, motivo,
    )
    # Deja la conversacion en 'derivada' y la sube al CRM con esa etapa, que es
    # por donde el equipo se entera.
    await herramientas.escalar_a_humano(
        turno.conversacion_id, f"uso anomalo: {motivo}"
    )
    try:
        await _enviar(turno, MENSAJE_LIMITE)
    except Exception:
        logger.exception("no se pudo avisar al cliente | conversacion=%s",
                         turno.conversacion_id)
    return True


async def _mostrar_escribiendo(turno: Turno, segundos: float, parte: int = 1) -> None:
    """Mantiene el indicador mientras dura la pausa.

    Telegram lo apaga solo a los ~5 segundos, asi que hay que renovarlo. En
    Meta dura 25 segundos **o hasta que uno responde**, y ahi esta el problema
    del segundo mensaje: despues de enviar el primero el indicador se apaga, y
    reactivarlo significa volver a marcar como leido un mensaje que ya esta
    leido. Si Meta lo rechaza, el cliente ve el segundo globo aparecer de la
    nada.

    Si falla no se corta el envio —es cosmetico—, pero a partir del segundo
    mensaje se loguea en serio: es un pedido explicito del cliente y un debug
    mudo no deja verlo.
    """
    settings = obtener_settings()
    restante = segundos
    while restante > 0:
        try:
            if turno.canal == "telegram" and settings.telegram_bot_token:
                await telegram.indicar_escribiendo(
                    settings.telegram_bot_token, turno.identificador
                )
            elif (turno.canal == "whatsapp" and settings.whatsapp_token
                  and turno.ultimo_id_externo):
                # En Meta el indicador va pegado a marcar como leido, y necesita
                # el id del mensaje entrante al que se responde.
                await whatsapp.indicar_escribiendo(
                    settings.whatsapp_token, settings.whatsapp_phone_number_id,
                    turno.ultimo_id_externo,
                )
        except Exception:
            if parte > 1:
                logger.warning(
                    "no se pudo mostrar el indicador antes del mensaje %s: "
                    "el cliente lo va a ver aparecer sin aviso", parte,
                    exc_info=True,
                )
            else:
                logger.debug("no se pudo mostrar el indicador de escribiendo")
        tramo = min(4.0, restante)
        await asyncio.sleep(tramo)
        restante -= tramo


async def _enviar(turno: Turno, texto: str) -> str | None:
    """Manda un mensaje por el canal de la conversacion."""
    settings = obtener_settings()
    if turno.canal == "telegram":
        if not settings.telegram_bot_token:
            logger.error("sin TELEGRAM_BOT_TOKEN: no se puede responder")
            return None
        return await telegram.enviar(
            settings.telegram_bot_token, turno.identificador, texto
        )
    if turno.canal == "whatsapp":
        if not settings.whatsapp_token or not settings.whatsapp_phone_number_id:
            logger.error("sin credenciales de WhatsApp: no se puede responder")
            return None
        return await whatsapp.enviar(
            settings.whatsapp_token, settings.whatsapp_phone_number_id,
            turno.identificador, texto,
        )
    logger.error("canal sin implementar: %s", turno.canal)
    return None


async def _audio_de_los_precios(conversacion_id: int) -> Audio | None:
    """La nota de voz que acompaña a la lista, si corresponde y no salio antes."""
    zona = await db.valor(
        "SELECT datos->>'zona' FROM conversaciones WHERE id = $1", conversacion_id)
    audio = fichas.audio_de_la_zona(zona)
    if not audio or await fichas.audio_ya_enviado(conversacion_id):
        return None
    return audio


async def _enviar_audio(turno: Turno, audio: Audio) -> str | None:
    """Manda la nota de voz. Devuelve None si no salio, sin romper nada.

    Un audio que falla no puede dejar a la persona sin la oferta que viene
    detras, igual que con el video.
    """
    settings = obtener_settings()
    if turno.canal != "whatsapp":
        logger.info("el canal %s no manda audio: se sigue sin el", turno.canal)
        return None
    if not settings.whatsapp_token or not settings.whatsapp_phone_number_id:
        logger.error("sin credenciales de WhatsApp: no se puede mandar el audio")
        return None
    if not audio.ruta.exists():
        logger.error("no esta el archivo del audio: %s", audio.ruta)
        return None
    try:
        return await whatsapp.enviar_audio(
            settings.whatsapp_token, settings.whatsapp_phone_number_id,
            turno.identificador, audio.ruta,
        )
    except Exception:
        logger.exception("no se pudo mandar la nota de voz | conversacion=%s",
                         turno.conversacion_id)
        return None


async def _enviar_video(turno: Turno, video: Video) -> str | None:
    """Manda el video por el canal. Devuelve None si no salio, sin romper nada.

    Un video que falla —el token no puede subir archivos, Meta lo rechaza— no
    puede dejar a la persona sin la respuesta que viene detras.
    """
    settings = obtener_settings()
    if turno.canal != "whatsapp":
        logger.info("el canal %s no manda video: se sigue sin el", turno.canal)
        return None
    if not settings.whatsapp_token or not settings.whatsapp_phone_number_id:
        logger.error("sin credenciales de WhatsApp: no se puede mandar el video")
        return None
    if not video.ruta.exists():
        logger.error("no esta el archivo del video: %s", video.ruta)
        return None
    try:
        return await whatsapp.enviar_video(
            settings.whatsapp_token, settings.whatsapp_phone_number_id,
            turno.identificador, video.ruta, leyenda=video.leyenda,
        )
    except Exception:
        logger.exception("no se pudo mandar el video | conversacion=%s",
                         turno.conversacion_id)
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

        # En paralelo: mandar una respuesta partida con pausas lleva decenas de
        # segundos, y una conversacion no puede hacer esperar a las demas.
        await asyncio.gather(*(
            self._procesar_una(
                fila["conversacion_id"], fila["procesar_despues"], fila["intentos"]
            )
            for fila in tomadas
        ))
        return len(tomadas)

    async def _procesar_una(
        self, conversacion_id: int, procesar_despues: datetime, intentos: int
    ) -> None:
        # Desde aca hasta que termine el turno, todo lo que se loguee —el loop,
        # las herramientas, la sincronizacion con el CRM, httpx— lleva el id
        # solo. Es lo que permite reconstruir un turno entero despues.
        with registro.con_conversacion(conversacion_id, intento=intentos):
            await self._turno(conversacion_id, procesar_despues, intentos)

    async def _turno(
        self, conversacion_id: int, procesar_despues: datetime, intentos: int
    ) -> None:
        try:
            filas = await db.consultar(_MENSAJES_DEL_TURNO, conversacion_id)
            turno = armar_turno(
                conversacion_id, await transcribir_audios(conversacion_id, filas)
            )

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
