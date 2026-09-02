"""Cliente y sincronización con Kommo, contra una API simulada.

Lo que se verifica es lo que cuesta caro si sale mal: que no se dupliquen
contactos ni leads de la misma persona, que los datos caigan en los campos que
corresponden, y que un 4xx no se reintente en vano.
"""

import json
from datetime import date

import httpx
import pytest
import respx

from app.crm.documento import Documento
from app.kommo import sincronizacion
from app.kommo.cliente import Kommo, KommoError, configuracion
from app.scoring import ALTA, FUERA, Puntaje

pytestmark = pytest.mark.usefixtures("settings_kommo")

BASE = "https://cuenta-de-prueba.kommo.com/api/v4"


@pytest.fixture
def settings_kommo(monkeypatch):
    from app.config import obtener_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:c@host:5432/base")
    monkeypatch.setenv("KOMMO_SUBDOMAIN", "cuenta-de-prueba")
    monkeypatch.setenv("KOMMO_ACCESS_TOKEN", "token-de-prueba")
    obtener_settings.cache_clear()
    yield
    obtener_settings.cache_clear()


def documento(etapa=ALTA, clasificacion="alta", presupuesto=1470.0):
    return Documento(
        conversacion_id=7,
        contacto={"nombre": "Andrea Salazar", "telefono": "+593987445566"},
        lead={
            "nombre": "Andrea Salazar", "etapa": etapa, "score": 81,
            "clasificacion": clasificacion, "canal": "Telegram",
            "telefono": "+593987445566", "zona": "Quito y valles",
            "linea": "Arquitectónico", "producto": "Control Solar Arquitectónico",
            "metros_cuadrados": 35, "presupuesto": presupuesto,
            "garantia": "10 años", "aplicacion": "Oficina", "urgencia": "Inmediato",
            "tipo_cliente": "Empresa", "disponibilidad": "martes por la tarde",
            "modelo_vehiculo": None, "medidas_detalle": None,
        },
        nota="Resumen\n\n---\n\nTranscripción",
        tarea={"texto": "Llamar a Andrea Salazar", "vence": date(2026, 9, 2),
               "responsable": None},
        puntaje=Puntaje(81, clasificacion, etapa),
    )


def _cuerpo(mock, metodo, fragmento_url):
    """El JSON de la primera llamada que coincide. Comparar texto no sirve:
    httpx serializa compacto y sin espacios."""
    for llamada in mock.calls:
        url = str(llamada.request.url)
        if llamada.request.method == metodo and fragmento_url in url:
            return json.loads(llamada.request.content.decode())
    raise AssertionError(f"no hubo {metodo} a {fragmento_url}")


def _rutas(mock, *, contacto_existente=None):
    """Deja montada una API que responde a todo lo que hace la sincronización."""
    if contacto_existente:
        mock.get(f"{BASE}/contacts").mock(return_value=httpx.Response(
            200, json={"_embedded": {"contacts": [{"id": contacto_existente}]}}))
    else:
        mock.get(f"{BASE}/contacts").mock(return_value=httpx.Response(204))
    mock.post(f"{BASE}/contacts").mock(return_value=httpx.Response(
        200, json={"_embedded": {"contacts": [{"id": 5993200}]}}))
    mock.post(f"{BASE}/leads").mock(return_value=httpx.Response(
        200, json={"_embedded": {"leads": [{"id": 2412202}]}}))
    mock.patch(url__regex=rf"{BASE}/leads/\d+$").mock(return_value=httpx.Response(200, json={}))
    mock.post(url__regex=rf"{BASE}/leads/\d+/notes").mock(
        return_value=httpx.Response(200, json={"_embedded": {"notes": [{"id": 1}]}}))
    mock.post(f"{BASE}/tasks").mock(return_value=httpx.Response(
        200, json={"_embedded": {"tasks": [{"id": 204028}]}}))


# --- primera carga ----------------------------------------------------------

async def test_crea_contacto_lead_nota_y_tarea():
    with respx.mock as mock:
        _rutas(mock)
        referencia = await sincronizacion.sincronizar(documento())

    assert referencia == {"destino": "kommo", "contacto": 5993200, "lead": 2412202,
                          "tarea": 204028, "huella_nota": referencia["huella_nota"]}


async def test_los_datos_caen_en_los_campos_que_corresponden():
    with respx.mock as mock:
        _rutas(mock)
        await sincronizacion.sincronizar(documento())
        lead = _cuerpo(mock, "POST", "/leads")[0]

    ids = configuracion()["campos_lead"]
    valores = {c["field_id"]: c["values"][0]["value"] for c in lead["custom_fields_values"]}
    assert valores[ids["score"]] == 81
    assert valores[ids["presupuesto"]] == 1470.0
    assert valores[ids["zona"]] == "Quito y valles"
    assert valores[ids["clasificacion"]] == "Alta"
    assert lead["status_id"] == configuracion()["etapas"][ALTA]
    assert lead["pipeline_id"] == configuracion()["embudo"]["id"]


async def test_el_monto_del_lead_es_el_presupuesto():
    """Es lo que Kommo suma en sus reportes."""
    with respx.mock as mock:
        _rutas(mock)
        await sincronizacion.sincronizar(documento())
        assert _cuerpo(mock, "POST", "/leads")[0]["price"] == 1470


# --- no duplicar ------------------------------------------------------------

async def test_reutiliza_el_contacto_encontrado_por_telefono():
    """Sin esto, la misma persona termina con un contacto por conversación."""
    with respx.mock as mock:
        _rutas(mock, contacto_existente=999)
        referencia = await sincronizacion.sincronizar(documento())
        creo_contacto = any(
            c.request.method == "POST" and str(c.request.url).endswith("/contacts")
            for c in mock.calls
        )

    assert referencia["contacto"] == 999
    assert not creo_contacto


async def test_la_segunda_vez_actualiza_el_lead_en_vez_de_crear_otro():
    previa = {"contacto": 5993200, "lead": 2412202, "tarea": 204028,
              "huella_nota": "otra"}
    with respx.mock as mock:
        _rutas(mock)
        referencia = await sincronizacion.sincronizar(documento(), previa)

        metodos = [(c.request.method, str(c.request.url)) for c in mock.calls]

    assert referencia["lead"] == 2412202
    assert not any(m == "POST" and u.endswith("/leads") for m, u in metodos)
    assert any(m == "PATCH" for m, _ in metodos)


async def test_la_tarea_no_se_crea_dos_veces():
    previa = {"contacto": 1, "lead": 2, "tarea": 204028, "huella_nota": "x"}
    with respx.mock as mock:
        _rutas(mock)
        await sincronizacion.sincronizar(documento(), previa)
        assert not any(str(c.request.url).endswith("/tasks") for c in mock.calls)


async def test_una_nota_identica_no_se_repite():
    """Una sincronización se puede repetir por un reintento; la nota no."""
    with respx.mock as mock:
        _rutas(mock)
        primera = await sincronizacion.sincronizar(documento())
        notas_1 = sum(1 for c in mock.calls if str(c.request.url).endswith("/notes"))
        await sincronizacion.sincronizar(documento(), primera)
        notas_2 = sum(1 for c in mock.calls if str(c.request.url).endswith("/notes"))

    assert notas_1 == 1
    assert notas_2 == 1, "la segunda no agrega nota"


# --- qué genera tarea y qué no ---------------------------------------------

async def test_un_descarte_no_genera_tarea_de_llamado():
    """Llenar la lista de tareas de cosas que nadie va a hacer es la forma más
    rápida de que dejen de mirarla."""
    with respx.mock as mock:
        _rutas(mock)
        referencia = await sincronizacion.sincronizar(
            documento(etapa=FUERA, clasificacion="descartada", presupuesto=None)
        )

    assert referencia["tarea"] is None
    assert not any(str(c.request.url).endswith("/tasks") for c in mock.calls)


# --- el cliente HTTP --------------------------------------------------------

async def test_un_4xx_no_se_reintenta():
    """No mejora insistiendo: es el payload o los permisos."""
    with respx.mock as mock:
        ruta = mock.get(f"{BASE}/account").mock(
            return_value=httpx.Response(403, text="forbidden"))
        with pytest.raises(KommoError, match="403"):
            await Kommo().cuenta()
    assert ruta.call_count == 1


async def test_un_5xx_se_reintenta(monkeypatch):
    monkeypatch.setattr("app.kommo.cliente.ESPERA_BASE", 0)
    with respx.mock as mock:
        ruta = mock.get(f"{BASE}/account").mock(return_value=httpx.Response(500))
        with pytest.raises(KommoError):
            await Kommo().cuenta()
    assert ruta.call_count == 3


async def test_sin_resultados_devuelve_none():
    """Kommo contesta 204 cuando una búsqueda no encuentra nada."""
    with respx.mock as mock:
        mock.get(f"{BASE}/contacts").mock(return_value=httpx.Response(204))
        assert await Kommo().buscar_contacto("+593999000000") is None
