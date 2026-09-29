"""Partido de mensajes y demoras.

Lo que se verifica acá es que la conversación no se lea como un bot: mensajes
cortos, en varios globos, con pausas que se parecen a alguien tipeando.
"""

import pytest

from app import humanizacion


# --- partido ----------------------------------------------------------------

def test_una_frase_corta_es_un_solo_mensaje():
    assert humanizacion.partir("Buenas tardes, ¿en qué le ayudo?") == [
        "Buenas tardes, ¿en qué le ayudo?"
    ]


def test_la_respuesta_y_la_repregunta_van_separadas():
    """El modelo separa con salto de línea lo que son ideas distintas, y ese es
    el corte natural: una persona manda dos globos, no un párrafo."""
    partes = humanizacion.partir(
        "No, el laminado no reduce el ruido exterior.\n¿En qué ciudad está?"
    )
    assert len(partes) == 2
    assert partes[1] == "¿En qué ciudad está?"


def test_un_parrafo_largo_se_corta_por_oraciones():
    largo = (
        "Con 25 m² le queda en 1050 dólares más IVA, con el material de 10 años. "
        "El precio incluye material, instalación, traslado, andamios si hacen falta "
        "y la limpieza previa de los vidrios. "
        "¿Me facilita un teléfono de contacto para que un asesor MS lo llame?"
    )
    partes = humanizacion.partir(largo)
    assert len(partes) > 1
    assert all(len(p) <= humanizacion.LARGO_COMODO + 80 for p in partes)
    # No se pierde nada por el camino.
    assert "1050 dólares más IVA" in " ".join(partes)
    assert "teléfono de contacto" in " ".join(partes)


def test_nunca_mas_de_tres_globos():
    """Tres mensajes seguidos ya es mucho; más cansa."""
    texto = "\n".join(f"Linea numero {n}." for n in range(1, 9))
    partes = humanizacion.partir(texto)
    assert len(partes) == humanizacion.MAX_PARTES
    assert "Linea numero 8." in partes[-1], "lo que sobra se pega al último"


def test_no_quedan_mensajes_vacios():
    partes = humanizacion.partir("Primera.\n\n\nSegunda.")
    assert partes == ["Primera.", "Segunda."]


@pytest.mark.parametrize("texto", ["", "   ", None])
def test_sin_texto_no_se_manda_nada(texto):
    assert humanizacion.partir(texto) == []


# --- demoras ----------------------------------------------------------------

def test_la_pausa_crece_con_el_largo():
    corto = humanizacion.demora_de_escritura("Sí.")
    largo = humanizacion.demora_de_escritura("x" * 200)
    assert corto < largo


@pytest.mark.parametrize("texto", ["a", "x" * 50, "x" * 5000])
def test_la_pausa_esta_acotada(texto):
    """Ni instantáneo ni una eternidad."""
    pausa = humanizacion.demora_de_escritura(texto)
    assert humanizacion.PAUSA_MIN <= pausa <= humanizacion.PAUSA_MAX


def test_la_demora_de_respuesta_cae_en_el_rango():
    for _ in range(50):
        assert 60 <= humanizacion.demora_de_respuesta(60, 120) <= 120


def test_la_demora_de_respuesta_varia():
    """Un retraso fijo de 90 segundos es tan detectable como contestar al
    instante: es un patrón."""
    sorteos = {humanizacion.demora_de_respuesta(60, 120) for _ in range(40)}
    assert len(sorteos) > 5


def test_las_pausas_entre_mensajes_varian():
    pausas = {round(humanizacion.demora_de_escritura("x" * 100), 3) for _ in range(30)}
    assert len(pausas) > 5


def test_la_pausa_entre_mensajes_da_tiempo_a_que_se_vea_escribiendo():
    """Con 1.5 segundos, Meta no alcanzaba a propagar el indicador y el segundo
    y el tercer mensaje aparecían de la nada. Desde el 14/9 el piso es 2.5: menos
    el respiro antes de encenderlo, queda más de un segundo visible."""
    from app import worker

    for _ in range(50):
        pausa = humanizacion.demora_de_escritura("¿Con quién tengo el gusto?")
        assert pausa >= 2.5
        assert pausa - worker.RESPIRO_ANTES_DEL_INDICADOR >= 1.3


def test_la_pausa_no_pasa_de_cuatro_segundos():
    """Con el modelo tardando 15 a 25 segundos, 7 más por globo hacía la
    conversación lenta."""
    assert humanizacion.demora_de_escritura("x" * 5000) <= 4.0


def test_el_piso_de_la_pausa_no_es_siempre_el_mismo():
    """Un mensaje corto siempre toca el piso. Si fuera fijo, cada saludo saldría
    exactamente a los tres segundos, que es tan detectable como no esperar."""
    medidas = {round(humanizacion.demora_de_escritura("dale"), 2) for _ in range(50)}
    assert len(medidas) > 5, "el piso se sortea, no es una constante"
