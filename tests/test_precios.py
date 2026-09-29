"""Cotización.

Los números de acá son los que dio el cliente. Si alguien edita
`config/productos.yaml` y se equivoca en un dígito, estos tests se caen — que
es exactamente lo que tienen que hacer, porque el error se descubriría si no
cuando un cliente real reciba un presupuesto que no cierra.
"""

import copy
from datetime import date
import pytest

from app import precios
from app.precios import Cotizacion, cotizar, garantias_disponibles

CONTROL_SOLAR = "control_solar_ventanas"
PRIVACIDAD = "privacidad_arquitectonica"
SEGURIDAD = "seguridad_arquitectonica"
VEHICULAR = "seguridad_vehicular"

# Las cinco zonas de venta del documento "UBICACIONES Y PRECIOS MS 2027", que
# el 29/9/2026 reemplazaron a "Quito o no Quito" con recargo plano.
QUITO = "quito_y_valles"        # minimo 5 m2
CERCANA = "pichincha_cercana"   # minimo 10 m2
AZUL = "zona_azul"              # minimo 15 m2
VERDE = "zona_verde"            # minimo 20 m2
ROJA = "zona_roja"              # minimo 25 m2
GALAPAGOS = "galapagos"
AFUERA = "fuera_del_pais"

# Los precios de lista se prueban con una fecha fuera de la promoción del mes.
# Sin fijarla, estos tests dependerían del calendario y se caerían solos cuando
# venciera el especial, que es ruido y no una falla.
SIN_PROMO = date(2027, 1, 15)


# --- qué producto corresponde -----------------------------------------------

@pytest.mark.parametrize(
    "datos,producto",
    [
        ({"linea": "vehicular"}, VEHICULAR),
        ({"linea": "arquitectonico", "objetivo": "privacidad"}, PRIVACIDAD),
        ({"linea": "arquitectonico", "objetivo": "seguridad"}, SEGURIDAD),
        ({"linea": "arquitectonico", "objetivo": "control_solar", "superficie": "ventanas"},
         CONTROL_SOLAR),
        ({"linea": "arquitectonico", "objetivo": "control_solar", "superficie": "techo"},
         "control_solar_techos"),
        # Control solar son dos productos: sin la superficie no hay uno solo.
        ({"linea": "arquitectonico", "objetivo": "control_solar"}, None),
        ({"linea": "arquitectonico"}, None),
        ({}, None),
    ],
)
def test_producto_para(datos, producto):
    assert precios.producto_para(datos) == producto


def test_techos_cuesta_lo_mismo_que_ventanas():
    """Decisión del cliente. En el YAML es un alias, así que es el mismo dato y
    no una copia que se pueda desactualizar."""
    techos = precios.informar_precios("control_solar_techos", QUITO, hoy=SIN_PROMO)
    ventanas = precios.informar_precios(CONTROL_SOLAR, QUITO, hoy=SIN_PROMO)
    assert techos.puede_informar is True
    assert techos.calidades == ventanas.calidades


@pytest.mark.parametrize(
    "datos,producto",
    [
        # Cuestan lo mismo: para el precio no hace falta saber la superficie.
        ({"linea": "arquitectonico", "objetivo": "control_solar"}, CONTROL_SOLAR),
        ({"linea": "arquitectonico", "objetivo": "control_solar", "superficie": "techo"},
         "control_solar_techos"),
        ({"linea": "arquitectonico", "objetivo": "privacidad"}, PRIVACIDAD),
        # Sin objetivo quedan productos con precios distintos.
        ({"linea": "arquitectonico"}, None),
        ({}, None),
    ],
)
def test_producto_para_cotizar(datos, producto):
    assert precios.producto_para_cotizar(datos) == producto


def test_si_ventanas_y_techos_dejan_de_costar_lo_mismo_se_vuelve_a_pedir_la_superficie(
    monkeypatch,
):
    cfg = copy.deepcopy(precios.configuracion())
    # Techos pasa a comer de su propia tabla, con otros valores.
    techos = next(p for p in cfg["productos"] if p["id"] == "control_solar_techos")
    techos["tabla_de_precios"] = "control_solar_techos"
    for zona in cfg["zonas"].values():
        if zona.get("precios"):
            zona["precios"]["control_solar_techos"] = [
                {"garantia_anios": 10, "precio_normal": 50, "precio_especial": 45}]
    monkeypatch.setattr(precios, "configuracion", lambda ruta=None: cfg)

    assert precios.producto_para_cotizar(
        {"linea": "arquitectonico", "objetivo": "control_solar"}) is None


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
    """Cumbayá, Tumbaco, Los Chillos: el mínimo más bajo, 5 m². El resto de
    Pichincha es otra zona, con mínimo 10."""
    c = cotizar(CONTROL_SOLAR, 6, QUITO, 10)
    assert c.puede_cotizar, "6 m² pasa el mínimo de 5"
    assert precios.informar_precios(CONTROL_SOLAR, QUITO).minimo_m2 == 5
    # El resto de Pichincha es otra zona: el mismo pedido no alcanzaría.
    assert not cotizar(CONTROL_SOLAR, 6, CERCANA, 10).puede_cotizar


def test_cada_zona_tiene_su_propio_precio():
    """Hasta el 29/9/2026 provincia era Quito más un recargo plano de 10. Ahora
    cada zona trae su tabla y no hay formula: en verde la calidad de 10 años
    cuesta 55, no 52."""
    quito = cotizar(CONTROL_SOLAR, 25, QUITO, 10, hoy=SIN_PROMO)
    verde = cotizar(CONTROL_SOLAR, 25, VERDE, 10, hoy=SIN_PROMO)
    assert quito.precio_m2 == 42
    assert verde.precio_m2 == 55
    assert verde.subtotal == 55 * 25


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


def test_el_minimo_sube_con_la_distancia():
    """Cuatro veces el de Quito: es el filtro de calificación más duro que
    tiene el negocio."""
    c = cotizar(CONTROL_SOLAR, 12, VERDE, 10)
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


def test_seguridad_tambien_cambia_por_zona():
    """El adicional de USD 10 por m² aplica a todos los productos cotizables."""
    c = cotizar(SEGURIDAD, 30, VERDE, 10)
    assert c.puede_cotizar
    assert c.tipo == "desde"
    assert c.precio_m2 == 35, "seguridad en verde: 35, no 24 + recargo"
    assert c.subtotal == 35 * 30


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

def test_el_precio_por_metro_sale_de_la_zona():
    """Devolverlo aparte obligaba a sumarlo a quien leyera la respuesta, y el
    agente tiene prohibido hacer cuentas: le dijo 37 a un cliente de Guayaquil
    cuando son 47, y el control de precios lo dejó pasar porque 37 sí venía de
    la herramienta."""
    quito = precios.informar_precios(CONTROL_SOLAR, QUITO, hoy=SIN_PROMO)
    otra = precios.informar_precios(CONTROL_SOLAR, VERDE, hoy=SIN_PROMO)

    de_quito = {c.garantia_anios: c.precio_normal for c in quito.calidades}
    de_otra = {c.garantia_anios: c.precio_normal for c in otra.calidades}

    assert de_quito == {10: 42, 5: 32}
    assert de_otra == {10: 55, 5: 45}, "los precios de verde, sin formula que los derive"


def test_la_promocion_tambien_es_por_zona():
    otra = precios.informar_precios(CONTROL_SOLAR, VERDE, hoy=date(2026, 9, 15))
    especiales = {c.garantia_anios: c.precio_especial for c in otra.calidades}
    assert especiales == {10: 49, 5: 39}


def test_el_minimo_de_la_zona_viaja_con_el_precio():
    """Es cuatro veces el de Quito y decide si la persona es cliente."""
    otra = precios.informar_precios(CONTROL_SOLAR, VERDE)
    assert otra.minimo_m2 == 20
    assert precios.informar_precios(CONTROL_SOLAR, QUITO).minimo_m2 == 5
