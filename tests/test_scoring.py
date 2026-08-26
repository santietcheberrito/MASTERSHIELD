"""Puntaje de calificación.

Los pesos son provisorios y salen del kick off, así que estos tests no fijan
números: fijan el **orden**. Una constructora con un trabajo grande y urgente
tiene que puntuar más que un particular explorando, valgan lo que valgan los
pesos del día. Cuando lleguen los criterios reales del cliente, los pesos
cambian y esto tiene que seguir pasando.
"""

import pytest

from app import scoring


def datos(**extra):
    base = {"linea": "arquitectonico", "objetivo": "control_solar", "zona": "quito_y_valles"}
    base.update(extra)
    return base


# --- reglas duras: no se negocian con puntos --------------------------------

def test_fuera_de_ecuador_se_descarta_por_grande_que_sea():
    p = scoring.puntuar(datos(zona="fuera_del_pais", metros_cuadrados=500,
                              urgencia="inmediato", tipo_cliente="constructora_o_arquitecto"))
    assert p.etapa == scoring.FUERA
    assert p.score == 0
    assert "fuera de Ecuador" in p.motivo


def test_bajo_el_minimo_de_quito_no_se_puede_vender():
    p = scoring.puntuar(datos(metros_cuadrados=4, urgencia="inmediato"))
    assert p.etapa == scoring.BAJO_MINIMO
    assert "5 m²" in p.motivo


def test_el_minimo_de_otras_ciudades_es_cuatro_veces_mas_alto():
    """Los mismos 12 m² se venden en Quito y no en Ambato."""
    assert scoring.puntuar(datos(metros_cuadrados=12)).etapa != scoring.BAJO_MINIMO
    assert scoring.puntuar(
        datos(zona="otra_ciudad", metros_cuadrados=12)
    ).etapa == scoring.BAJO_MINIMO


def test_vehicular_no_tiene_minimo_de_metros():
    """Ahí se cotiza por vehículo: el m² no es la unidad."""
    p = scoring.puntuar({"linea": "vehicular", "zona": "quito_y_valles",
                         "modelo_vehiculo": "Hilux 2020"})
    assert p.etapa != scoring.BAJO_MINIMO


# --- orden relativo ---------------------------------------------------------

def test_una_constructora_urgente_puntua_mas_que_un_particular_explorando():
    grande = scoring.puntuar(datos(metros_cuadrados=60, urgencia="inmediato",
                                   tipo_cliente="constructora_o_arquitecto",
                                   disponibilidad="jueves"))
    chico = scoring.puntuar(datos(metros_cuadrados=6, urgencia="explorando",
                                  tipo_cliente="particular"))
    assert grande.score > chico.score
    assert grande.etapa == scoring.ALTA
    assert chico.etapa == scoring.BAJA


@pytest.mark.parametrize("campo,mayor,menor", [
    ("urgencia", "inmediato", "explorando"),
    ("tipo_cliente", "constructora_o_arquitecto", "particular"),
    ("zona", "quito_y_valles", "otra_ciudad"),
])
def test_cada_eje_ordena_como_corresponde(campo, mayor, menor):
    base = {"metros_cuadrados": 25, "urgencia": "semanas",
            "tipo_cliente": "particular", "zona": "quito_y_valles",
            "linea": "arquitectonico", "objetivo": "control_solar"}
    assert scoring.puntuar({**base, campo: mayor}).score > \
           scoring.puntuar({**base, campo: menor}).score


def test_mas_metros_valen_mas():
    puntajes = [scoring.puntuar(datos(metros_cuadrados=m)).score for m in (6, 15, 25, 60)]
    assert puntajes == sorted(puntajes)
    assert len(set(puntajes)) > 1


def test_decir_cuando_puede_suma():
    """Es señal de intención y además es lo que el vendedor necesita."""
    sin = scoring.puntuar(datos(metros_cuadrados=25))
    con = scoring.puntuar(datos(metros_cuadrados=25, disponibilidad="el jueves"))
    assert con.score > sin.score


# --- forma del resultado ----------------------------------------------------

def test_el_score_esta_acotado():
    p = scoring.puntuar(datos(metros_cuadrados=1000, urgencia="inmediato",
                              tipo_cliente="constructora_o_arquitecto",
                              disponibilidad="cuando sea"))
    assert 0 <= p.score <= 100


def test_el_desglose_explica_el_numero():
    """Se guarda y se manda a Kommo en la nota: el vendedor tiene que poder ver
    por qué el sistema puntuó así."""
    p = scoring.puntuar(datos(metros_cuadrados=25, urgencia="inmediato",
                              tipo_cliente="empresa"))
    assert sum(d["puntos"] for d in p.desglose) == p.score
    campos = {d["campo"] for d in p.desglose}
    assert {"zona", "urgencia", "tipo_cliente", "metros_cuadrados"} <= campos


def test_una_consulta_vacia_no_explota():
    p = scoring.puntuar({})
    assert p.score == 0
    assert p.etapa == scoring.BAJA


def test_las_tres_clasificaciones_existen():
    umbrales = scoring.calificacion()["umbrales"]
    assert umbrales["alta"] > umbrales["media"] > 0
