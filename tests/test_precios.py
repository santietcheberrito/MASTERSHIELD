"""Cotización.

Los números de acá son los que dio el cliente. Si alguien edita
`config/productos.yaml` y se equivoca en un dígito, estos tests se caen — que
es exactamente lo que tienen que hacer, porque el error se descubriría si no
cuando un cliente real reciba un presupuesto que no cierra.
"""

from datetime import date
import pytest

from app import precios
from app.precios import Cotizacion, cotizar, garantias_disponibles

CONTROL_SOLAR = "control_solar_arquitectonico"
PRIVACIDAD = "privacidad_arquitectonica"
SEGURIDAD = "seguridad_arquitectonica"
VEHICULAR = "seguridad_vehicular"

QUITO = "quito_y_valles"
OTRA = "otra_ciudad"
AFUERA = "fuera_del_pais"

# Los precios de lista se prueban con una fecha fuera de la promoción del mes.
# Sin fijarla, estos tests dependerían del calendario y se caerían solos cuando
# venciera el especial, que es ruido y no una falla.
SIN_PROMO = date(2027, 1, 15)


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
    c = cotizar(producto, 20, QUITO, garantia, hoy=SIN_PROMO)
    assert c.puede_cotizar
    assert c.precio_m2 == precio
    assert c.subtotal == precio * 20


def test_los_valles_cuentan_como_quito():
    """Cumbayá, Tumbaco, Los Chillos: precio estándar y mínimo de 5 m². Es una
    sola zona, no hay categoría intermedia."""
    c = cotizar(CONTROL_SOLAR, 6, QUITO, 10)
    assert c.puede_cotizar
    assert c.recargo_m2 == 0
    assert c.minimo_m2 is None


def test_otras_ciudades_pagan_diez_dolares_mas_por_metro():
    quito = cotizar(CONTROL_SOLAR, 25, QUITO, 10, hoy=SIN_PROMO)
    otra = cotizar(CONTROL_SOLAR, 25, OTRA, 10, hoy=SIN_PROMO)
    assert otra.precio_m2 - quito.precio_m2 == 10
    assert otra.subtotal == 52 * 25


def test_el_total_no_lleva_iva_sumado():
    """El cliente pidió que el agente diga los precios con la frase "más IVA",
    no que lo sume. Así el número que sale por chat es el mismo que figura en
    la lista de la empresa."""
    c = cotizar(CONTROL_SOLAR, 10, QUITO, 10, hoy=SIN_PROMO)
    assert c.subtotal == 420
    assert c.subtotal != round(420 * 1.15, 2)


def test_informa_el_descuento_por_pago_de_contado():
    assert cotizar(CONTROL_SOLAR, 10, QUITO, 10).descuento_pago_contado == 10


def test_informa_que_incluye_la_instalacion():
    """Es argumento de venta: no es solo el material."""
    c = cotizar(CONTROL_SOLAR, 10, QUITO, 10, hoy=SIN_PROMO)
    assert any("mano de obra" in i for i in c.incluye)
    assert any("garantia" in i or "posventa" in i for i in c.incluye)


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


def test_seguridad_fuera_de_quito_tambien_paga_el_adicional():
    """El adicional de USD 10 por m² aplica a todos los productos cotizables."""
    c = cotizar(SEGURIDAD, 30, OTRA, 10)
    assert c.puede_cotizar
    assert c.tipo == "desde"
    assert c.precio_m2 == 34
    assert c.subtotal == 34 * 30


# --- vehicular: no se cotiza por chat ---------------------------------------

def test_vehicular_no_se_cotiza_y_pide_los_datos_que_necesita_el_asesor():
    """El cliente fue explícito: acá el agente no da precios, solo releva. El
    m² no es la unidad; el modelo de vehículo define el tipo y el material."""
    c = cotizar(VEHICULAR, None, QUITO, None)
    assert not c.puede_cotizar
    assert c.datos_faltantes == ["modelo_vehiculo"]


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

def test_vencida_la_promocion_vuelve_el_precio_normal():
    """Quedarse corto y que el asesor tenga que subir el número después es peor
    que arrancar arriba y poder mejorarlo."""
    c = cotizar(CONTROL_SOLAR, 20, QUITO, 10, hoy=SIN_PROMO)
    assert c.precio_m2 == 42


def test_dentro_del_mes_la_estimacion_usa_el_precio_especial():
    """La estimación es para el vendedor, pero tiene que contar la misma
    historia que la conversación: si el agente informó 37, el CRM no puede
    decir 42."""
    c = cotizar(CONTROL_SOLAR, 20, QUITO, 10, hoy=date(2026, 9, 15))
    assert c.precio_m2 == 37


def test_la_cotizacion_redondea_a_centavos():
    c = cotizar(CONTROL_SOLAR, 7.33, QUITO, 10, hoy=SIN_PROMO)
    assert c.subtotal == round(42 * 7.33, 2)
    assert isinstance(c, Cotizacion)


# --- la promoción del mes ---------------------------------------------------

def test_el_precio_especial_rige_dentro_del_mes():
    cfg = {"vigencia_precio_especial": "2026-09"}
    assert precios._especial_vigente(cfg, date(2026, 9, 30)) is True


def test_el_precio_especial_no_sobrevive_al_mes():
    """Un agente prometiendo en octubre el precio de septiembre deja a la
    empresa teniendo que sostenerlo o desdecirse delante del cliente."""
    cfg = {"vigencia_precio_especial": "2026-09"}
    assert precios._especial_vigente(cfg, date(2026, 10, 1)) is False


def test_sin_vigencia_declarada_no_hay_promocion():
    """El comportamiento seguro es el precio normal, que es el más alto:
    quedarse corto y que el asesor tenga que subir el número es peor."""
    assert precios._especial_vigente({}, date(2026, 9, 15)) is False
    assert precios._especial_vigente({"vigencia_precio_especial": None}) is False


# --- el recargo de provincias va dentro del precio --------------------------

def test_en_provincias_el_precio_por_metro_ya_trae_el_recargo():
    """Devolverlo aparte obligaba a sumarlo a quien leyera la respuesta, y el
    agente tiene prohibido hacer cuentas: le dijo 37 a un cliente de Guayaquil
    cuando son 47, y el control de precios lo dejó pasar porque 37 sí venía de
    la herramienta."""
    quito = precios.informar_precios(CONTROL_SOLAR, QUITO, hoy=SIN_PROMO)
    otra = precios.informar_precios(CONTROL_SOLAR, OTRA, hoy=SIN_PROMO)

    de_quito = {c.garantia_anios: c.precio_normal for c in quito.calidades}
    de_otra = {c.garantia_anios: c.precio_normal for c in otra.calidades}

    assert de_quito == {10: 42, 5: 32}
    assert de_otra == {10: 52, 5: 42}, "los 10 dólares de recargo ya están adentro"


def test_el_recargo_tambien_entra_en_el_precio_de_promocion():
    otra = precios.informar_precios(CONTROL_SOLAR, OTRA, hoy=date(2026, 9, 15))
    especiales = {c.garantia_anios: c.precio_especial for c in otra.calidades}
    assert especiales == {10: 47, 5: 35}


def test_el_minimo_de_provincias_viaja_con_el_precio():
    """Es cuatro veces el de Quito y decide si la persona es cliente."""
    otra = precios.informar_precios(CONTROL_SOLAR, OTRA)
    assert otra.minimo_m2 == 20
    assert precios.informar_precios(CONTROL_SOLAR, QUITO).minimo_m2 == 5
