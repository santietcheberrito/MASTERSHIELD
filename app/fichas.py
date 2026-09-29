"""Las descripciones oficiales de cada producto, y el video que las acompaña.

MasterShield escribio un bloque de texto por producto y pidio que cuando alguien
pregunte por uno, se le mande ese bloque entero. Es texto de marca, igual que la
bienvenida, y por la misma razon no pasa por el modelo: si lo reescribiera, a la
tercera conversacion ya diria otra cosa.

El modelo decide *cuando* mandar una ficha —con `enviar_ficha`— y el worker la
manda palabra por palabra, antes de lo que el modelo escriba.

Los textos viven en `prompts/fichas/<id del producto>.txt`. Cambiar una
descripcion es editar ese archivo y reiniciar, como el prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app import db
from app.precios import configuracion

RAIZ = Path(__file__).resolve().parent.parent
DIRECTORIO = RAIZ / "prompts" / "fichas"


@dataclass(frozen=True)
class Video:
    """Un video a mandar como un mensaje mas de la respuesta."""

    ruta: Path
    # Lo que queda en el historial en lugar del video: el modelo lo lee en los
    # turnos siguientes y el asesor lo ve en la transcripcion.
    descripcion: str
    # El texto que acompaña al video, dentro del mismo mensaje.
    leyenda: str = ""

    @property
    def contenido(self) -> str:
        """Como queda en `mensajes`: que se mando y que decia."""
        return f"{self.descripcion} {self.leyenda}".strip()


# Un solo clip para los productos arquitectonicos. El vehicular no sale en el
# video, asi que su ficha va sin el. La leyenda es texto de la empresa, igual
# que las fichas, y vive en un archivo para poder cambiarla sin tocar codigo.
VIDEO_PRODUCTOS = Video(
    ruta=RAIZ / "media" / "video-productos.mp4",
    descripcion="[video de los productos MasterShield®]",
    leyenda=(DIRECTORIO / "leyenda-del-video.txt").read_text(encoding="utf-8").strip(),
)


def disponibles() -> list[str]:
    """Los productos que tienen ficha, en el orden del catalogo."""
    return [p["id"] for p in configuracion()["productos"]
            if (DIRECTORIO / f"{p['id']}.txt").exists()]


def nombre(id_producto: str) -> str:
    producto = next((p for p in configuracion()["productos"] if p["id"] == id_producto), {})
    return producto.get("nombre", id_producto)


@lru_cache
def texto(id_producto: str) -> str:
    return (DIRECTORIO / f"{id_producto}.txt").read_text(encoding="utf-8").strip()


def lleva_video(id_producto: str) -> bool:
    producto = next((p for p in configuracion()["productos"] if p["id"] == id_producto), {})
    return producto.get("linea") == "arquitectonico" and VIDEO_PRODUCTOS.ruta.exists()


async def ya_enviada(conversacion_id: int, id_producto: str) -> bool:
    """Si esta persona ya recibio esa ficha.

    Se mira lo que efectivamente salio y no un registro de "se pidio": un turno
    que se descarta porque la persona escribio en el medio pidio la ficha y no
    la mando.
    """
    return bool(await db.valor(
        "SELECT 1 FROM mensajes WHERE conversacion_id = $1 AND rol = 'agente' "
        "AND contenido = $2 LIMIT 1",
        conversacion_id, texto(id_producto),
    ))


async def video_ya_enviado(conversacion_id: int) -> bool:
    return bool(await db.valor(
        "SELECT 1 FROM mensajes WHERE conversacion_id = $1 AND rol = 'agente' "
        "AND tipo = 'video' LIMIT 1",
        conversacion_id,
    ))


async def armar_envio(conversacion_id: int, productos: list[str]) -> list[str | Video]:
    """Los mensajes que salen antes de la respuesta del modelo.

    Cada ficha va entera en un solo mensaje —partirla la desarmaria— y el video
    sale una sola vez por conversacion, pegado a la primera ficha
    arquitectonica. Mandarlo con cada producto seria mandar el mismo clip de
    nuevo.
    """
    partes: list[str | Video] = []
    falta_el_video = not await video_ya_enviado(conversacion_id)
    for id_producto in productos:
        partes.append(texto(id_producto))
        if falta_el_video and lleva_video(id_producto):
            partes.append(VIDEO_PRODUCTOS)
            falta_el_video = False
    return partes
