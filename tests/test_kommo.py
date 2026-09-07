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
from app.kommo import archivos, sincronizacion
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
                          "tarea": 204028, "huella_nota": referencia["huella_nota"],
                          # Sin fotos queda vacío, pero el campo viaja: es lo
                          # que evita volver a subir lo mismo en cada reintento.
                          "archivos": {}}


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


# --- archivos ---------------------------------------------------------------

DRIVE = "https://drive-x.kommo.com"


def _drive(respx_mock):
    respx_mock.get(f"{BASE}/account?with=drive_url").mock(
        return_value=httpx.Response(200, json={"drive_url": DRIVE}))


def _sesion(respx_mock, max_part_size=1024):
    respx_mock.post(f"{DRIVE}/v1.0/sessions").mock(
        return_value=httpx.Response(200, json={
            "session_id": 1, "upload_url": f"{DRIVE}/upload/tok",
            "max_file_size": 314572800, "max_part_size": max_part_size,
        }))


@respx.mock
async def test_sube_un_archivo_y_devuelve_su_uuid(respx_mock):
    _drive(respx_mock)
    _sesion(respx_mock)
    respx_mock.post(f"{DRIVE}/upload/tok").mock(
        return_value=httpx.Response(200, json={"uuid": "u-1", "name": "foto-1"}))

    kommo = Kommo()
    drive = await archivos.drive_de_la_cuenta(kommo)
    uuid = await archivos.subir(kommo, drive, "foto-1.jpg", b"x" * 100, "image/jpeg")

    assert uuid == "u-1"


@respx.mock
async def test_un_archivo_grande_se_sube_por_partes(respx_mock):
    """Kommo limita las partes a medio mega y una foto de teléfono pasa eso sin
    esfuerzo. Cada respuesta dice a dónde va la siguiente."""
    _drive(respx_mock)
    _sesion(respx_mock, max_part_size=10)
    partes = []

    def _recibir(request):
        partes.append(request.content)
        if len(partes) < 3:
            return httpx.Response(200, json={"next_url": f"{DRIVE}/upload/tok"})
        return httpx.Response(200, json={"uuid": "u-grande"})

    respx_mock.post(f"{DRIVE}/upload/tok").mock(side_effect=_recibir)

    kommo = Kommo()
    uuid = await archivos.subir(kommo, DRIVE, "grande.jpg", b"a" * 25, "image/jpeg")

    assert uuid == "u-grande"
    assert len(partes) == 3
    assert b"".join(partes) == b"a" * 25, "el archivo llega entero"


@respx.mock
async def test_adjuntar_tolera_la_respuesta_vacia(respx_mock):
    """Ese PUT contesta 200 con el cuerpo vacío, no con un JSON. Sin esto la
    llamada salía bien y el cliente reventaba al parsear."""
    ruta = respx_mock.put(f"{BASE}/leads/77/files").mock(
        return_value=httpx.Response(200, content=b""))

    await archivos.adjuntar(Kommo(), 77, ["u-1", "u-2"])

    assert ruta.called
    assert json.loads(ruta.calls[0].request.content) == [
        {"file_uuid": "u-1"}, {"file_uuid": "u-2"}]


async def test_adjuntar_sin_archivos_no_llama_a_nadie():
    await archivos.adjuntar(Kommo(), 77, [])  # no explota y no pega a la red


def test_una_foto_de_whatsapp_recibe_un_nombre_legible():
    """No traen nombre. Un asesor que ve "foto-1.jpg" entiende más que con un
    uuid de treinta caracteres."""
    assert archivos.nombre_para({"mime": "image/jpeg"}, 1) == "foto-1.jpg"
    assert archivos.nombre_para({"mime": "image/png"}, 3) == "foto-3.png"


def test_un_documento_conserva_su_nombre():
    assert archivos.nombre_para(
        {"nombre": "medidas.pdf", "mime": "application/pdf"}, 1) == "medidas.pdf"


@respx.mock
async def test_las_fotos_del_cliente_llegan_al_lead(respx_mock):
    """El agente le dice a la persona que un asesor revisa sus fotos. Hasta
    ahora esa promesa no se cumplía: la foto quedaba en la base y el asesor
    abría el lead sin nada."""
    _rutas(respx_mock)
    _drive(respx_mock)
    _sesion(respx_mock)
    respx_mock.post(f"{DRIVE}/upload/tok").mock(
        return_value=httpx.Response(200, json={"uuid": "u-foto"}))
    adjuntar = respx_mock.put(f"{BASE}/leads/2412202/files").mock(
        return_value=httpx.Response(200, content=b""))

    doc = documento()
    doc.archivos = [{"id": "media-1", "contenido": b"jpeg", "mime": "image/jpeg"}]
    referencia = await sincronizacion.sincronizar(doc)

    assert adjuntar.called
    assert referencia["archivos"] == {"media-1": "u-foto"}


@respx.mock
async def test_un_reintento_no_vuelve_a_subir_la_misma_foto(respx_mock):
    """La sincronización se reintenta hasta seis veces. Sin esto, el asesor
    abriría el lead con la misma foto repetida seis veces."""
    _rutas(respx_mock)
    _drive(respx_mock)
    sesion = _sesion(respx_mock)
    respx_mock.post(f"{DRIVE}/upload/tok").mock(
        return_value=httpx.Response(200, json={"uuid": "u-foto"}))
    respx_mock.put(f"{BASE}/leads/2412202/files").mock(
        return_value=httpx.Response(200, content=b""))

    doc = documento()
    doc.archivos = [{"id": "media-1", "contenido": b"jpeg", "mime": "image/jpeg"}]
    primera = await sincronizacion.sincronizar(doc)
    llamadas = len(respx_mock.calls)

    segunda = await sincronizacion.sincronizar(doc, primera)

    assert segunda["archivos"] == primera["archivos"]
    subidas = [c for c in list(respx_mock.calls)[llamadas:]
               if "drive-x" in str(c.request.url)]
    assert subidas == [], "no se toca el drive la segunda vez"


@respx.mock
async def test_si_falla_la_subida_el_lead_igual_se_carga(respx_mock):
    """Perder una foto es malo; perder el lead entero por una foto sería peor:
    el asesor se queda sin nadie a quien llamar."""
    _rutas(respx_mock)
    _drive(respx_mock)
    _sesion(respx_mock)
    respx_mock.post(f"{DRIVE}/upload/tok").mock(
        return_value=httpx.Response(500, text="el drive se cayó"))

    doc = documento()
    doc.archivos = [{"id": "media-1", "contenido": b"jpeg", "mime": "image/jpeg"}]
    referencia = await sincronizacion.sincronizar(doc)

    assert referencia["lead"] == 2412202, "el lead se cargó igual"
    assert referencia["archivos"] == {}, "queda pendiente para el próximo intento"
