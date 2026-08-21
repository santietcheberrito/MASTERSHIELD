"""El conocimiento destilado no puede perder guardarraíles.

`prompts/conocimiento.md` es la única fuente de verdad técnica del agente y se
va a editar a mano: cuando el cliente confirme un dato, cuando cambie una
garantía, cuando alguien lo "resuma un poco más". Estos tests son la red que
impide que en una de esas ediciones se caiga una negación.

Una negación perdida no rompe nada visible: el agente simplemente empieza a
afirmar, con total seguridad, algo falso sobre el producto.
"""

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
CONOCIMIENTO = RAIZ / "prompts" / "conocimiento.md"
FUENTE = RAIZ / "docs" / "preguntas-frecuentes-2025.txt"


@pytest.fixture(scope="module")
def texto() -> str:
    return CONOCIMIENTO.read_text(encoding="utf-8").lower()


def test_el_archivo_existe():
    assert CONOCIMIENTO.exists()


def test_la_fuente_queda_versionada():
    """La destilación tiene que ser rastreable hasta el documento del cliente."""
    assert FUENTE.exists()
    assert "MasterShield" in FUENTE.read_text(encoding="utf-8")


# Cada guardarraíl con las palabras que tienen que seguir estando. El agente
# nunca debe afirmar estas cosas, y son las que un modelo suelto afirmaría
# porque son ciertas para otros productos del rubro.
GUARDARRAILES = {
    "no rechaza el frío": ["frío"],
    "no enfría el ambiente": ["enfría"],
    "no reduce el ruido": ["ruido"],
    "no evita la condensación": ["empañ"],
    "no es antibalas ni blindaje": ["antibalas", "blindaje", "anti motín"],
    "no oscurece los espacios": ["oscurece"],
}


@pytest.mark.parametrize("guardarrail,palabras", GUARDARRAILES.items())
def test_el_guardarrail_sigue_estando(texto, guardarrail, palabras):
    for palabra in palabras:
        assert palabra in texto, f"se perdió el guardarraíl: {guardarrail}"


def test_la_privacidad_pierde_el_efecto_de_noche(texto):
    """Es el límite que más reclamos genera si no se avisa antes de vender."""
    assert "18h00" in texto
    assert "noche" in texto


def test_la_instalacion_exterior_dura_menos(texto):
    """Roof Shield en pérgolas: 1 a 3 años contra 15, 10 o 5 de la interna."""
    assert "roof shield" in texto
    assert "1 a 3 años" in texto


def test_no_se_mezclan_las_lineas(texto):
    """El material vehicular no va sobre vidrio arquitectónico, pero la línea
    vehicular sí es negocio propio y no se rechaza."""
    assert "vehicular" in texto
    assert "arquitectónico" in texto


def test_estan_las_marcas_que_distribuyen(texto):
    for marca in ("hüper optik", "midas films", "conco", "madico", "3m"):
        assert marca in texto


def test_estan_los_tres_plazos_de_garantia(texto):
    assert "15, 10 y 5 años" in texto or "15, 10 o 5 años" in texto


def test_la_visita_es_gratis_en_quito(texto):
    assert "quito" in texto
    assert "no tiene costo" in texto


def test_declara_lo_que_no_sabe(texto):
    """La lista de lo que se deriva a un asesor es tan importante como el resto:
    es lo que impide que el agente rellene huecos."""
    for tema in ("rotura térmica", "formas de pago", "montos mínimos"):
        assert tema in texto


def test_no_inventa_precios(texto):
    """El documento del cliente no trae precios y la destilación tampoco puede.
    Si algún día aparece un número de plata acá, es porque alguien lo inventó."""
    assert "$" not in texto
    assert not re.search(r"\busd\b", texto)
    assert "precio por m" not in texto


def test_avisa_que_falta_la_revision_del_cliente(texto):
    """Mientras nadie de MasterShield lo valide, tiene que estar dicho."""
    assert "pendiente" in texto
