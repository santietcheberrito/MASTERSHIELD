"""El estado del sistema ahora.

Lo que importa acá no son los números sino las alertas: un tablero de números
crudos obliga a saber cuál está mal, y eso lo sabe quien escribió el sistema, no
quien lo mira un martes a la mañana.
"""

from datetime import date

import pytest

from app import metricas

pytestmark = pytest.mark.usefixtures("settings_de_prueba")


# --- las alertas ------------------------------------------------------------

SANO = {
    "crm": {"pendientes": 0, "agotadas": 0, "vencidas_sin_reintentar": 0, "mas_intentos": 0},
    "conversaciones": {"activa": 3, "calificada": 12},
    "turnos": {"en_cola": 1, "vencidos": 0, "trabados": 0, "mas_intentos": 1},
}


def test_un_sistema_sano_no_avisa_nada():
    assert metricas.alertas(SANO) == []


def test_un_lead_que_agoto_los_reintentos_avisa():
    """Es la razón de ser de todo esto. La fila queda marcada a propósito,
    esperando que algo la muestre; sin esto ese algo no existe."""
    datos = {**SANO, "crm": {**SANO["crm"], "pendientes": 1, "agotadas": 1}}

    avisos = metricas.alertas(datos)

    assert len(avisos) == 1
    assert "no llego a Kommo" in avisos[0]


def test_una_sincronizacion_vencida_sin_tomar_delata_que_el_loop_no_corre():
    """Si están vencidas y nadie las toma, el loop de reintentos está caído. Es
    distinto de que el CRM esté caído, y el aviso lo tiene que decir."""
    datos = {**SANO, "crm": {**SANO["crm"], "pendientes": 2, "vencidas_sin_reintentar": 2}}

    avisos = metricas.alertas(datos)

    assert any("no este corriendo" in a for a in avisos)


def test_los_turnos_trabados_avisan_que_hay_gente_esperando():
    datos = {**SANO, "turnos": {**SANO["turnos"], "trabados": 2, "mas_intentos": 5}}

    avisos = metricas.alertas(datos)

    assert any("esperando respuesta" in a for a in avisos)


def test_las_derivadas_avisan():
    datos = {**SANO, "conversaciones": {"activa": 1, "derivada": 3}}

    avisos = metricas.alertas(datos)

    assert any("esperando a una persona" in a for a in avisos)


def test_una_sincronizacion_pendiente_al_dia_no_alarma():
    """Pendiente con reintento agendado en el futuro es el sistema funcionando,
    no un problema: el loop la va a tomar."""
    datos = {**SANO, "crm": {**SANO["crm"], "pendientes": 1, "mas_intentos": 2}}

    assert metricas.alertas(datos) == []


# --- contra la base ---------------------------------------------------------

@pytest.mark.db
async def test_reunir_devuelve_las_cinco_familias(pool_en_transaccion):
    datos = await metricas.reunir()

    assert set(datos) >= {"crm", "conversaciones", "turnos", "hoy", "alertas", "estado"}
    assert set(datos["crm"]) == {
        "pendientes", "agotadas", "vencidas_sin_reintentar", "mas_intentos"}
    assert datos["estado"] in ("ok", "atencion")


@pytest.mark.db
async def test_una_conversacion_agotada_aparece_en_las_metricas(pool_en_transaccion):
    conexion = pool_en_transaccion
    await conexion.execute(
        "INSERT INTO conversaciones (canal, identificador, estado, crm_pendiente, "
        "crm_reintentar_en, crm_intentos) "
        "VALUES ('consola', 'metricas-test', 'calificada', true, NULL, 5)"
    )

    datos = await metricas.reunir()

    assert datos["crm"]["agotadas"] >= 1
    assert datos["estado"] == "atencion"
    assert any("no llego a Kommo" in a for a in datos["alertas"])


@pytest.mark.db
async def test_si_la_base_falla_lo_dice_en_vez_de_explotar(monkeypatch):
    """El endpoint se pega cada un minuto. Que reviente por una consulta es
    perder justo la señal que uno fue a buscar."""
    from app import db

    async def _explota(*a, **k):
        raise RuntimeError("sin conexion")

    monkeypatch.setattr(db, "consultar_una", _explota)

    datos = await metricas.reunir()

    assert datos["estado"] == "sin datos"
    assert "sin conexion" in datos["error"]


# --- la promoción del mes ---------------------------------------------------

def test_dentro_del_mes_la_promocion_esta_vigente():
    estado = metricas.estado_de_los_precios(date(2026, 9, 9))
    assert estado["vigente"] is True
    assert estado["dias_que_quedan"] == 21


def test_avisa_unos_dias_antes_de_que_venza():
    """Avisar cuando ya venció llega tarde: para entonces el agente ya dejó de
    ofrecer la promoción."""
    estado = metricas.estado_de_los_precios(date(2026, 9, 28))
    avisos = metricas.alertas({**SANO, "precios": estado})

    assert any("termina en 2 dia(s)" in a for a in avisos)


def test_vencida_la_promocion_lo_dice_y_explica_qué_hacer():
    """El código vuelve solo al precio normal, que es lo seguro. Pero deja de
    ofrecer la promoción sin que nadie se entere."""
    estado = metricas.estado_de_los_precios(date(2026, 10, 3))
    avisos = metricas.alertas({**SANO, "precios": estado})

    assert any("cotizando al precio normal" in a for a in avisos)
    assert any("productos.yaml" in a for a in avisos)


def test_a_mitad_de_mes_no_molesta():
    estado = metricas.estado_de_los_precios(date(2026, 9, 9))
    assert metricas.alertas({**SANO, "precios": estado}) == []
