"""Parseo de updates de Telegram. Sin base ni red."""

import pytest

from app.canales import telegram
from app.canales.base import MensajeEntrante


def update(**cambios):
    base = {
        "update_id": 1,
        "message": {
            "message_id": 42,
            "from": {"id": 7, "is_bot": False, "first_name": "Maria", "last_name": "Paez"},
            "chat": {"id": 7, "type": "private"},
            "text": "buenas, necesito lamina",
        },
    }
    base["message"].update(cambios)
    return base


def test_mensaje_de_texto():
    m = telegram.parsear(update())
    assert isinstance(m, MensajeEntrante)
    assert m.canal == "telegram"
    assert m.identificador == "7"
    assert m.texto == "buenas, necesito lamina"
    assert m.tipo == "texto"
    assert m.nombre == "Maria Paez"
    assert m.telefono is None


def test_el_id_externo_incluye_el_chat():
    """El message_id de Telegram es unico por chat, no globalmente. Sin calificar
    por chat, el mensaje 42 de una persona descartaria el 42 de otra como si
    fuera un duplicado."""
    de_maria = telegram.parsear(update())
    otro = update()
    otro["message"]["chat"] = {"id": 99, "type": "private"}
    otro["message"]["from"] = {"id": 99, "is_bot": False, "first_name": "Jorge"}
    de_jorge = telegram.parsear(otro)

    assert de_maria.id_externo == "telegram:7:42"
    assert de_jorge.id_externo == "telegram:99:42"
    assert de_maria.id_externo != de_jorge.id_externo


def test_foto_con_epigrafe():
    """El caso real: manda la foto de la ventana y escribe las medidas."""
    u = update(photo=[{"file_id": "abc"}], caption="mide 1.20 x 2.40")
    u["message"].pop("text")
    m = telegram.parsear(u)
    assert m.tipo == "imagen"
    assert m.texto == "mide 1.20 x 2.40"


def test_foto_sin_epigrafe():
    u = update(photo=[{"file_id": "abc"}])
    u["message"].pop("text")
    m = telegram.parsear(u)
    assert m.tipo == "imagen"
    assert m.texto == ""


def test_audio_y_ubicacion():
    for clave, esperado in (("voice", "audio"), ("location", "ubicacion")):
        u = update(**{clave: {"x": 1}})
        u["message"].pop("text")
        assert telegram.parsear(u).tipo == esperado


def test_contacto_compartido_trae_telefono():
    """Sobre Telegram el telefono es un dato a relevar; si lo comparte, se toma."""
    u = update(contact={"phone_number": "+593999123456", "first_name": "Maria"})
    u["message"].pop("text")
    m = telegram.parsear(u)
    assert m.telefono == "+593999123456"


def test_username_si_no_hay_nombre():
    u = update()
    u["message"]["from"] = {"id": 7, "is_bot": False, "username": "mpaez"}
    assert telegram.parsear(u).nombre == "mpaez"


@pytest.mark.parametrize(
    "u",
    [
        {"update_id": 1, "edited_message": {"message_id": 1}},
        {"update_id": 1, "callback_query": {"id": "x"}},
        {"update_id": 1},
    ],
)
def test_updates_que_no_son_mensajes(u):
    assert telegram.parsear(u) is None


def test_grupos_se_ignoran():
    """Un grupo no es un lead."""
    u = update()
    u["message"]["chat"] = {"id": -100, "type": "group"}
    assert telegram.parsear(u) is None


def test_mensajes_de_bots_se_ignoran():
    u = update()
    u["message"]["from"] = {"id": 8, "is_bot": True, "first_name": "OtroBot"}
    assert telegram.parsear(u) is None


# --- validacion del secreto -------------------------------------------------

def test_secreto_correcto():
    assert telegram.firma_valida("s3creto", "s3creto") is True


@pytest.mark.parametrize("cabecera", ["otro", "", None])
def test_secreto_incorrecto(cabecera):
    assert telegram.firma_valida("s3creto", cabecera) is False


def test_sin_secreto_configurado_no_se_valida():
    """Aceptable en desarrollo con polling, donde no hay endpoint expuesto."""
    assert telegram.firma_valida("", None) is True
