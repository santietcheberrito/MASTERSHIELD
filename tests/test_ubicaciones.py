"""De que zona de precios es cada ciudad.

Desde el 29/9/2026 MasterShield cotiza por cinco zonas y el minimo de
instalacion va de 5 a 25 m2. Saber que Gualaceo es Azuay y que Azuay es zona
verde es un dato duro, no criterio del modelo: se decide aca.
"""

import pytest

from app import ubicaciones

QUITO = "quito_y_valles"
CERCANA = "pichincha_cercana"
AZUL = "zona_azul"
VERDE = "zona_verde"
ROJA = "zona_roja"


@pytest.mark.parametrize("lugar, zona", [
    # Amarillo: Quito y los valles que el cliente marco, minimo 5 m2
    ("Quito", QUITO),
    ("Cumbayá", QUITO),
    ("Sangolquí", QUITO),
    ("Tumbaco", QUITO),
    # El resto de Pichincha es otra zona, con minimo 10
    ("Cayambe", CERCANA),
    ("Mindo", CERCANA),
    ("Machachi", CERCANA),
    # Azul, verde y roja por ciudad
    ("Otavalo", AZUL),
    ("Ambato", AZUL),
    ("Santo Domingo", AZUL),
    ("Cuenca", VERDE),
    ("Guayaquil", VERDE),
    ("Manta", VERDE),
    ("Loja", ROJA),
    ("Machala", ROJA),
    ("Salinas", ROJA),
    ("Puyo", ROJA),
])
def test_cada_ciudad_cae_en_su_zona(lugar, zona):
    assert ubicaciones.zona_de(lugar) == zona


@pytest.mark.parametrize("provincia, zona", [
    ("Imbabura", AZUL), ("Cotopaxi", AZUL), ("Azuay", VERDE), ("Guayas", VERDE),
    ("Manabí", VERDE), ("El Oro", ROJA), ("Santa Elena", ROJA),
])
def test_tambien_se_reconoce_la_provincia(provincia, zona):
    """La gente dice las dos cosas: "estoy en Azuay" y "es en Gualaceo"."""
    assert ubicaciones.zona_de(provincia) == zona


@pytest.mark.parametrize("escrito", ["cumbaya", "CUMBAYÁ", "Cumbaya", "  cumbayá  "])
def test_da_igual_como_lo_escriban(escrito):
    """Tildes, mayusculas y espacios de sobra: es la misma ciudad."""
    assert ubicaciones.zona_de(escrito) == QUITO


@pytest.mark.parametrize("frase, zona", [
    ("la instalación es en Cuenca", VERDE),
    ("estoy en Manta", VERDE),
    ("necesito para mi casa en Otavalo", AZUL),
    ("es en el norte de Quito", QUITO),
])
def test_encuentra_la_ciudad_dentro_de_una_frase(frase, zona):
    assert ubicaciones.zona_de(frase) == zona


def test_gana_el_nombre_mas_largo():
    """San Miguel de los Bancos es Pichincha y San Miguel es Bolivar: si gana el
    corto, alguien de Pichincha recibe el precio de otra zona."""
    assert ubicaciones.zona_de("San Miguel de los Bancos") == CERCANA
    assert ubicaciones.zona_de("San Miguel") == AZUL


def test_no_confunde_un_nombre_que_esta_dentro_de_otra_palabra():
    """"Mira" es una ciudad de Carchi y aparece dentro de "mirador"."""
    assert ubicaciones.zona_de("Mira") == ROJA
    assert ubicaciones.zona_de("tengo un mirador") is None


def test_galapagos_no_se_atiende():
    zona = ubicaciones.zona_de("Puerto Ayora")
    assert zona == "galapagos"
    assert ubicaciones.se_atiende(zona) is False


@pytest.mark.parametrize("zona, atiende", [
    (QUITO, True), (CERCANA, True), (AZUL, True), (VERDE, True), (ROJA, True),
    ("galapagos", False), ("fuera_del_pais", False), (None, False), ("inventada", False),
])
def test_que_zonas_se_atienden(zona, atiende):
    assert ubicaciones.se_atiende(zona) is atiende


@pytest.mark.parametrize("lugar", ["Bogotá", "Lima", "cualquier cosa", "", None])
def test_lo_que_no_esta_en_la_tabla_no_se_inventa(lugar):
    """Devolver una zona al azar seria darle un precio equivocado: el agente
    tiene que preguntar de nuevo."""
    assert ubicaciones.zona_de(lugar) is None


def test_estan_las_218_ciudades_del_documento():
    """Si alguien borra media tabla, esto se cae."""
    indice = ubicaciones._indice()
    assert len(indice) > 200, "la tabla del documento tiene 218 ciudades mas provincias"
