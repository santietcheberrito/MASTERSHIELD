"""Logging estructurado.

Lo que se testea no es el formato, es el contexto: que el id de la conversación
llegue solo a todas las líneas del turno, incluidas las de otros módulos. Sin
eso hay que acordarse de pasarlo en cada log, y la línea donde uno se olvidó es
justo la que después hace falta.
"""

import io
import json
import logging

import pytest

from app import registro


@pytest.fixture
def salida():
    """Configura el logging en JSON sobre un flujo propio.

    Va sobre un StringIO y no sobre stderr porque pytest intercepta stderr con
    su propio handler, y entonces el test mira la captura de pytest en vez de
    lo que el módulo escribió de verdad.
    """
    flujo = io.StringIO()
    registro.configurar("INFO", "json", flujo)

    def leer():
        lineas = [json.loads(l) for l in flujo.getvalue().splitlines() if l.startswith("{")]
        flujo.truncate(0)
        flujo.seek(0)
        return lineas

    yield leer
    registro.configurar("INFO", "texto")


def test_el_id_de_la_conversacion_llega_solo_a_cada_linea(salida):
    log = logging.getLogger("app.worker")

    with registro.con_conversacion(4017):
        log.info("turno tomado")
        log.info("respuesta enviada")

    lineas = salida()
    assert [l["event"] for l in lineas] == ["turno tomado", "respuesta enviada"]
    assert all(l["conversacion_id"] == 4017 for l in lineas)


def test_tambien_alcanza_a_los_otros_modulos(salida):
    """El turno pasa por el loop, las herramientas y la sincronización con el
    CRM. Si el id solo apareciera en el worker, no serviría para reconstruirlo."""
    with registro.con_conversacion(4017):
        logging.getLogger("app.agente.loop").info("herramienta llamada")
        logging.getLogger("app.crm.sincronizacion").info("lead actualizado")

    lineas = salida()
    assert {l["logger"] for l in lineas} == {"app.agente.loop", "app.crm.sincronizacion"}
    assert all(l["conversacion_id"] == 4017 for l in lineas)


def test_al_salir_del_turno_el_contexto_se_limpia(salida):
    """Si quedara pegado, el turno siguiente heredaría el id del anterior y los
    logs mentirían, que es peor que no tenerlos."""
    log = logging.getLogger("app.worker")

    with registro.con_conversacion(4017):
        log.info("adentro")
    log.info("afuera")

    adentro, afuera = salida()
    assert adentro["conversacion_id"] == 4017
    assert "conversacion_id" not in afuera


def test_los_turnos_no_se_mezclan(salida):
    with registro.con_conversacion(1):
        logging.getLogger("app.worker").info("uno")
    with registro.con_conversacion(2):
        logging.getLogger("app.worker").info("dos")

    uno, dos = salida()
    assert uno["conversacion_id"] == 1
    assert dos["conversacion_id"] == 2


def test_los_marcadores_de_los_logs_viejos_se_resuelven(salida):
    """Casi todos los logs del proyecto usan "%s". Sin el procesador que los
    resuelve, saldrían a JSON con los marcadores sin llenar."""
    logging.getLogger("app.worker").info("enviando %s mensaje(s) tras %.1fs", 3, 2.5)

    assert salida()[0]["event"] == "enviando 3 mensaje(s) tras 2.5s"


def test_el_traceback_sobrevive_al_json(salida):
    """Un `logger.exception` sin esto pierde el traceback al pasar a JSON, que es
    exactamente lo que uno va a buscar."""
    with registro.con_conversacion(4017):
        try:
            raise RuntimeError("kommo caido")
        except RuntimeError:
            logging.getLogger("app.worker").exception("fallo la sincronizacion")

    linea = salida()[0]
    assert linea["level"] == "error"
    assert "RuntimeError: kommo caido" in linea["exception"]
    assert linea["conversacion_id"] == 4017


def test_el_formato_texto_no_es_json():
    """En la terminal los logs se leen; en producción se consultan."""
    flujo = io.StringIO()
    registro.configurar("INFO", "texto", flujo)
    logging.getLogger("app.worker").info("worker arrancado")
    registro.configurar("INFO", "texto")

    linea = flujo.getvalue()
    assert "worker arrancado" in linea
    assert not linea.lstrip().startswith("{")
