"""El documento que se carga en el CRM.

Deliberadamente no sabe nada de Kommo ni de Notion: arma qué contacto, qué
lead, qué nota y qué tarea corresponden a una conversación, y cada destino se
encarga de traducirlo a sus propios nombres de campo.

Esa separación es lo que permite construir y validar esto sin acceso a Kommo.
Los IDs numéricos de campos y etapas son lo único que falta, y son un mapeo al
final del camino, no una decisión de diseño.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from app import db
from app.config import obtener_settings
from app.precios import PRODUCTO_POR_OBJETIVO, cotizar
from app.scoring import CONVERSANDO, DERIVADA, Puntaje, puntuar

CANALES = {"telegram": "Telegram", "whatsapp": "WhatsApp", "consola": "Consola"}
ZONAS = {
    "quito_y_valles": "Quito y valles",
    "otra_ciudad": "Otra ciudad del país",
    "fuera_del_pais": "Fuera de Ecuador",
}
LINEAS = {"arquitectonico": "Arquitectónico", "vehicular": "Vehicular"}
APLICACIONES = {
    "domicilio": "Domicilio", "oficina": "Oficina", "pergola": "Pérgola",
    "local": "Local", "vehiculo": "Vehículo",
}
URGENCIAS = {"inmediato": "Inmediato", "semanas": "Semanas", "explorando": "Explorando"}
TIPOS_CLIENTE = {
    "particular": "Particular", "empresa": "Empresa",
    "constructora_o_arquitecto": "Constructora o arquitecto",
}
PRODUCTOS = {
    "control_solar_arquitectonico": "Control Solar Arquitectónico",
    "privacidad_arquitectonica": "Privacidad Arquitectónica",
    "seguridad_arquitectonica": "Seguridad Arquitectónica",
    "seguridad_vehicular": "Seguridad Vehicular",
}


@dataclass
class Documento:
    conversacion_id: int
    contacto: dict[str, Any]
    lead: dict[str, Any]
    nota: str
    tarea: dict[str, Any]
    puntaje: Puntaje = field(default=None)  # type: ignore[assignment]


def _texto_de_la_tarea(
    nombre: str,
    telefono: str | None,
    disponibilidad: str | None,
    motivo_derivacion: str | None,
) -> str:
    """Lo unico que el asesor lee antes de decidir a quien atiende primero."""
    quien = f"{nombre} ({telefono})" if telefono else nombre
    if motivo_derivacion:
        return f"ATENDER a {quien} — {motivo_derivacion}"
    if disponibilidad:
        return f"Llamar a {quien} — {disponibilidad}"
    return f"Llamar a {quien}"


def proxima_fecha_de_llamado(momento: datetime | None = None) -> date:
    """Cuándo tiene que llamar el vendedor.

    El agente atiende y califica a cualquier hora —es una ventaja de venta—
    pero la tarea de llamado se agenda para cuando haya alguien. Se evalúa
    siempre en hora de Ecuador, no en la del servidor.
    """
    settings = obtener_settings()
    momento = (momento or datetime.now(settings.zona)).astimezone(settings.zona)

    if settings.esta_en_horario(momento):
        return momento.date()

    # El proximo dia habil. Si ya paso el horario de hoy, es manana.
    candidato = momento
    if momento.time() >= settings.horario[1]:
        candidato = momento + timedelta(days=1)
    while candidato.weekday() >= 5:
        candidato += timedelta(days=1)
    return candidato.date()


def _presupuesto(datos: dict[str, Any]) -> float | None:
    """El monto que el agente le dijo al cliente, sin IVA."""
    id_producto = PRODUCTO_POR_OBJETIVO.get((datos.get("linea"), datos.get("objetivo")))
    if not id_producto:
        return None
    resultado = cotizar(
        id_producto,
        datos.get("metros_cuadrados"),
        datos.get("zona"),
        datos.get("garantia_anios"),
    )
    return resultado.subtotal if resultado.puede_cotizar else None


def _producto(datos: dict[str, Any]) -> str | None:
    id_producto = PRODUCTO_POR_OBJETIVO.get((datos.get("linea"), datos.get("objetivo")))
    return PRODUCTOS.get(id_producto) if id_producto else None


def resumir(datos: dict[str, Any], puntaje: Puntaje, presupuesto: float | None) -> str:
    """El encabezado de la nota: lo que el vendedor lee antes de marcar."""
    partes = []
    producto = _producto(datos)
    if producto:
        partes.append(producto)
    if datos.get("metros_cuadrados"):
        partes.append(f"{datos['metros_cuadrados']:g} m²")
    if datos.get("aplicacion"):
        partes.append(APLICACIONES.get(datos["aplicacion"], datos["aplicacion"]).lower())
    if datos.get("zona"):
        partes.append(ZONAS.get(datos["zona"], datos["zona"]))

    lineas = [" · ".join(partes) if partes else "Consulta sin datos suficientes"]

    if presupuesto is not None:
        lineas.append(f"Presupuesto estimado: USD {presupuesto:,.2f} + IVA")
    if datos.get("modelo_vehiculo"):
        lineas.append(f"Vehículo: {datos['modelo_vehiculo']}")
    if datos.get("disponibilidad"):
        lineas.append(f"Disponible: {datos['disponibilidad']}")
    if datos.get("medidas_detalle"):
        lineas.append(f"Medidas: {datos['medidas_detalle']}")

    if puntaje.motivo:
        lineas.append(f"Motivo: {puntaje.motivo}")
    elif puntaje.desglose:
        lineas.append(f"Score {puntaje.score}/100 — {puntaje.resumen()}")

    return "\n".join(lineas)


async def transcribir(conversacion_id: int) -> str:
    """La conversación completa, para que el asesor sepa qué se habló."""
    filas = await db.consultar(
        "SELECT rol, tipo, contenido, creado_en FROM mensajes "
        "WHERE conversacion_id = $1 ORDER BY id",
        conversacion_id,
    )
    quien = {"cliente": "Cliente", "agente": "Agente", "vendedor": "Asesor"}
    lineas = []
    for f in filas:
        texto = f["contenido"] or f"[{f['tipo']}]"
        if f["tipo"] != "texto" and f["contenido"]:
            texto = f"[{f['tipo']}] {texto}"
        lineas.append(f"{f['creado_en']:%d/%m %H:%M} · {quien.get(f['rol'], f['rol'])}: {texto}")
    return "\n".join(lineas)


async def armar(conversacion_id: int) -> Documento:
    """Construye el documento a partir del estado de la conversación."""
    fila = await db.consultar_una(
        "SELECT id, canal, identificador, nombre, telefono, estado, datos "
        "FROM conversaciones WHERE id = $1",
        conversacion_id,
    )
    if fila is None:
        raise ValueError(f"no existe la conversacion {conversacion_id}")

    datos = fila["datos"] or {}
    puntaje = puntuar(datos)

    # El estado de la conversacion manda sobre el puntaje: si una persona se
    # hizo cargo, la etapa lo tiene que decir aunque el score sea alto.
    if fila["estado"] == "derivada":
        puntaje = Puntaje(puntaje.score, puntaje.clasificacion, DERIVADA,
                          puntaje.desglose, "un asesor se hizo cargo de la conversación")
    elif fila["estado"] == "activa":
        puntaje = Puntaje(puntaje.score, puntaje.clasificacion, CONVERSANDO,
                          puntaje.desglose, "el agente todavía está conversando")

    # El puntaje solo sabe que la conversacion esta derivada; por que lo esta
    # —lo pidio la persona, se puso molesta, saltaron los limites de uso— quedo
    # en el evento. Es lo primero que necesita leer quien la tome.
    motivo_derivacion = None
    if fila["estado"] == "derivada":
        motivo_derivacion = await db.valor(
            "SELECT detalle->>'motivo' FROM eventos WHERE conversacion_id = $1 "
            "AND tipo = 'escalado_a_humano' ORDER BY id DESC LIMIT 1",
            conversacion_id,
        )

    presupuesto = _presupuesto(datos)
    nota = resumir(datos, puntaje, presupuesto) + "\n\n---\n\n" + await transcribir(conversacion_id)

    nombre = fila["nombre"] or fila["telefono"] or f"Consulta {conversacion_id}"

    return Documento(
        conversacion_id=conversacion_id,
        contacto={"nombre": nombre, "telefono": fila["telefono"]},
        lead={
            "nombre": nombre,
            "etapa": puntaje.etapa,
            "score": puntaje.score,
            "clasificacion": puntaje.clasificacion,
            "canal": CANALES.get(fila["canal"], fila["canal"]),
            "telefono": fila["telefono"],
            "zona": ZONAS.get(datos.get("zona")),
            "linea": LINEAS.get(datos.get("linea")),
            "producto": _producto(datos),
            "metros_cuadrados": datos.get("metros_cuadrados"),
            "presupuesto": presupuesto,
            "garantia": f"{datos['garantia_anios']} años" if datos.get("garantia_anios") else None,
            "aplicacion": APLICACIONES.get(datos.get("aplicacion")),
            "urgencia": URGENCIAS.get(datos.get("urgencia")),
            "tipo_cliente": TIPOS_CLIENTE.get(datos.get("tipo_cliente")),
            "disponibilidad": datos.get("disponibilidad"),
            "modelo_vehiculo": datos.get("modelo_vehiculo"),
            "medidas_detalle": datos.get("medidas_detalle"),
        },
        nota=nota,
        tarea={
            # La disponibilidad va en el texto, no solo en un campo del lead: la
            # lista de tareas es lo unico que el asesor mira antes de marcar, y
            # "hoy en una hora" cambia a que hora levanta el telefono.
            #
            # Una derivacion es otra cosa: no es "llamar cuando le quede comodo",
            # es alguien esperando ahora. Kommo avisa al responsable cuando una
            # tarea esta por vencer —campana, push al movil y mail—, y eso es lo
            # unico que la API deja usar como notificacion: el centro de
            # notificaciones de Kommo es JavaScript de widget, no un endpoint.
            # Asi que la tarea urgente ES el aviso.
            "texto": _texto_de_la_tarea(
                nombre, fila["telefono"], datos.get("disponibilidad"), motivo_derivacion
            ),
            "vence": proxima_fecha_de_llamado(),
            "urgente": motivo_derivacion is not None,
            "responsable": None,  # los 3 vendedores la ven; la toma el primero
        },
        puntaje=puntaje,
    )
