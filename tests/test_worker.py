"""Worker del buffer: armado del turno, lock y las carreras del debounce."""

import pytest

from app import ingesta, worker
from app.canales.base import MensajeEntrante


def mensaje(n: int, chat: str = "7", texto: str | None = None, tipo: str = "texto"):
    return MensajeEntrante(
        canal="telegram",
        identificador=chat,
        id_externo=f"telegram:{chat}:{n}",
        texto=texto if texto is not None else f"mensaje {n}",
        tipo=tipo,
        nombre="Maria Paez",
    )


# --- armado del turno, sin base --------------------------------------------

def registro(id_, tipo, contenido, id_externo=None):
    # `id_externo` lo necesita WhatsApp: el indicador de "escribiendo" va
    # pegado a marcar como leido un mensaje entrante concreto.
    return {"id": id_, "tipo": tipo, "contenido": contenido,
            "id_externo": id_externo or f"telegram:7:{id_}"}


def test_la_rafaga_se_junta_en_un_solo_texto():
    turno = worker.armar_turno(
        1,
        [
            registro(1, "texto", "buenas"),
            registro(2, "texto", "necesito lamina"),
            registro(3, "texto", "para una oficina en Cumbaya"),
        ],
    )
    assert turno.texto == "buenas\nnecesito lamina\npara una oficina en Cumbaya"
    assert turno.ids_mensajes == [1, 2, 3]
    assert turno.ultimo_id == 3
    assert turno.ultimo_id_externo == "telegram:7:3"


def test_los_adjuntos_se_anotan_en_el_turno():
    turno = worker.armar_turno(
        1,
        [
            registro(1, "imagen", "mide 1.20 x 2.40"),
            registro(2, "audio", ""),
        ],
    )
    assert turno.texto == "[imagen] mide 1.20 x 2.40\n[audio]"


def test_sin_mensajes_no_hay_turno():
    assert worker.armar_turno(1, []) is None


# --- con base ---------------------------------------------------------------

async def _vencer(conexion, conversacion_id=None):
    """Corre la ventana al pasado. Dentro de una transaccion now() no avanza,
    asi que el paso del tiempo se simula moviendo la fila."""
    if conversacion_id is None:
        await conexion.execute(
            "UPDATE pendientes SET procesar_despues = now() - interval '1 second'"
        )
    else:
        await conexion.execute(
            "UPDATE pendientes SET procesar_despues = now() - interval '1 second' "
            "WHERE conversacion_id = $1",
            conversacion_id,
        )


async def _id_conversacion(conexion, chat="7"):
    return await conexion.fetchval(
        "SELECT id FROM conversaciones WHERE canal='telegram' AND identificador=$1", chat
    )


@pytest.fixture
def turnos(monkeypatch):
    """Captura los turnos en vez de mandarlos al agente (que no existe todavia)."""
    capturados = []

    async def _procesar(turno):
        capturados.append(turno)

    monkeypatch.setattr(worker, "procesar_turno", _procesar)
    return capturados


@pytest.mark.db
async def test_tres_mensajes_seguidos_dan_un_solo_turno(pool_en_transaccion, turnos):
    """El caso central del debounce: la rafaga se contesta una sola vez."""
    conexion = pool_en_transaccion
    for n, t in ((1, "buenas"), (2, "necesito lamina"), (3, "para una oficina en Cumbaya")):
        await ingesta.registrar(mensaje(n, texto=t), 6)
    await _vencer(conexion)

    assert await worker.Worker().una_vuelta() == 1

    assert len(turnos) == 1
    assert turnos[0].texto == "buenas\nnecesito lamina\npara una oficina en Cumbaya"
    assert await conexion.fetchval("SELECT count(*) FROM pendientes") == 0
    assert await conexion.fetchval(
        "SELECT count(*) FROM mensajes WHERE conversacion_id = $1 AND NOT procesado",
        await _id_conversacion(conexion),
    ) == 0


@pytest.mark.db
async def test_no_toma_las_que_todavia_no_vencieron(pool_en_transaccion, turnos):
    await ingesta.registrar(mensaje(1), 600)
    assert await worker.Worker().una_vuelta() == 0
    assert turnos == []


@pytest.mark.db
async def test_una_conversacion_no_se_procesa_dos_veces(pool_en_transaccion, turnos):
    """Dos vueltas seguidas: la segunda no la vuelve a tomar."""
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 6)
    await _vencer(conexion)

    w = worker.Worker()
    assert await w.una_vuelta() == 1
    assert await w.una_vuelta() == 0
    assert len(turnos) == 1


@pytest.mark.db
async def test_el_mensaje_que_llega_durante_el_turno_no_se_pierde(
    pool_en_transaccion, monkeypatch
):
    """La carrera del debounce: mientras el worker contesta, el cliente sigue
    escribiendo. Ese mensaje no puede quedar sin respuesta."""
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1, texto="buenas"), 6)
    await _vencer(conexion)

    capturados = []

    async def _procesar_y_que_llegue_otro(turno):
        capturados.append(turno)
        # Justo mientras se procesa el turno, entra un mensaje nuevo.
        await ingesta.registrar(mensaje(2, texto="ah, y tambien la pergola"), 6)

    monkeypatch.setattr(worker, "procesar_turno", _procesar_y_que_llegue_otro)

    await worker.Worker().una_vuelta()

    id_conv = await _id_conversacion(conexion)
    # La pendiente sigue viva, con la ventana corrida y sin lock.
    fila = await conexion.fetchrow(
        "SELECT * FROM pendientes WHERE conversacion_id = $1", id_conv
    )
    assert fila is not None, "la conversacion no puede quedar sin agendar"
    assert fila["bloqueado_hasta"] is None
    assert fila["intentos"] == 0

    # El mensaje nuevo quedo sin procesar; el del primer turno, procesado.
    sin_procesar = await conexion.fetch(
        "SELECT contenido FROM mensajes WHERE conversacion_id=$1 AND NOT procesado", id_conv
    )
    assert [f["contenido"] for f in sin_procesar] == ["ah, y tambien la pergola"]

    # Y en la vuelta siguiente se contesta.
    capturados.clear()

    async def _solo_capturar(turno):
        capturados.append(turno)

    monkeypatch.setattr(worker, "procesar_turno", _solo_capturar)
    await _vencer(conexion, id_conv)
    await worker.Worker().una_vuelta()
    assert len(capturados) == 1
    assert capturados[0].texto == "ah, y tambien la pergola"


@pytest.mark.db
async def test_los_pendientes_sobreviven_al_reinicio(pool_en_transaccion, turnos):
    """Railway reinicia el proceso en cada deploy. El buffer vive en la base
    justamente para esto: un Worker nuevo levanta lo que dejo el anterior."""
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1, texto="buenas"), 6)
    await ingesta.registrar(mensaje(2, texto="necesito lamina"), 6)

    # El proceso muere aca: nada se proceso, la pendiente sigue en la base.
    assert await conexion.fetchval("SELECT count(*) FROM pendientes") == 1

    await _vencer(conexion)
    assert await worker.Worker().una_vuelta() == 1  # otro Worker, otro proceso

    assert len(turnos) == 1
    assert turnos[0].texto == "buenas\nnecesito lamina"


@pytest.mark.db
async def test_un_lock_vencido_se_retoma(pool_en_transaccion, turnos):
    """Si el proceso muere a mitad de un turno, el lock caduca solo."""
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 6)
    id_conv = await _id_conversacion(conexion)
    await _vencer(conexion)
    await conexion.execute(
        "UPDATE pendientes SET bloqueado_hasta = now() - interval '1 minute' "
        "WHERE conversacion_id = $1",
        id_conv,
    )

    assert await worker.Worker().una_vuelta() == 1
    assert len(turnos) == 1


@pytest.mark.db
async def test_un_lock_vigente_no_se_toca(pool_en_transaccion, turnos):
    """Durante un deploy conviven dos procesos mirando la misma tabla."""
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 6)
    id_conv = await _id_conversacion(conexion)
    await _vencer(conexion)
    await conexion.execute(
        "UPDATE pendientes SET bloqueado_hasta = now() + interval '2 minutes' "
        "WHERE conversacion_id = $1",
        id_conv,
    )

    assert await worker.Worker().una_vuelta() == 0
    assert turnos == []


@pytest.mark.db
async def test_un_turno_que_falla_no_pierde_la_conversacion(pool_en_transaccion, monkeypatch):
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 6)
    await _vencer(conexion)

    async def _explota(turno):
        raise RuntimeError("el agente se cayo")

    monkeypatch.setattr(worker, "procesar_turno", _explota)
    await worker.Worker().una_vuelta()

    fila = await conexion.fetchrow("SELECT * FROM pendientes")
    assert fila is not None
    assert fila["bloqueado_hasta"] is None, "tiene que quedar libre para reintentar"
    assert "el agente se cayo" in fila["ultimo_error"]
    assert await conexion.fetchval(
        "SELECT count(*) FROM mensajes WHERE conversacion_id = $1 AND NOT procesado",
        await _id_conversacion(conexion),
    ) == 1


@pytest.mark.db
async def test_despues_de_varios_intentos_se_posterga(pool_en_transaccion, monkeypatch):
    """No gastar el loop cada segundo en algo que esta roto."""
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 6)
    await _vencer(conexion)
    await conexion.execute(
        "UPDATE pendientes SET intentos = $1", worker.MAX_INTENTOS
    )

    async def _explota(turno):
        raise RuntimeError("sigue roto")

    monkeypatch.setattr(worker, "procesar_turno", _explota)
    await worker.Worker().una_vuelta()

    fila = await conexion.fetchrow("SELECT * FROM pendientes")
    assert fila["procesar_despues"] > await conexion.fetchval("SELECT now()")


@pytest.mark.db
async def test_pendiente_sin_mensajes_se_limpia(pool_en_transaccion, turnos):
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 6)
    id_conv = await _id_conversacion(conexion)
    await conexion.execute("UPDATE mensajes SET procesado = true")
    await _vencer(conexion)

    await worker.Worker().una_vuelta()

    assert turnos == []
    assert await conexion.fetchval("SELECT count(*) FROM pendientes") == 0


# --- uso anómalo ------------------------------------------------------------

@pytest.mark.db
async def test_una_rafaga_no_llega_a_gastar_una_llamada_al_modelo(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    """El control está antes de llamar al modelo, que es donde está el costo."""
    from app.agente import loop as loop_agente

    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 6)
    id_conv = await _id_conversacion(conexion)
    for n in range(12):
        await conexion.execute(
            "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo) "
            "VALUES ($1, 'cliente', 'spam', $2)", id_conv, f"flood:{n}",
        )
    await _vencer(conexion)

    async def _no_deberia_llamarse(conversacion_id):
        raise AssertionError("no tendría que haber llamado al modelo")

    enviados = []

    async def _enviar(turno, texto):
        enviados.append(texto)
        return None

    monkeypatch.setattr(loop_agente, "responder", _no_deberia_llamarse)
    monkeypatch.setattr(worker, "_enviar", _enviar)

    await worker.Worker().una_vuelta()

    assert await conexion.fetchval(
        "SELECT estado FROM conversaciones WHERE id = $1", id_conv
    ) == "derivada"
    assert len(enviados) == 1, "se avisa una sola vez"
    evento = await conexion.fetchrow(
        "SELECT detalle FROM eventos WHERE conversacion_id = $1 AND tipo = 'escalado_a_humano'",
        id_conv,
    )
    assert "uso anomalo" in evento["detalle"]["motivo"]


@pytest.mark.db
async def test_una_conversacion_normal_no_se_deriva(
    pool_en_transaccion, monkeypatch, settings_de_prueba, turnos
):
    conexion = pool_en_transaccion
    await ingesta.registrar(mensaje(1), 6)
    await _vencer(conexion)

    await worker.Worker().una_vuelta()

    assert len(turnos) == 1
    assert await conexion.fetchval(
        "SELECT estado FROM conversaciones WHERE id = $1",
        await _id_conversacion(conexion),
    ) == "activa"


# --- la bienvenida no pasa por el modelo ------------------------------------

def test_la_bienvenida_sale_del_archivo():
    """Es texto de marca: MasterShield lo edita sin tocar código."""
    mensajes = worker.bienvenida()
    assert len(mensajes) >= 2
    assert "MasterShield" in mensajes[0]
    assert "gusto" in mensajes[-1].lower(), "la última pregunta el nombre"


def test_la_bienvenida_no_trae_comentarios_ni_lineas_vacias():
    assert all(m.strip() and not m.startswith("#") for m in worker.bienvenida())


@pytest.mark.db
async def test_una_conversacion_sin_respuestas_es_primer_turno(pool_en_transaccion):
    conexion = pool_en_transaccion
    id_conv = await conexion.fetchval(
        "INSERT INTO conversaciones (canal, identificador) "
        "VALUES ('consola', 'primer-turno') RETURNING id")
    await conexion.execute(
        "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo) "
        "VALUES ($1, 'cliente', 'hola', 'pt-1')", id_conv)

    assert await worker.es_primer_turno(id_conv) is True


@pytest.mark.db
async def test_si_el_agente_ya_hablo_no_es_primer_turno(pool_en_transaccion):
    """Una conversación reabierta después de meses ya fue saludada: saludarla de
    nuevo la trataría como si fuera la primera vez."""
    conexion = pool_en_transaccion
    id_conv = await conexion.fetchval(
        "INSERT INTO conversaciones (canal, identificador) "
        "VALUES ('consola', 'primer-turno') RETURNING id")
    for rol, texto, ext in [("cliente", "hola", "pt-1"), ("agente", "Bienvenido", "pt-2")]:
        await conexion.execute(
            "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo) "
            "VALUES ($1, $2, $3, $4)", id_conv, rol, texto, ext)

    assert await worker.es_primer_turno(id_conv) is False


@pytest.mark.db
async def test_un_mensaje_del_vendedor_tambien_cuenta_como_hablado(pool_en_transaccion):
    """Si un asesor ya contestó a mano, la conversación está abierta: la
    bienvenida llegaría después de que una persona real ya saludó."""
    conexion = pool_en_transaccion
    id_conv = await conexion.fetchval(
        "INSERT INTO conversaciones (canal, identificador) "
        "VALUES ('consola', 'primer-turno') RETURNING id")
    for rol, texto, ext in [("cliente", "hola", "pt-1"), ("vendedor", "yo sigo", "pt-2")]:
        await conexion.execute(
            "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo) "
            "VALUES ($1, $2, $3, $4)", id_conv, rol, texto, ext)

    assert await worker.es_primer_turno(id_conv) is False


@pytest.mark.db
async def test_el_primer_turno_manda_la_bienvenida_y_no_llama_al_modelo(
    pool_en_transaccion, monkeypatch, settings_de_prueba
):
    """La bienvenida es texto de marca y sale igual siempre. Cuando la escribía
    el modelo, una de cada dos veces se comía la línea de bienvenida — y es lo
    primero que lee un cliente."""
    conexion = pool_en_transaccion
    id_conv = await conexion.fetchval(
        "INSERT INTO conversaciones (canal, identificador) "
        "VALUES ('consola', 'bienvenida-test') RETURNING id")
    await conexion.execute(
        "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo) "
        "VALUES ($1, 'cliente', 'hola, busco algo para el sol', 'bt-1')", id_conv)

    enviados = []

    async def _no_llamar(_):
        raise AssertionError("el primer turno no puede gastar una llamada al modelo")

    async def _enviar(turno, texto):
        enviados.append(texto)
        return None

    monkeypatch.setattr(worker.loop, "responder", _no_llamar)
    monkeypatch.setattr(worker, "_enviar", _enviar)
    monkeypatch.setattr(worker.humanizacion, "demora_de_escritura", lambda t: 0.0)
    monkeypatch.setattr(worker, "_mostrar_escribiendo", _sin_indicador)

    turno = worker.Turno(
        conversacion_id=id_conv, texto="hola, busco algo para el sol",
        ids_mensajes=[1], canal="consola", identificador="bienvenida-test",
    )
    await worker.procesar_turno(turno)

    assert enviados == worker.bienvenida(), "sale palabra por palabra, siempre igual"


async def _sin_indicador(turno, segundos, parte=1):
    return None
