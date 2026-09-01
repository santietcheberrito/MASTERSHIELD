"""Verificación del precio antes de enviar.

El precio lo decide Python, pero el mensaje lo escribe el modelo. Este es el
último control: si el número del mensaje no es el que calculó la herramienta,
no sale.
"""

import pytest

from app import verificacion


# --- qué cuenta como plata --------------------------------------------------

@pytest.mark.parametrize("texto,esperado", [
    ("Le queda en 1050 dólares más IVA.", [1050.0]),
    ("Son $840 más IVA.", [840.0]),
    ("Serían 1.050 dólares.", [1050.0]),
    ("1050 USD más IVA", [1050.0]),
])
def test_reconoce_los_montos(texto, esperado):
    assert verificacion.montos_mencionados(texto) == esperado


@pytest.mark.parametrize("texto", [
    "Con 25 m² y garantía de 10 años.",
    "Pagando en efectivo hay un 10% de descuento adicional.",
    "El mínimo es de 5 m² en Quito y 20 en el resto del país.",
    "El rollo viene en 1.82 y 1.52 metros de ancho.",
    "Un asesor MS lo llama hoy alrededor de las 17h00 al +593987654321.",
])
def test_no_confunde_metros_años_porcentajes_ni_telefonos(texto):
    """Estos números aparecen todo el tiempo y son legítimos."""
    assert verificacion.montos_mencionados(texto) == []


# --- la verificación --------------------------------------------------------

def test_el_precio_calculado_pasa():
    ok, _ = verificacion.verificar(
        "Con 25 m² le queda en 1050 dólares más IVA.", {1050.0, 42.0}
    )
    assert ok


def test_un_precio_distinto_al_calculado_no_pasa():
    ok, motivo = verificacion.verificar(
        "Con 25 m² le queda en 800 dólares más IVA.", {1050.0, 42.0}
    )
    assert not ok
    assert "800" in motivo


def test_un_descuento_inventado_no_pasa():
    """El caso que importa: convencer al modelo de dar un precio que no existe."""
    ok, motivo = verificacion.verificar(
        "Le hago un precio especial de $300 más IVA.", {1050.0, 42.0}
    )
    assert not ok


def test_no_se_puede_decir_un_precio_sin_haberlo_calculado():
    """Repetir un precio de memoria no vale: hay que volver a calcularlo."""
    ok, motivo = verificacion.verificar("Como le decía, son 1050 dólares.", set())
    assert not ok
    assert "sin haber calculado" in motivo


def test_un_mensaje_sin_plata_siempre_pasa():
    ok, _ = verificacion.verificar("¿En qué ciudad está la oficina?", set())
    assert ok


def test_el_precio_por_metro_tambien_esta_autorizado():
    ok, _ = verificacion.verificar(
        "Son 42 dólares por m², o sea 1050 dólares más IVA en total.", {1050.0, 42.0}
    )
    assert ok


def test_el_mensaje_de_reemplazo_no_dice_ningun_numero():
    assert verificacion.montos_mencionados(verificacion.MENSAJE_SEGURO) == []
