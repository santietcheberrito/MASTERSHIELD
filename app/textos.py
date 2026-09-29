"""La lista de precios de MasterShield, y como saber si un texto ya salio.

La lista tiene numeros, asi que la arma el codigo con lo que devuelve
`consultar_precio` y sale tal cual antes del mensaje del modelo. El modelo nunca
escribe un precio.

Hasta el 15/9/2026 aca vivian tambien las preguntas y el cierre como textos
fijos que el codigo metia en el mismo mensaje que el modelo. Chocaban con lo que
escribia el modelo y salian duplicados: ahora son respuestas por situacion en la
base (conocimiento/respuestas.yaml) y las escribe solo el modelo.
"""

from __future__ import annotations

import re
from typing import Any

from app import db
from app.precios import configuracion

MESES = ("ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO",
         "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE")

NUMEROS = ("1⃣", "2⃣", "3⃣", "4⃣")


def normal(texto: str) -> str:
    """Sin emojis, signos ni espacios de sobra, en minuscula.

    Para comparar textos: un emoji agregado o un renglon libre no pueden hacer
    que un texto que ya salio parezca nuevo. Paso con la explicacion del precio,
    que se mando dos veces porque se le agrego un 💰 (15/9/2026).
    """
    return " ".join(re.sub(r"[^\w\s]", " ", (texto or "").lower()).split())


async def ya_enviado(conversacion_id: int, contenido: str) -> bool:
    """Si ese texto ya le llego a esta persona, solo o dentro de otro mensaje.

    Se mira lo que efectivamente salio: un turno que se descarto porque la
    persona escribio en el medio no lo mando.
    """
    buscado = normal(contenido)
    if not buscado:
        return False
    filas = await db.consultar(
        "SELECT contenido FROM mensajes WHERE conversacion_id = $1 AND rol = 'agente' "
        "ORDER BY id DESC LIMIT 80",
        conversacion_id,
    )
    return any(buscado in normal(f["contenido"]) for f in filas)


def _monto(valor: float) -> str:
    return f"${valor:g}+iva por m2"


def lista_de_precios(precios: dict[str, Any]) -> str:
    """El mensaje de precios con el formato de MasterShield.

    `precios` es la respuesta de `consultar_precio`, que ya trae el precio de la
    zona y el especial solo si la promocion del mes esta vigente. Aca no se
    decide ningun numero: solo se les da forma.
    """
    calidades = precios["calidades"]
    mes = str(configuracion().get("vigencia_precio_especial") or "")
    nombre_mes = MESES[int(mes[5:7]) - 1] if len(mes) == 7 else ""
    etiqueta_normal = "Precio desde" if precios.get("tipo") == "desde" else "Precio normal"

    if len(calidades) == 1:
        encabezado = "▪ Actualmente en el material que necesita tenemos:"
    else:
        encabezado = f"▪ Actualmente en el material que necesita tenemos {len(calidades)} calidades:"

    bloques = []
    for numero, c in zip(NUMEROS, calidades):
        titulo = f"*Calidad {c['garantia_anios']} Años de Garantía*"
        if c.get("vida_util_hasta_anios"):
            titulo += f"  (Hasta {c['vida_util_hasta_anios']} años de vida útil)"
        if len(calidades) > 1:
            titulo = f"{numero} {titulo}"
        lineas = [titulo, f"{etiqueta_normal}: {_monto(c['precio_normal_m2_sin_iva'])}"]
        if c.get("precio_especial_m2_sin_iva") is not None and nombre_mes:
            lineas.append(f"*Precio especial {nombre_mes}*: {_monto(c['precio_especial_m2_sin_iva'])}")
        bloques.append("\n".join(lineas))

    partes = [encabezado, *bloques, "🔵 🟢"]
    if precios.get("descuento_pago_contado_pct"):
        # Una viñeta por forma de pago, a pedido del cliente (15/9/2026).
        partes.append(
            "▪ Puede cancelar con tarjeta de crédito sin recargo adicional.\n"
            f"▪ Pagos en efectivo/transferencia tienen un {precios['descuento_pago_contado_pct']}% "
            "de descuento adicional."
        )
    return "\n\n".join(partes)
