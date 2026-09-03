"""El loop que retoma las sincronizaciones que fallaron.

La promesa del CLAUDE.md es que si el CRM falla, la conversación no se pierde.
La mitad que faltaba era esta: `sincronizar` dejaba la marca y nadie la leía.
"""

import pytest

from app.crm import reintentos, sincronizacion

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("settings_de_prueba")]


async def _conversacion(conexion, *, pendiente=True, intentos=1, vencida=True):
    """Una conversación calificada que quedó marcada para reintento."""
    id_conv = await conexion.fetchval(
        "INSERT INTO conversaciones (canal, identificador, nombre, telefono, datos, estado) "
        "VALUES ('consola', 'reintento-test', 'Nora Vaca', '+593999123456', $1::jsonb, "
        "'calificada') RETURNING id",
        {
            "linea": "arquitectonico", "objetivo": "control_solar",
            "zona": "quito_y_valles", "metros_cuadrados": 25,
            "telefono": "0999123456", "disponibilidad": "el jueves",
        },
    )
    await conexion.execute(
        "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo) "
        "VALUES ($1, 'cliente', 'hola', 'reintento-m1')", id_conv,
    )
    cuando = "now() - interval '1 minute'" if vencida else "now() + interval '1 hour'"
    await conexion.execute(
        f"UPDATE conversaciones SET crm_pendiente = $2, crm_intentos = $3, "
        f"crm_reintentar_en = {cuando} WHERE id = $1",
        id_conv, pendiente, intentos,
    )
    return id_conv


# --- el backoff -------------------------------------------------------------

def test_el_backoff_se_abre_con_cada_intento():
    """Las caídas cortas se recuperan en un minuto; para una rota de verdad no
    tiene sentido golpear cada minuto durante horas."""
    esperas = [sincronizacion.espera_del_intento(n) for n in range(1, 6)]
    assert esperas == [60, 300, 900, 3600, 21600]
    assert esperas == sorted(esperas)


def test_pasado_el_ultimo_intento_la_espera_no_crece_mas():
    assert sincronizacion.espera_del_intento(9) == sincronizacion.espera_del_intento(5)


async def test_al_agotar_los_intentos_deja_de_reintentar_pero_sigue_pendiente(
    pool_en_transaccion,
):
    """Agotado no es resuelto. Si se limpiara la marca, un lead que nunca llegó
    al CRM quedaría indistinguible de uno que sí."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion, intentos=sincronizacion.MAX_REINTENTOS - 1)

    await sincronizacion._marcar_pendiente(id_conv)

    fila = await conexion.fetchrow(
        "SELECT crm_pendiente, crm_reintentar_en, crm_intentos FROM conversaciones "
        "WHERE id = $1", id_conv,
    )
    assert fila["crm_pendiente"] is True
    assert fila["crm_reintentar_en"] is None, "no se vuelve a tomar"
    assert await conexion.fetchval(
        "SELECT count(*) FROM eventos WHERE conversacion_id = $1 AND tipo = 'crm_agotado'",
        id_conv,
    ) == 1


# --- qué toma el loop -------------------------------------------------------

async def test_toma_las_vencidas(pool_en_transaccion):
    id_conv = await _conversacion(pool_en_transaccion)

    tomadas = await pool_en_transaccion.fetch(
        reintentos._TOMAR, reintentos.RESERVA_SEGUNDOS, 10
    )

    assert id_conv in [f["id"] for f in tomadas]


async def test_no_toma_las_que_todavia_no_vencieron(pool_en_transaccion):
    id_conv = await _conversacion(pool_en_transaccion, vencida=False)

    tomadas = await pool_en_transaccion.fetch(
        reintentos._TOMAR, reintentos.RESERVA_SEGUNDOS, 10
    )

    assert id_conv not in [f["id"] for f in tomadas]


async def test_no_toma_las_agotadas(pool_en_transaccion):
    """`crm_reintentar_en` en NULL es la marca de agotada: pendiente, visible en
    una métrica, y fuera del loop."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    await conexion.execute(
        "UPDATE conversaciones SET crm_reintentar_en = NULL WHERE id = $1", id_conv
    )

    tomadas = await conexion.fetch(reintentos._TOMAR, reintentos.RESERVA_SEGUNDOS, 10)

    assert id_conv not in [f["id"] for f in tomadas]


async def test_no_toma_las_ya_sincronizadas(pool_en_transaccion):
    id_conv = await _conversacion(pool_en_transaccion, pendiente=False)

    tomadas = await pool_en_transaccion.fetch(
        reintentos._TOMAR, reintentos.RESERVA_SEGUNDOS, 10
    )

    assert id_conv not in [f["id"] for f in tomadas]


async def test_tomarla_la_reserva_para_que_otro_proceso_no_la_repita(pool_en_transaccion):
    """Durante un deploy conviven el contenedor viejo y el nuevo unos segundos,
    y los dos miran esta tabla."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)

    primera = await conexion.fetch(reintentos._TOMAR, reintentos.RESERVA_SEGUNDOS, 10)
    segunda = await conexion.fetch(reintentos._TOMAR, reintentos.RESERVA_SEGUNDOS, 10)

    assert id_conv in [f["id"] for f in primera]
    assert id_conv not in [f["id"] for f in segunda]


# --- la vuelta completa -----------------------------------------------------

async def test_una_vuelta_recupera_la_conversacion(pool_en_transaccion, monkeypatch):
    """Lo que esto arregla, de punta a punta: Kommo se cayó, el lead quedó
    marcado, vuelve a estar en pie y el lead llega."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion)
    sincronizadas = []

    async def _ok(cid):
        sincronizadas.append(cid)
        await conexion.execute(
            "UPDATE conversaciones SET crm_pendiente = false, crm_reintentar_en = NULL, "
            "crm_sincronizada_en = now() WHERE id = $1", cid,
        )
        return True

    monkeypatch.setattr(reintentos, "sincronizar", _ok)

    tomadas = await reintentos.Reintentos().una_vuelta()

    assert tomadas >= 1
    assert id_conv in sincronizadas
    assert await conexion.fetchval(
        "SELECT crm_pendiente FROM conversaciones WHERE id = $1", id_conv
    ) is False


async def test_un_reintento_que_vuelve_a_fallar_no_rompe_la_vuelta(
    pool_en_transaccion, monkeypatch
):
    """El CRM sigue caído. La vuelta tiene que terminar igual: si una excepción
    matara el loop, volveríamos al problema que esto arregla."""
    conexion = pool_en_transaccion
    await _conversacion(conexion)

    async def _explota(cid):
        raise RuntimeError("kommo sigue caido")

    monkeypatch.setattr(reintentos, "sincronizar", _explota)

    with pytest.raises(RuntimeError):
        await reintentos.Reintentos().una_vuelta()

    # El loop la absorbe: es lo que garantiza que la vuelta siguiente ocurra.
    loop = reintentos.Reintentos(intervalo=0.01)
    loop._corriendo = True

    async def _una_vez():
        loop._corriendo = False
        raise RuntimeError("kommo sigue caido")

    monkeypatch.setattr(loop, "una_vuelta", _una_vez)
    await loop._loop()  # no propaga
