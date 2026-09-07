"""Traduce el documento del lead a Kommo y lo carga.

El documento no sabe de ningun CRM: dice que contacto, que lead, que nota y que
tarea corresponden a una conversacion. Aca se convierte a los IDs numericos de
la cuenta, que salen de `config/kommo.yaml` y nunca se escriben a mano.

Es idempotente: si la conversacion ya tiene un lead, se actualiza en vez de
crear otro. Y el contacto se busca por telefono antes de crearlo, que es lo que
evita terminar con un contacto por conversacion de la misma persona.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, time, timedelta
from typing import Any

from app.config import obtener_settings
from app.crm.documento import Documento
from app.kommo import archivos as archivos_kommo
from app.kommo.cliente import Kommo, KommoNoConfigurado, configuracion

logger = logging.getLogger(__name__)

__all__ = ["sincronizar", "KommoNoConfigurado"]

# Que campo del lead recibe cada dato del documento.
CAMPOS = {
    "score": "score",
    "clasificacion": "clasificacion",
    "zona": "zona",
    "linea": "linea",
    "producto": "producto",
    "metros_cuadrados": "metros_cuadrados",
    "presupuesto": "presupuesto",
    "garantia": "garantia",
    "aplicacion": "aplicacion",
    "urgencia": "urgencia",
    "tipo_cliente": "tipo_cliente",
    "disponibilidad": "disponibilidad",
    "modelo_vehiculo": "modelo_vehiculo",
    "medidas_detalle": "medidas_detalle",
    "canal": "canal",
    # Por que esta esperando una persona. Estaba solo en la nota y en el texto
    # de la tarea; como campo se ve en la tarjeta y se puede filtrar, que es lo
    # que permite mirar de un vistazo que hay en la etapa de derivados.
    "motivo_derivacion": "motivo_derivacion",
}

# La clasificacion del scoring, con la primera en mayuscula, como esta cargada
# en el campo de seleccion de Kommo.
CLASIFICACIONES = {
    "alta": "Alta", "media": "Media", "baja": "Baja", "descartada": "Descartada",
}


def _valores_personalizados(documento: Documento) -> list[dict[str, Any]]:
    ids = configuracion()["campos_lead"]
    valores = []
    for clave_documento, clave_config in CAMPOS.items():
        id_campo = ids.get(clave_config)
        dato = documento.lead.get(clave_documento)
        if clave_documento == "clasificacion":
            dato = CLASIFICACIONES.get(dato)
        if id_campo is None or dato in (None, ""):
            continue
        valores.append({"field_id": id_campo, "values": [{"value": dato}]})
    return valores


# Cuanto se le da a una derivacion antes de que Kommo la marque vencida. Corto
# a proposito: es lo que hace que suene la campana y llegue el push. Poner cero
# no sirve —Kommo rechaza tareas que ya vencieron— y poner una hora la deja
# indistinguible de las demas en la lista.
MINUTOS_PARA_UNA_DERIVACION = 15


def _vencimiento(documento: Documento) -> int:
    """Cuando vence la tarea, en hora de Ecuador.

    Una tarea normal vence al final del dia de llamado. Una derivacion no: hay
    alguien esperando ahora, y el vencimiento es lo unico que hace que Kommo
    avise —campana, push al movil, mail al responsable—. No hay un endpoint de
    notificaciones en la API: el centro de notificaciones de Kommo es
    JavaScript que corre dentro de un widget.
    """
    settings = obtener_settings()
    ahora = datetime.now(settings.zona)

    if documento.tarea.get("urgente"):
        return int((ahora + timedelta(minutes=MINUTOS_PARA_UNA_DERIVACION)).timestamp())

    fecha = documento.tarea["vence"]
    _, fin = settings.horario
    momento = datetime.combine(fecha, fin or time(18, 0), tzinfo=settings.zona)

    # Si el dia de llamado es hoy y el horario ya paso —una consulta que entra
    # 19h30—, un timestamp en el pasado no le sirve a nadie: la tarea nace
    # vencida y se pierde entre las atrasadas.
    if momento <= ahora:
        return int((ahora + timedelta(minutes=MINUTOS_PARA_UNA_DERIVACION)).timestamp())

    return int(momento.timestamp())


def _cuerpo_del_lead(documento: Documento, id_contacto: int | None) -> dict[str, Any]:
    config = configuracion()
    etapa = config["etapas"].get(documento.lead["etapa"])

    cuerpo: dict[str, Any] = {
        "name": f"{documento.lead['nombre']} — {documento.lead.get('producto') or 'consulta'}",
        "pipeline_id": config["embudo"]["id"],
        "custom_fields_values": _valores_personalizados(documento),
    }
    if etapa:
        cuerpo["status_id"] = etapa
    if documento.lead.get("presupuesto"):
        # El monto del lead, que es lo que Kommo suma en los reportes.
        cuerpo["price"] = int(documento.lead["presupuesto"])
    if id_contacto:
        cuerpo["_embedded"] = {"contacts": [{"id": id_contacto}]}
    return cuerpo


async def sincronizar(documento: Documento, referencia: dict | None = None) -> dict:
    """Carga la conversacion en Kommo. Devuelve la referencia para guardarla.

    `referencia` es lo que devolvio una sincronizacion anterior de esta misma
    conversacion, si la hubo.
    """
    kommo = Kommo()
    referencia = referencia or {}
    telefono = documento.contacto.get("telefono")

    # --- contacto -----------------------------------------------------------
    id_contacto = referencia.get("contacto")
    if not id_contacto and telefono:
        id_contacto = await kommo.buscar_contacto(telefono)
        if id_contacto:
            logger.info("contacto existente reutilizado | kommo_contacto=%s", id_contacto)
    if not id_contacto:
        id_contacto = await kommo.crear_contacto(documento.contacto["nombre"], telefono)
        logger.info("contacto creado | kommo_contacto=%s", id_contacto)

    # --- lead ---------------------------------------------------------------
    cuerpo = _cuerpo_del_lead(documento, id_contacto)
    id_lead = referencia.get("lead")

    if id_lead:
        # En un PATCH no se reasignan los contactos ni cambia el nombre.
        cuerpo.pop("_embedded", None)
        cuerpo.pop("name", None)
        await kommo.actualizar_lead(id_lead, cuerpo)
        logger.info("lead actualizado | kommo_lead=%s", id_lead)
    else:
        id_lead = await kommo.crear_lead(cuerpo)
        logger.info("lead creado | kommo_lead=%s etapa=%s",
                    id_lead, documento.lead["etapa"])

    # --- nota ---------------------------------------------------------------
    # Las notas son el historial de lo que fue pasando, no un campo que se
    # pisa: cada vez que la conversacion avanza se agrega una. Pero repetir la
    # misma no aporta nada, y una sincronizacion se puede repetir por un
    # reintento. Se guarda una huella de la ultima y se saltea si no cambio.
    huella = hashlib.sha256(documento.nota.encode("utf-8")).hexdigest()[:16]
    if huella != referencia.get("huella_nota"):
        await kommo.agregar_nota(id_lead, documento.nota)
    else:
        logger.info("la nota no cambio, no se duplica | kommo_lead=%s", id_lead)

    # --- tarea de llamado ---------------------------------------------------
    # Solo para lo que un asesor tiene que llamar. Un descarte o una consulta a
    # medias no genera tarea: llenar la lista de tareas de cosas que nadie va a
    # hacer es la forma mas rapida de que dejen de mirarla.
    id_tarea = referencia.get("tarea")
    urgente = bool(documento.tarea.get("urgente"))

    if not id_tarea and (
        urgente or documento.puntaje.clasificacion in ("alta", "media", "baja")
    ):
        id_tarea = await kommo.crear_tarea(
            id_lead, documento.tarea["texto"], _vencimiento(documento)
        )
        logger.info("tarea creada | kommo_tarea=%s urgente=%s", id_tarea, urgente)

    # La conversacion se derivo despues de que ya hubiera una tarea de llamado
    # agendada para el jueves. Esa tarea ya no representa lo que hay que hacer:
    # hay alguien esperando ahora. Se adelanta en vez de crear una segunda, que
    # dejaria al asesor con dos filas para la misma persona.
    elif id_tarea and urgente:
        await kommo.actualizar_tarea(
            id_tarea,
            {"text": documento.tarea["texto"], "complete_till": _vencimiento(documento)},
        )
        logger.info("tarea adelantada por derivacion | kommo_tarea=%s", id_tarea)

    # --- archivos -----------------------------------------------------------
    # Las fotos que mando el cliente. Van al final: si algo falla aca, el lead
    # ya esta cargado y el asesor tiene con que llamar. Perder una foto es malo;
    # perder el lead entero por una foto seria peor.
    subidos = dict(referencia.get("archivos") or {})
    if documento.archivos:
        subidos = await _subir_archivos(kommo, id_lead, documento.archivos, subidos)

    return {"destino": "kommo", "contacto": id_contacto, "lead": id_lead,
            "tarea": id_tarea, "huella_nota": huella, "archivos": subidos}


async def _subir_archivos(kommo: Kommo, id_lead: int, archivos: list[dict],
                          ya_subidos: dict) -> dict:
    """Sube al lead lo que todavia no este. Nunca corta la sincronizacion.

    `ya_subidos` mapea el id del archivo en Meta al uuid que quedo en Kommo, y
    es lo que evita subir la misma foto en cada reintento.
    """
    pendientes = [a for a in archivos if a["id"] not in ya_subidos]
    if not pendientes:
        return ya_subidos

    try:
        drive = await archivos_kommo.drive_de_la_cuenta(kommo)
    except Exception:
        logger.exception("no se pudo averiguar el drive: no se suben archivos "
                         "| kommo_lead=%s", id_lead)
        return ya_subidos

    nuevos = []
    for numero, archivo in enumerate(pendientes, len(ya_subidos) + 1):
        try:
            uuid = await archivos_kommo.subir(
                kommo, drive,
                archivos_kommo.nombre_para(archivo, numero),
                archivo["contenido"],
                archivo.get("mime") or "application/octet-stream",
            )
        except Exception:
            # Un archivo que falla no puede llevarse puestos a los demas ni a
            # la sincronizacion: queda fuera de `ya_subidos` y el proximo
            # reintento lo vuelve a intentar, si Meta todavia lo tiene.
            logger.exception("no se pudo subir un archivo | kommo_lead=%s media=%s",
                             id_lead, archivo["id"])
            continue
        ya_subidos[archivo["id"]] = uuid
        nuevos.append(uuid)

    if nuevos:
        try:
            await archivos_kommo.adjuntar(kommo, id_lead, nuevos)
            logger.info("%s archivo(s) adjuntados al lead | kommo_lead=%s",
                        len(nuevos), id_lead)
        except Exception:
            logger.exception("los archivos se subieron pero no se pudieron "
                             "adjuntar | kommo_lead=%s", id_lead)
            # Se quitan de la lista para que el reintento vuelva a colgarlos.
            for uuid in nuevos:
                ya_subidos = {k: v for k, v in ya_subidos.items() if v != uuid}

    return ya_subidos
