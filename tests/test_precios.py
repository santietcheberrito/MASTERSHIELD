"""Cotización.

Los números de acá son los que dio el cliente. Si alguien edita
`config/productos.yaml` y se equivoca en un dígito, estos tests se caen — que
es exactamente lo que tienen que hacer, porque el error se descubriría si no
cuando un cliente real reciba un presupuesto que no cierra.
"""

import pytest

from app.precios import Cotizacion, cotizar, garantias_disponibles

CONTROL_SOLAR = "control_solar_arquitectonico"
PRIVACIDAD = "privacidad_arquitectonica"
SEGURIDAD = "seguridad_arquitectonica"
VEHICULAR = "seguridad_vehicular"

QUITO = "quito_y_valles"
OTRA = "otra_ciudad"
AFUERA = "fuera_del_pais"


# --- precios de lista -------------------------------------------------------

@pytest.mark.parametrize(
    "producto,garantia,precio",
    [
        (CONTROL_SOLAR, 10, 42),
        (CONTROL_SOLAR, 5, 32),
        (PRIVACIDAD, 10, 42),
        (PRIVACIDAD, 5, 32),
    ],
)
def test_precio_de_lista_en_quito(producto, garantia, precio):
    c = cotizar(producto, 20, QUITO, garantia)
    assert c.puede_cotizar
    assert c.precio_m2 == precio
    assert c.subtotal == precio * 20


def test_otras_ciudades_pagan_diez_dolares_mas_por_metro():
    quito = cotizar(CONTROL_SOLAR, 25, QUITO, 10)
    otra = cotizar(CONTROL_SOLAR, 25, OTRA, 10)
    assert otra.precio_m2 - quito.precio_m2 == 10
    assert otra.subtotal == 52 * 25


def test_el_total_no_lleva_iva_sumado():
    """El cliente pidió que el agente diga los precios con la frase "más IVA",
    no que lo sume. Así el número que sale por chat es el mismo que figura en
    la lista de la empresa."""
    c = cotizar(CONTROL_SOLAR, 10, QUITO, 10)
    assert c.subtotal == 420
    assert c.subtotal != round(420 * 1.15, 2)


def test_informa_el_descuento_por_pago_de_contado():
    assert cotizar(CONTROL_SOLAR, 10, QUITO, 10).descuento_pago_contado == 10


def test_informa_que_incluye_la_instalacion():
    """Es argumento de venta: no es solo el material."""
    c = cotizar(CONTROL_SOLAR, 10, QUITO, 10)
    assert any("mano de obra" in i for i in c.incluye)
    assert any("escaleras" in i or "andamios" in i for i in c.incluye)


# --- mínimos de venta -------------------------------------------------------

def test_minimo_de_cinco_metros_en_quito():
    c = cotizar(CONTROL_SOLAR, 4, QUITO, 10)
    assert not c.puede_cotizar
    assert c.minimo_m2 == 5


def test_justo_en_el_minimo_se_puede():
    assert cotizar(CONTROL_SOLAR, 5, QUITO, 10).puede_cotizar


def test_minimo_de_veinte_metros_fuera_de_quito():
    """Cuatro veces el de Quito: es el filtro de calificación más duro que
    tiene el negocio."""
    c = cotizar(CONTROL_SOLAR, 12, OTRA, 10)
    assert not c.puede_cotizar
    assert c.minimo_m2 == 20
    # Y los mismos 12 m² en Quito sí se pueden.
    assert cotizar(CONTROL_SOLAR, 12, QUITO, 10).puede_cotizar


def test_el_minimo_no_lo_decide_el_modelo():
    """Por más que el pedido sea de 4.9 m², la respuesta es la misma. El agente
    no puede hacer una excepción porque el cliente insista."""
    assert not cotizar(CONTROL_SOLAR, 4.9, QUITO, 10).puede_cotizar


# --- fuera de Ecuador -------------------------------------------------------

def test_fuera_del_pais_es_descarte():
    c = cotizar(CONTROL_SOLAR, 100, AFUERA, 10)
    assert not c.puede_cotizar
    assert "no se atiende" in c.motivo


# --- seguridad arquitectónica: piso, no total -------------------------------

def test_seguridad_arquitectonica_da_un_desde():
    """Hay distintos niveles de seguridad y el grosor lo sugiere un asesor. El
    agente da un piso, nunca un total cerrado."""
    c = cotizar(SEGURIDAD, 30, QUITO, 10)
    assert c.puede_cotizar
    assert c.tipo == "desde"
    assert c.precio_m2 == 24


def test_seguridad_en_otra_ciudad_no_se_cotiza_todavia():
    """No está confirmado si el recargo de USD 10 aplica también a seguridad.
    Mientras no se sepa, no se inventa el número."""
    c = cotizar(SEGURIDAD, 30, OTRA, 10)
    assert not c.puede_cotizar
    assert "recargo" in c.motivo


# --- vehicular: no se cotiza por chat ---------------------------------------

def test_vehicular_no_se_cotiza_y_pide_los_datos_que_necesita_el_asesor():
    """El cliente fue explícito: acá el agente no da precios, solo releva. El
    m² no es la unidad; el modelo de vehículo define el tipo y el material."""
    c = cotizar(VEHICULAR, None, QUITO, None)
    assert not c.puede_cotizar
    assert c.datos_faltantes == ["modelo_vehiculo", "nivel_seguridad"]


def test_vehicular_no_cotiza_ni_con_metros():
    assert not cotizar(VEHICULAR, 50, QUITO, 10).puede_cotizar


# --- qué falta relevar ------------------------------------------------------

def test_sin_zona_no_hay_precio():
    """La zona define mínimo y recargo, así que el agente tiene que preguntar
    dónde está el cliente antes de hablar de plata."""
    c = cotizar(CONTROL_SOLAR, 20, None, 10)
    assert not c.puede_cotizar
    assert c.datos_faltantes == ["zona"]


def test_sin_metros_no_hay_precio():
    c = cotizar(CONTROL_SOLAR, None, QUITO, 10)
    assert not c.puede_cotizar
    assert c.datos_faltantes == ["medidas"]


def test_con_dos_garantias_hay_que_elegir():
    """Control solar y privacidad vienen en 10 y 5 años, a precios distintos."""
    c = cotizar(CONTROL_SOLAR, 20, QUITO, None)
    assert not c.puede_cotizar
    assert c.datos_faltantes == ["garantia_anios"]
    assert sorted(garantias_disponibles(CONTROL_SOLAR)) == [5, 10]


def test_con_una_sola_garantia_no_hace_falta_preguntar():
    assert garantias_disponibles(SEGURIDAD) == [10]
    assert cotizar(SEGURIDAD, 30, QUITO, None).puede_cotizar


def test_producto_inexistente():
    assert not cotizar("lamina_antibalas", 20, QUITO, 10).puede_cotizar


def test_zona_inexistente():
    assert not cotizar(CONTROL_SOLAR, 20, "galapagos", 10).puede_cotizar


# --- mientras falte el criterio del precio especial -------------------------

def test_usa_el_precio_normal_mientras_no_se_sepa_cuando_va_el_especial():
    """Quedarse corto y que el asesor tenga que subir el número después es peor
    que arrancar arriba y poder mejorarlo."""
    c = cotizar(CONTROL_SOLAR, 20, QUITO, 10)
    assert c.precio_m2 == 42, "el especial es 37; no se usa hasta saber cuándo aplica"


def test_la_cotizacion_redondea_a_centavos():
    c = cotizar(CONTROL_SOLAR, 7.33, QUITO, 10)
    assert c.subtotal == round(42 * 7.33, 2)
    assert isinstance(c, Cotizacion)
