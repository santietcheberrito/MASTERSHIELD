"""Destino Notion: mapeo de propiedades y comportamiento ante fallas.

La regla que se verifica acá es la más importante de la integración: **si el
CRM falla, la conversación no se pierde**. Queda marcada para reintento y el
agente sigue contestando. Una integración caída no puede dejar sin respuesta a
alguien que está escribiendo.
"""

import httpx
import pytest

from app import scoring
from app.crm import documento, notion, sincronizacion

pytestmark = [pytest.mark.db,
              pytest.mark.usefixtures("settings_de_prueba", "destino_notion")]


@pytest.fixture
def destino_notion(monkeypatch):
    from app.config import obtener_settings

    monkeypatch.setenv("CRM_DESTINO", "notion")
    obtener_settings.cache_clear()
    yield
    obtener_settings.cache_clear()

COMPLETO = {
    "linea": "arquitectonico", "objetivo": "control_solar", "superficie": "ventanas", "zona": "quito_y_valles",
    "metros_cuadrados": 32, "garantia_anios": 10, "aplicacion": "oficina",
    "urgencia": "inmediato", "tipo_cliente": "empresa", "disponibilidad": "martes",
}


async def _conversacion(conexion, datos=None, estado="calificada"):
    id_conv = await conexion.fetchval(
        "INSERT INTO conversaciones (canal, identificador, nombre, telefono, datos, estado) "
        "VALUES ('consola', 'notion-test', 'Lucía Fernández', '+593987112233', $1::jsonb, $2) "
        "RETURNING id", datos or {}, estado,
    )
    await conexion.execute(
        "INSERT INTO mensajes (conversacion_id, rol, contenido, id_externo) "
        "VALUES ($1, 'cliente', 'necesito lamina', $2)", id_conv, f"nt:{id_conv}",
    )
    return id_conv


# --- mapeo ------------------------------------------------------------------

async def test_las_propiedades_traducen_el_documento(pool_en_transaccion, sin_promocion):
    doc = await documento.armar(await _conversacion(pool_en_transaccion, COMPLETO))
    props = notion.propiedades(doc)

    assert props["Etapa"]["select"]["name"] == scoring.ALTA
    assert props["Score"]["number"] == doc.lead["score"]
    assert props["Teléfono"]["phone_number"] == "+593987112233"
    assert props["Presupuesto estimado"]["number"] == 42 * 32
    assert props["Producto sugerido"]["select"]["name"] == "Control Solar Ventanas"
    assert props["Llamar antes de"]["date"]["start"]


async def test_los_campos_vacios_no_se_mandan(pool_en_transaccion):
    """Mandar un select en null hace que Notion rechace el request entero."""
    doc = await documento.armar(
        await _conversacion(pool_en_transaccion, {"linea": "arquitectonico"})
    )
    props = notion.propiedades(doc)
    assert "Urgencia" not in props
    assert "Modelo de vehículo" not in props
    assert "Contacto" in props and "Etapa" in props


async def test_la_conversacion_queda_referenciada(pool_en_transaccion):
    """Es lo que hace idempotente la sincronización: si la fila ya existe, se
    actualiza en vez de crear una segunda."""
    id_conv = await _conversacion(pool_en_transaccion, COMPLETO)
    props = notion.propiedades(await documento.armar(id_conv))
    assert props["Conversación"]["number"] == id_conv


async def test_la_nota_va_como_bloques(pool_en_transaccion):
    doc = await documento.armar(await _conversacion(pool_en_transaccion, COMPLETO))
    bloques = notion.bloques(doc)
    assert bloques and all(b["type"] == "paragraph" for b in bloques)
    assert len(bloques) <= 100, "Notion acepta 100 bloques por request"


# --- si el CRM falla --------------------------------------------------------

async def test_sin_crm_configurado_queda_pendiente_sin_ensuciar_los_eventos(
    pool_en_transaccion,
):
    """No es un fallo: es que todavía no hay CRM conectado."""
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion, COMPLETO)

    assert await sincronizacion.sincronizar(id_conv) is False

    fila = await conexion.fetchrow(
        "SELECT crm_pendiente, crm_intentos FROM conversaciones WHERE id = $1", id_conv
    )
    assert fila["crm_pendiente"] is True
    assert fila["crm_intentos"] == 1
    assert await conexion.fetchval(
        "SELECT count(*) FROM eventos WHERE conversacion_id = $1 AND estado = 'error'", id_conv
    ) == 0


async def test_un_fallo_del_crm_no_rompe_la_conversacion(pool_en_transaccion, monkeypatch):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion, COMPLETO)

    async def _explota(doc):
        raise httpx.ConnectError("no hay red")

    monkeypatch.setattr(notion, "sincronizar", _explota)

    assert await sincronizacion.sincronizar(id_conv) is False

    fila = await conexion.fetchrow(
        "SELECT estado, crm_pendiente, crm_reintentar_en FROM conversaciones WHERE id = $1",
        id_conv,
    )
    assert fila["estado"] == "calificada", "la conversación sigue como estaba"
    assert fila["crm_pendiente"] is True
    assert fila["crm_reintentar_en"] is not None
    evento = await conexion.fetchrow(
        "SELECT detalle FROM eventos WHERE conversacion_id = $1 AND estado = 'error'", id_conv
    )
    assert "ConnectError" in evento["detalle"]["detalle"]


async def test_cuando_sale_bien_deja_la_referencia(pool_en_transaccion, monkeypatch):
    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion, COMPLETO)

    async def _ok(doc):
        return "pagina-123"

    monkeypatch.setattr(notion, "sincronizar", _ok)

    assert await sincronizacion.sincronizar(id_conv) is True

    fila = await conexion.fetchrow(
        "SELECT crm_pendiente, crm_sincronizada_en, crm_referencia "
        "FROM conversaciones WHERE id = $1", id_conv
    )
    assert fila["crm_pendiente"] is False
    assert fila["crm_sincronizada_en"] is not None
    assert fila["crm_referencia"]["notion"]["pagina"] == "pagina-123"


async def test_finalizar_no_falla_aunque_el_crm_este_caido(pool_en_transaccion, monkeypatch):
    """Lo que ve el cliente no puede depender de que Notion o Kommo respondan."""
    from app.agente import herramientas

    conexion = pool_en_transaccion
    id_conv = await _conversacion(conexion, {**COMPLETO, "telefono": "0987112233"})

    async def _explota(doc):
        raise httpx.ConnectError("no hay red")

    monkeypatch.setattr(notion, "sincronizar", _explota)

    r = await herramientas.finalizar_calificacion(id_conv)

    assert r["finalizada"] is True
    assert await conexion.fetchval(
        "SELECT estado FROM conversaciones WHERE id = $1", id_conv
    ) == "calificada"
