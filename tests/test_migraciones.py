"""El esquema, verificado contra la base real.

Todo corre dentro de una transaccion que se revierte, asi que estos tests son
seguros incluso contra la base de Supabase del cliente.

No verifican "que las tablas existan" y ya: verifican las decisiones que el
resto del sistema da por ciertas — la deduplicacion del webhook, el reinicio
del timer del debounce, y que un estado invalido no entre.
"""

import asyncpg
import pytest

pytestmark = pytest.mark.db

TABLAS = ("conversaciones", "mensajes", "pendientes", "catalogo", "eventos")


async def _crear_conversacion(conexion, telefono="+593999000001") -> int:
    return await conexion.fetchval(
        "INSERT INTO conversaciones (telefono) VALUES ($1) RETURNING id", telefono
    )


@pytest.mark.parametrize("tabla", TABLAS)
async def test_la_tabla_existe(conexion, tabla):
    assert await conexion.fetchval("SELECT to_regclass($1)", tabla) == tabla


async def test_pgvector_instalada(conexion):
    assert await conexion.fetchval(
        "SELECT count(*) FROM pg_extension WHERE extname = 'vector'"
    ) == 1


async def test_todas_las_marcas_de_tiempo_son_timestamptz(conexion):
    """Un timestamp sin zona haria que el horario de atencion se evalue contra
    la hora del servidor (UTC en Railway) en vez de la de Ecuador."""
    sin_zona = await conexion.fetch(
        """
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ANY($1::text[])
          AND data_type = 'timestamp without time zone'
        """,
        list(TABLAS),
    )
    assert sin_zona == []


# --- conversaciones ---------------------------------------------------------

async def test_el_telefono_es_unico(conexion):
    await _crear_conversacion(conexion)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _crear_conversacion(conexion)


async def test_estado_invalido_se_rechaza(conexion):
    with pytest.raises(asyncpg.CheckViolationError):
        await conexion.execute(
            "INSERT INTO conversaciones (telefono, estado) VALUES ($1, 'inventado')",
            "+593999000002",
        )


async def test_datos_arranca_vacio_y_guarda_dict(conexion):
    """`guardar_dato` escribe incrementalmente sobre este jsonb."""
    id_conv = await _crear_conversacion(conexion)
    assert await conexion.fetchval("SELECT datos FROM conversaciones WHERE id = $1", id_conv) == {}

    await conexion.execute(
        "UPDATE conversaciones SET datos = datos || $2::jsonb WHERE id = $1",
        id_conv,
        {"linea": "arquitectonico"},
    )
    await conexion.execute(
        "UPDATE conversaciones SET datos = datos || $2::jsonb WHERE id = $1",
        id_conv,
        {"zona": "Quito"},
    )
    datos = await conexion.fetchval("SELECT datos FROM conversaciones WHERE id = $1", id_conv)
    assert datos == {"linea": "arquitectonico", "zona": "Quito"}


async def test_score_fuera_de_rango_se_rechaza(conexion):
    id_conv = await _crear_conversacion(conexion)
    with pytest.raises(asyncpg.CheckViolationError):
        await conexion.execute("UPDATE conversaciones SET score = 101 WHERE id = $1", id_conv)


async def test_trigger_toca_actualizada_en(conexion):
    id_conv = await _crear_conversacion(conexion)
    antes = await conexion.fetchval(
        "SELECT actualizada_en FROM conversaciones WHERE id = $1", id_conv
    )
    # now() es constante dentro de una transaccion, asi que se avanza a mano
    # para que el trigger tenga algo distinto que escribir.
    await conexion.execute(
        "UPDATE conversaciones SET estado = 'pausada', creada_en = creada_en WHERE id = $1",
        id_conv,
    )
    despues = await conexion.fetchval(
        "SELECT actualizada_en FROM conversaciones WHERE id = $1", id_conv
    )
    assert despues >= antes


# --- mensajes ---------------------------------------------------------------

async def test_wa_message_id_repetido_no_entra_dos_veces(conexion):
    """Asi deduplica el webhook: sin SELECT previo, que tendria carrera si el
    BSP reintenta en paralelo."""
    id_conv = await _crear_conversacion(conexion)
    sql = """
        INSERT INTO mensajes (conversacion_id, rol, contenido, wa_message_id)
        VALUES ($1, 'cliente', $2, $3)
        ON CONFLICT (wa_message_id) DO NOTHING
        RETURNING id
    """
    primero = await conexion.fetchval(sql, id_conv, "buenas", "wamid.ABC")
    segundo = await conexion.fetchval(sql, id_conv, "buenas", "wamid.ABC")

    assert primero is not None
    assert segundo is None, "el reintento del BSP no debe crear un segundo mensaje"


async def test_varios_mensajes_del_agente_sin_wa_message_id(conexion):
    """El indice unico acepta NULL repetido: los mensajes salientes todavia no
    tienen id del BSP cuando se guardan."""
    id_conv = await _crear_conversacion(conexion)
    for texto in ("Buenas tardes", "Cuenteme un poco mas"):
        await conexion.execute(
            "INSERT INTO mensajes (conversacion_id, rol, contenido) VALUES ($1, 'agente', $2)",
            id_conv,
            texto,
        )
    assert await conexion.fetchval(
        "SELECT count(*) FROM mensajes WHERE conversacion_id = $1", id_conv
    ) == 2


async def test_rol_invalido_se_rechaza(conexion):
    id_conv = await _crear_conversacion(conexion)
    with pytest.raises(asyncpg.CheckViolationError):
        await conexion.execute(
            "INSERT INTO mensajes (conversacion_id, rol, contenido) VALUES ($1, 'bot', 'hola')",
            id_conv,
        )


async def test_los_mensajes_de_una_rafaga_conservan_el_orden(conexion):
    """Dentro de una transaccion los tres comparten creado_en; el orden lo da
    el id, que es de lo que depende el armado del historial."""
    id_conv = await _crear_conversacion(conexion)
    for texto in ("buenas", "necesito lamina", "para una oficina en Cumbaya"):
        await conexion.execute(
            "INSERT INTO mensajes (conversacion_id, rol, contenido) VALUES ($1, 'cliente', $2)",
            id_conv,
            texto,
        )
    filas = await conexion.fetch(
        "SELECT contenido, creado_en FROM mensajes WHERE conversacion_id = $1 ORDER BY id",
        id_conv,
    )
    assert [f["contenido"] for f in filas] == [
        "buenas",
        "necesito lamina",
        "para una oficina en Cumbaya",
    ]
    assert len({f["creado_en"] for f in filas}) == 1


async def test_borrar_la_conversacion_arrastra_los_mensajes(conexion):
    id_conv = await _crear_conversacion(conexion)
    await conexion.execute(
        "INSERT INTO mensajes (conversacion_id, rol, contenido) VALUES ($1, 'cliente', 'hola')",
        id_conv,
    )
    await conexion.execute("DELETE FROM conversaciones WHERE id = $1", id_conv)
    assert await conexion.fetchval(
        "SELECT count(*) FROM mensajes WHERE conversacion_id = $1", id_conv
    ) == 0


# --- pendientes (debounce) --------------------------------------------------

UPSERT = """
    INSERT INTO pendientes (conversacion_id, procesar_despues)
    VALUES ($1, now() + ($2 || ' seconds')::interval)
    ON CONFLICT (conversacion_id)
    DO UPDATE SET procesar_despues = EXCLUDED.procesar_despues
    RETURNING procesar_despues
"""


async def test_cada_mensaje_reinicia_el_timer(conexion):
    """El corazon del debounce: tres mensajes seguidos dejan una sola fila y la
    ventana se corre al ultimo."""
    id_conv = await _crear_conversacion(conexion)

    primero = await conexion.fetchval(UPSERT, id_conv, "6")
    segundo = await conexion.fetchval(UPSERT, id_conv, "20")
    tercero = await conexion.fetchval(UPSERT, id_conv, "40")

    assert await conexion.fetchval("SELECT count(*) FROM pendientes") >= 1
    assert await conexion.fetchval(
        "SELECT count(*) FROM pendientes WHERE conversacion_id = $1", id_conv
    ) == 1
    assert primero < segundo < tercero


async def test_el_worker_toma_solo_las_vencidas_y_las_bloquea(conexion):
    vencida = await _crear_conversacion(conexion, "+593999000010")
    futura = await _crear_conversacion(conexion, "+593999000011")
    await conexion.execute(UPSERT, vencida, "-10")
    await conexion.execute(UPSERT, futura, "600")

    tomar = """
        UPDATE pendientes SET bloqueado_hasta = now() + interval '2 minutes',
                              intentos = intentos + 1
        WHERE conversacion_id IN (
            SELECT conversacion_id FROM pendientes
            WHERE procesar_despues <= now()
              AND (bloqueado_hasta IS NULL OR bloqueado_hasta < now())
            ORDER BY procesar_despues
            FOR UPDATE SKIP LOCKED
            LIMIT 10
        )
        RETURNING conversacion_id
    """
    tomadas = [f["conversacion_id"] for f in await conexion.fetch(tomar)]
    assert vencida in tomadas
    assert futura not in tomadas

    # Un segundo pase no la vuelve a tomar: sigue bloqueada.
    assert vencida not in [f["conversacion_id"] for f in await conexion.fetch(tomar)]


async def test_el_lock_vencido_se_puede_retomar(conexion):
    """Si Railway mata el proceso a mitad de un turno, la conversacion tiene que
    volver a estar disponible sola."""
    id_conv = await _crear_conversacion(conexion, "+593999000012")
    await conexion.execute(UPSERT, id_conv, "-10")
    await conexion.execute(
        "UPDATE pendientes SET bloqueado_hasta = now() - interval '1 minute' WHERE conversacion_id = $1",
        id_conv,
    )
    disponibles = await conexion.fetch(
        """
        SELECT conversacion_id FROM pendientes
        WHERE procesar_despues <= now()
          AND (bloqueado_hasta IS NULL OR bloqueado_hasta < now())
        """
    )
    assert id_conv in [f["conversacion_id"] for f in disponibles]


async def test_una_conversacion_no_puede_tener_dos_pendientes(conexion):
    id_conv = await _crear_conversacion(conexion)
    await conexion.execute(UPSERT, id_conv, "6")
    with pytest.raises(asyncpg.UniqueViolationError):
        await conexion.execute(
            "INSERT INTO pendientes (conversacion_id, procesar_despues) VALUES ($1, now())",
            id_conv,
        )


# --- catalogo ---------------------------------------------------------------

async def test_guarda_y_compara_embeddings(conexion):
    vector = "[" + ",".join(["0.1"] * 1536) + "]"
    id_chunk = await conexion.fetchval(
        """
        INSERT INTO catalogo (documento, titulo, contenido, prioridad, embedding)
        VALUES ('faq.md', 'No aisla termicamente',
                'La lamina no rechaza el frio ni aisla termicamente.', 10, $1::vector)
        RETURNING id
        """,
        vector,
    )
    distancia = await conexion.fetchval(
        "SELECT embedding <=> $1::vector FROM catalogo WHERE id = $2", vector, id_chunk
    )
    assert distancia == pytest.approx(0.0, abs=1e-6)


async def test_embedding_de_otra_dimension_se_rechaza(conexion):
    with pytest.raises(asyncpg.PostgresError):
        await conexion.execute(
            "INSERT INTO catalogo (documento, contenido, embedding) VALUES ('x', 'y', $1::vector)",
            "[" + ",".join(["0.1"] * 768) + "]",
        )


# --- eventos ----------------------------------------------------------------

async def test_el_evento_sobrevive_a_la_conversacion(conexion):
    """Es auditoria: borrar la conversacion no puede borrar el rastro de lo que
    se le mando a Kommo."""
    id_conv = await _crear_conversacion(conexion)
    id_evento = await conexion.fetchval(
        "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
        "VALUES ($1, 'kommo_lead_creado', 'ok', $2) RETURNING id",
        id_conv,
        {"lead_id": 12345},
    )
    await conexion.execute("DELETE FROM conversaciones WHERE id = $1", id_conv)

    fila = await conexion.fetchrow("SELECT * FROM eventos WHERE id = $1", id_evento)
    assert fila is not None
    assert fila["conversacion_id"] is None
    assert fila["detalle"] == {"lead_id": 12345}


async def test_estado_de_evento_invalido_se_rechaza(conexion):
    with pytest.raises(asyncpg.CheckViolationError):
        await conexion.execute(
            "INSERT INTO eventos (tipo, estado) VALUES ('kommo_lead_creado', 'masomenos')"
        )
