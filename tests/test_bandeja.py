"""La bandeja humana: que se espeja, que se descarta y que se valida."""

import hashlib
import hmac
import json

import httpx
import pytest
import respx

from app import bandeja

SECRETO = "secreto-de-prueba"


@pytest.fixture
def con_bandeja(monkeypatch, settings_de_prueba):
    from app.config import obtener_settings
    s = obtener_settings()
    monkeypatch.setattr(s, "chatwoot_url", "https://bandeja.test", raising=False)
    monkeypatch.setattr(s, "chatwoot_token", "token", raising=False)
    monkeypatch.setattr(s, "chatwoot_cuenta", 2, raising=False)
    monkeypatch.setattr(s, "chatwoot_bandeja", 1, raising=False)
    monkeypatch.setattr(s, "chatwoot_secreto", SECRETO, raising=False)
    return s


def aviso(**cambios):
    """Lo que manda Chatwoot cuando se crea un mensaje."""
    d = {
        "event": "message_created",
        "id": 42,
        "content": "le confirmo la visita para el jueves",
        "message_type": "outgoing",
        "private": False,
        "content_attributes": {},
        "conversation": {"id": 7},
        "sender": {"name": "Esteban", "type": "user"},
    }
    d.update(cambios)
    return d


# --- que llega al cliente y que no --------------------------------------------

def test_lo_que_escribe_un_asesor_sale(con_bandeja):
    r = bandeja.respuesta_humana(aviso())

    assert r is not None
    assert r["texto"] == "le confirmo la visita para el jueves"
    assert r["conversacion"] == 7
    assert r["autor"] == "Esteban"
    assert r["id_externo"] == "chatwoot:42"


def test_lo_que_espejamos_nosotros_no_vuelve_a_salir(con_bandeja):
    """Chatwoot avisa de todos los salientes, los nuestros incluidos. Sin este
    filtro cada respuesta del agente se reenviaria en un bucle infinito."""
    assert bandeja.respuesta_humana(
        aviso(content_attributes={bandeja.MARCA: True})) is None


def test_una_nota_privada_no_le_llega_al_cliente(con_bandeja):
    """Es para el equipo: el cliente no tiene que verla."""
    assert bandeja.respuesta_humana(aviso(private=True)) is None


def test_lo_que_entra_del_cliente_no_se_reenvia(con_bandeja):
    assert bandeja.respuesta_humana(aviso(message_type="incoming")) is None


@pytest.mark.parametrize("evento", ["conversation_updated", "webwidget_triggered", ""])
def test_otros_eventos_se_ignoran(con_bandeja, evento):
    assert bandeja.respuesta_humana(aviso(event=evento)) is None


def test_un_mensaje_vacio_no_se_manda(con_bandeja):
    assert bandeja.respuesta_humana(aviso(content="   ")) is None


# --- la firma -----------------------------------------------------------------

def firmar(cuerpo: bytes, marca: str, secreto: str = SECRETO) -> str:
    return "sha256=" + hmac.new(
        secreto.encode(), marca.encode() + b"." + cuerpo, hashlib.sha256).hexdigest()


def test_la_firma_de_chatwoot_se_acepta(con_bandeja):
    """El esquema no esta documentado: se dedujo de una firma real el 3/10/2026."""
    cuerpo, marca = json.dumps(aviso()).encode(), "1791060506"

    assert bandeja.firma_valida(cuerpo, firmar(cuerpo, marca), marca)


def test_una_firma_de_otro_secreto_se_rechaza(con_bandeja):
    cuerpo, marca = b'{"event":"message_created"}', "1791060506"

    assert not bandeja.firma_valida(cuerpo, firmar(cuerpo, marca, "otro"), marca)


def test_un_cuerpo_cambiado_invalida_la_firma(con_bandeja):
    """Si alcanzara con la firma sin el cuerpo, se podria reescribir el mensaje."""
    marca = "1791060506"
    firma = firmar(b'{"content":"hola"}', marca)

    assert not bandeja.firma_valida(b'{"content":"otra cosa"}', firma, marca)


@pytest.mark.parametrize("firma, marca", [(None, "1"), ("sha256=abc", None), (None, None)])
def test_sin_firma_o_sin_marca_se_rechaza(con_bandeja, firma, marca):
    assert not bandeja.firma_valida(b"{}", firma, marca)


def test_sin_secreto_configurado_se_rechaza_todo(con_bandeja, monkeypatch):
    """Preferible quedarse sin bandeja que aceptar cualquier cosa."""
    monkeypatch.setattr(con_bandeja, "chatwoot_secreto", "", raising=False)
    cuerpo, marca = b"{}", "1"

    assert not bandeja.firma_valida(cuerpo, firmar(cuerpo, marca), marca)


# --- cuando la bandeja no esta ------------------------------------------------

async def test_sin_configurar_no_se_intenta_espejar(settings_de_prueba):
    assert await bandeja.espejar(1, "+593999111222", "hola", del_cliente=True) is None


@respx.mock
async def test_si_la_bandeja_falla_la_conversacion_sigue(respx_mock, con_bandeja, monkeypatch):
    """Perder el espejo es perder visibilidad; que explote seria perder al cliente."""
    async def _sin_referencia(_):
        return None
    monkeypatch.setattr(bandeja, "_referencia", _sin_referencia)
    respx_mock.post("https://bandeja.test/api/v1/accounts/2/contacts").mock(
        return_value=httpx.Response(500, text="la bandeja esta caida"))

    assert await bandeja.espejar(1, "+593999111222", "hola", del_cliente=True) is None
