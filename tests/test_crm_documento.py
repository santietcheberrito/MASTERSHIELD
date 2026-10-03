"""El documento que se carga en el CRM.

No sabe de Kommo ni de Notion: arma qué contacto, qué lead, qué nota y qué
tarea corresponden a una conversación. Es lo que permite validar con el cliente
qué datos le llegan al vendedor sin tener todavía acceso al CRM.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import scoring
from app.crm import documento
from app.precios import informar_precios

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("settings_de_prueba")]

QUITO = ZoneInfo("America/Guayaquil")

COMPLETO = {
    "linea": "arquitectonico", "objetivo": "control_solar", "superficie": "ventanas", "zona": "quito_y_valles",
    "metros_cuadrados": 25, "garantia_anios": 10, "aplicacion": "oficina",
    "urgencia": "inmediato", "tipo_cliente": "empresa", "telefono": "0999123456",
    "disponibilidad": "jueves por la mañana",
}


async def _conversacion(conexion, datos=None, estado="calificada", telefono="+593999123456"):
    id_conv = await conexion.fetchval(
        "INSERT INTO conversaciones (canal, identificador, nombre, telefono, datos, estado) "
        "VALUES ('consola', 'doc-test', 'Maria Paez', $1, $2::jsonb, $3) RETURNING id",
        telefono, datos or {}, estado,
    )
    for n, (rol, texto) in enumerate(
        [("cliente", "buenas, necesito lamina para una oficina"),
         ("agente", "Buenas tardes, ¿en qué ciudad está?"),
         ("cliente", "en Quito, son 25 metros")], 1
    ):
        await conexion.execute(
            "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo) "
            "VALUES ($1, $2, $3, $4)", id_conv, rol, texto, f"doc:{id_conv}:{n}",
        )
    return id_conv


async def test_arma_el_lead_completo(pool_en_transaccion, sin_promocion):
    doc = await documento.armar(await _conversacion(pool_en_transaccion, COMPLETO))

    assert doc.lead["etapa"] == scoring.ALTA
    assert doc.lead["producto"] == "Control Solar Ventanas"
    assert doc.lead["presupuesto"] == 42 * 25
    assert doc.lead["zona"] == "Quito y valles"
    assert doc.lead["garantia"] == "10 años"
    assert doc.contacto["telefono"] == "+593999123456"


async def test_la_nota_lleva_el_resumen_y_la_transcripcion(pool_en_transaccion, sin_promocion):
    """El vendedor lee el resumen antes de marcar; la transcripción está para
    cuando necesite saber qué se habló."""
    doc = await documento.armar(await _conversacion(pool_en_transaccion, COMPLETO))

    resumen, transcripcion = doc.nota.split("---", 1)
    assert "USD 1,050.00 + IVA" in resumen
    assert "Score" in resumen
    assert "Cliente: buenas, necesito lamina" in transcripcion
    assert "Agente: Buenas tardes" in transcripcion


async def test_el_desglose_del_score_queda_en_la_nota(pool_en_transaccion):
    doc = await documento.armar(await _conversacion(pool_en_transaccion, COMPLETO))
    assert "urgencia: inmediato" in doc.nota


async def test_la_tarea_no_tiene_responsable(pool_en_transaccion):
    """Los 3 vendedores la ven y se la queda el primero que la toma."""
    doc = await documento.armar(await _conversacion(pool_en_transaccion, COMPLETO))
    assert doc.tarea["responsable"] is None
    assert "Maria Paez" in doc.tarea["texto"]


async def test_la_tarea_dice_cuando_llamar(pool_en_transaccion):
    """La lista de tareas es lo único que el asesor mira antes de marcar. Con la
    franja enterrada en un campo del lead, "hoy en una hora" no la ve nadie."""
    doc = await documento.armar(await _conversacion(pool_en_transaccion, COMPLETO))
    assert "jueves por la mañana" in doc.tarea["texto"]


async def test_la_franja_se_lee_como_la_diria_una_persona(pool_en_transaccion):
    """Desde el 11/9 se guarda `manana` o `tarde`. El asesor lee "por la tarde",
    no el valor interno."""
    doc = await documento.armar(
        await _conversacion(pool_en_transaccion, {**COMPLETO, "disponibilidad": "tarde"}))
    assert doc.tarea["texto"].endswith("— por la tarde")
    assert doc.lead["disponibilidad"] == "por la tarde"
    assert "Llamar: por la tarde" in doc.nota


async def test_sin_disponibilidad_la_tarea_no_queda_colgada(pool_en_transaccion):
    datos = {k: v for k, v in COMPLETO.items() if k != "disponibilidad"}
    doc = await documento.armar(await _conversacion(pool_en_transaccion, datos))
    assert doc.tarea["texto"].rstrip().endswith(")")


async def test_una_consulta_de_afuera_no_lleva_presupuesto(pool_en_transaccion):
    doc = await documento.armar(
        await _conversacion(pool_en_transaccion, {**COMPLETO, "zona": "fuera_del_pais"})
    )
    assert doc.lead["etapa"] == scoring.FUERA
    assert doc.lead["presupuesto"] is None
    assert "fuera de Ecuador" in doc.nota


async def test_bajo_el_minimo_la_nota_dice_por_que(pool_en_transaccion):
    doc = await documento.armar(
        await _conversacion(pool_en_transaccion, {**COMPLETO, "metros_cuadrados": 3})
    )
    assert doc.lead["etapa"] == scoring.BAJO_MINIMO
    assert "mínimo de 5 m²" in doc.nota


async def test_el_estado_manda_sobre_el_puntaje(pool_en_transaccion):
    """Si una persona se hizo cargo, la etapa lo tiene que decir aunque el
    score sea alto: si no, un vendedor llama a alguien que ya está atendido."""
    doc = await documento.armar(
        await _conversacion(pool_en_transaccion, COMPLETO, estado="derivada")
    )
    assert doc.lead["etapa"] == scoring.DERIVADA
    assert doc.lead["score"] > 0


async def test_mientras_conversa_no_es_un_lead_para_llamar(pool_en_transaccion):
    doc = await documento.armar(
        await _conversacion(pool_en_transaccion, COMPLETO, estado="activa")
    )
    assert doc.lead["etapa"] == scoring.CONVERSANDO


async def test_sin_nombre_usa_el_telefono(pool_en_transaccion):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion, COMPLETO)
    await conexion.execute("UPDATE conversaciones SET nombre = NULL WHERE id = $1", id_conv)
    doc = await documento.armar(id_conv)
    assert doc.contacto["nombre"] == "+593999123456"


async def test_una_conversacion_que_no_existe(pool_en_transaccion):
    with pytest.raises(ValueError):
        await documento.armar(999999999)


# --- cuándo llamar ----------------------------------------------------------

def test_dentro_del_horario_se_llama_hoy(settings_de_prueba):
    miercoles = datetime(2026, 8, 26, 11, 0, tzinfo=QUITO)
    assert documento.proxima_fecha_de_llamado(miercoles).day == 26


def test_de_noche_se_llama_al_dia_siguiente(settings_de_prueba):
    """El agente atiende a cualquier hora —es una ventaja de venta— pero la
    tarea se agenda para cuando haya alguien."""
    miercoles_tarde = datetime(2026, 8, 26, 22, 30, tzinfo=QUITO)
    assert documento.proxima_fecha_de_llamado(miercoles_tarde).day == 27


def test_el_fin_de_semana_se_llama_el_lunes(settings_de_prueba):
    sabado = datetime(2026, 8, 29, 11, 0, tzinfo=QUITO)
    resultado = documento.proxima_fecha_de_llamado(sabado)
    assert resultado.weekday() == 0
    assert resultado.day == 31


def test_el_viernes_de_noche_se_llama_el_lunes(settings_de_prueba):
    viernes = datetime(2026, 8, 28, 23, 0, tzinfo=QUITO)
    assert documento.proxima_fecha_de_llamado(viernes).weekday() == 0


# --- derivaciones -----------------------------------------------------------

async def _derivar(conexion, id_conv, motivo):
    await conexion.execute(
        "UPDATE conversaciones SET estado = 'derivada' WHERE id = $1", id_conv)
    await conexion.execute(
        "INSERT INTO eventos (conversacion_id, tipo, estado, detalle) "
        "VALUES ($1, 'escalado_a_humano', 'ok', $2::jsonb)",
        id_conv, {"motivo": motivo},
    )


async def test_la_tarea_de_una_derivacion_dice_por_que(pool_en_transaccion):
    """El puntaje solo sabe que está derivada. Por qué lo está quedó en el
    evento, y es lo primero que necesita leer quien la tome."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion, COMPLETO)
    await _derivar(conexion, id_conv, "la persona pidió hablar con alguien")

    doc = await documento.armar(id_conv)

    assert doc.tarea["texto"].startswith("ATENDER")
    assert "pidió hablar con alguien" in doc.tarea["texto"]
    assert doc.tarea["urgente"] is True
    assert doc.lead["etapa"] == scoring.DERIVADA


async def test_una_calificacion_normal_no_es_urgente(pool_en_transaccion):
    doc = await documento.armar(await _conversacion(pool_en_transaccion, COMPLETO))
    assert doc.tarea["urgente"] is False
    assert doc.tarea["texto"].startswith("Llamar a")


async def test_la_estimacion_del_crm_usa_el_precio_que_rige_hoy(pool_en_transaccion):
    """El agente ya no da totales, pero el vendedor necesita saber si va a un
    trabajo de 10 m² o de 200. Ese estimado tiene que contar la misma historia
    que la conversación: si el agente informó el precio de promoción, el CRM no
    puede mostrar el de lista.

    Sin `sin_promocion`, así que usa el precio que rige de verdad.
    """
    doc = await documento.armar(await _conversacion(pool_en_transaccion, COMPLETO))
    precios = informar_precios("control_solar_ventanas", "quito_y_valles")
    de_diez = next(c for c in precios.calidades if c.garantia_anios == 10)
    vigente = de_diez.precio_especial or de_diez.precio_normal

    assert doc.lead["presupuesto"] == vigente * 25


# --- la ciudad en la nota del asesor ------------------------------------------

def _puntaje():
    return scoring.Puntaje(score=50, clasificacion="tibio", etapa="Conversando")


def test_la_nota_dice_la_ciudad_y_la_zona(settings_de_prueba):
    """Solo la zona no le sirve a nadie para subirse a un auto: el asesor
    necesita el lugar, y la zona explica el precio (16/9/2026)."""
    resumen = documento.resumir(
        {"linea": "arquitectonico", "objetivo": "seguridad", "zona": "zona_verde",
         "ciudad": "Cuenca", "metros_cuadrados": 50},
        _puntaje(), None)

    assert "Cuenca (zona verde)" in resumen


def test_el_minimo_de_la_zona_no_queda_dentro_del_parentesis(settings_de_prueba):
    """Con el minimo en el nombre de la zona salia "Cuenca (zona verde (minimo
    20 m2))". Va como parte aparte: el asesor lo necesita para ver si es viable."""
    resumen = documento.resumir(
        {"linea": "arquitectonico", "objetivo": "seguridad", "zona": "zona_verde",
         "ciudad": "Cuenca", "metros_cuadrados": 50},
        _puntaje(), None)

    assert "Cuenca (zona verde) · mínimo 20 m²" in resumen
    assert "((" not in resumen and "))" not in resumen


def test_sin_ciudad_relevada_la_nota_sale_igual(settings_de_prueba):
    """Los leads anteriores al campo no la tienen."""
    resumen = documento.resumir(
        {"linea": "arquitectonico", "objetivo": "seguridad", "zona": "quito_y_valles"},
        _puntaje(), None)

    assert "Quito y valles" in resumen
    assert "(" not in resumen.split("·")[-1], "sin ciudad no se abre un parentesis vacio"
