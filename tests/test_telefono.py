"""Normalización de teléfonos."""

import pytest

from app.telefono import normalizar


@pytest.mark.parametrize(
    "crudo,esperado",
    [
        # Celular ecuatoriano, en todas las formas en que lo escribe la gente.
        ("0999123456", "+593999123456"),
        ("999123456", "+593999123456"),
        ("+593999123456", "+593999123456"),
        ("593999123456", "+593999123456"),
        ("00593999123456", "+593999123456"),
        ("099 912 3456", "+593999123456"),
        ("099-912-3456", "+593999123456"),
        ("(099) 9123456", "+593999123456"),
        # Fijo de Quito.
        ("022345678", "+59322345678"),
        ("22345678", "+59322345678"),
    ],
)
def test_numeros_ecuatorianos(crudo, esperado):
    assert normalizar(crudo) == esperado


def test_un_numero_extranjero_con_codigo_se_respeta():
    """Si trae código de país, es de quien dice ser."""
    assert normalizar("+5491160074604") == "+5491160074604"


@pytest.mark.parametrize("crudo", ["1160074604", "12345", "no tengo", "", None, "0000"])
def test_lo_que_no_se_puede_afirmar_queda_sin_normalizar(crudo):
    """Un teléfono mal normalizado es peor que uno sin normalizar: el vendedor
    llama a otro lado. Ante la duda, None, y el crudo queda en `datos`."""
    assert normalizar(crudo) is None


def test_el_prefijo_define_el_codigo_que_se_antepone():
    """Las reglas de largo siguen siendo las de Ecuador: el prefijo cambia el
    codigo de salida, no convierte al modulo en generico."""
    assert normalizar("999123456", prefijo="+54") == "+54999123456"
    assert normalizar("+5491160074604", prefijo="+54") == "+5491160074604"


@pytest.mark.parametrize("crudo", ["0000", "+00", "001", "+123"])
def test_basura_que_arranca_como_internacional(crudo):
    """Sin el chequeo de largo, "0000" salia como "+00"."""
    assert normalizar(crudo) is None
