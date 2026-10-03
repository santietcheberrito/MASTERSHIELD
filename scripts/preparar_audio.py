#!/usr/bin/env python3.12
"""Deja una nota de voz lista para mandarla por WhatsApp.

    python3.12 scripts/preparar_audio.py quito "~/Downloads/audio de Esteban.mp4"

WhatsApp dibuja la burbuja de nota de voz —avatar y microfono— solo si el
archivo es **Ogg/Opus**. Cualquier otro formato llega como archivo de audio, con
el icono de auriculares, que es como llega un audio reenviado y no es lo que el
cliente quiere que vea su cliente.

Y el Ogg tiene que salir **sin metadatos**. Esto costo una tarde el 30/9/2026:
un audio que sale de WhatsApp viene en .mp4 y arrastra sus tags —creation_time,
language, handler_name=Core Media Audio—. ffmpeg los copia al header OpusTags, y
ahi Meta:

1. acepta la subida y devuelve un media id,
2. acepta el envio y devuelve un wamid,
3. y recien despues falla, por webhook, con el error **131053**: "on processing
   it is of type application/octet-stream".

O sea que un audio con metadatos parece enviado y nunca llega. El mismo archivo
convertido con `-map_metadata -1` se entrega sin problema: probado uno contra
otro, con la unica diferencia de esa bandera.

Necesita ffmpeg en la maquina donde se corre, no en el servidor.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Que nombre lleva cada una. Las dos salen pegadas a la lista de precios, y cual
# sale depende de la zona: en Quito se impulsa la visita, afuera la llamada.
DESTINOS = {
    "quito": RAIZ / "media" / "audio-quito-visita.ogg",
    "fuera": RAIZ / "media" / "audio-fuera-llamada.ogg",
}

# El tope de WhatsApp es 16 MB. Una nota de voz de un minuto pesa unos 250 KB.
LIMITE_MB = 16.0


def _convertir(origen: Path, destino: Path) -> None:
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", str(origen),
        # Sin video (un .mp4 de WhatsApp no lo trae, pero un mp4 cualquiera si).
        "-vn",
        # La bandera que decide si el audio llega o no. No sacarla.
        "-map_metadata", "-1",
        "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1",
        # Opus afinado para voz, que es lo que son estas grabaciones.
        "-application", "voip",
        "-f", "ogg",
        str(destino),
    ], check=True)


def _describir(archivo: Path) -> dict:
    salida = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size,format_name:stream=codec_type,codec_name,channels,sample_rate",
        "-of", "json", str(archivo),
    ], check=True, capture_output=True, text=True)
    return json.loads(salida.stdout)


def _comentarios(archivo: Path) -> int:
    """Cuantos campos tiene el header OpusTags. Con mas de uno, Meta lo rechaza.

    Se lee del archivo y no de ffprobe: ffprobe no muestra el `encoder`, que es
    el unico que libopus escribe siempre y que si esta permitido.
    """
    datos = archivo.read_bytes()[:4096]
    i = datos.find(b"OpusTags")
    if i < 0:
        return -1
    largo_vendor = int.from_bytes(datos[i + 8:i + 12], "little")
    inicio = i + 12 + largo_vendor
    return int.from_bytes(datos[inicio:inicio + 4], "little")


def _problemas(info: dict, comentarios: int) -> list[str]:
    problemas = []
    mb = int(info["format"]["size"]) / 1_000_000
    if mb > LIMITE_MB:
        problemas.append(f"pesa {mb:.1f} MB y el limite es {LIMITE_MB:.0f}")
    if "ogg" not in info["format"]["format_name"]:
        problemas.append("el contenedor no es Ogg: WhatsApp no lo muestra como nota de voz")
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if len(audios) != 1:
        problemas.append(f"tiene {len(audios)} pistas de audio y tiene que tener una")
    elif audios[0].get("codec_name") != "opus":
        problemas.append("el audio no es Opus: WhatsApp no lo muestra como nota de voz")
    if comentarios < 0:
        problemas.append("no tiene header OpusTags")
    elif comentarios > 1:
        problemas.append(f"arrastra {comentarios} metadatos: Meta lo va a rechazar (131053)")
    return problemas


def principal(cual: str, origen: Path) -> int:
    if cual not in DESTINOS:
        print(f"El primer argumento es {' o '.join(DESTINOS)}, no {cual!r}")
        return 1
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("Falta ffmpeg: brew install ffmpeg")
        return 1
    if not origen.exists():
        print(f"No existe {origen}")
        return 1

    destino = DESTINOS[cual]
    destino.parent.mkdir(parents=True, exist_ok=True)
    _convertir(origen, destino)

    info = _describir(destino)
    comentarios = _comentarios(destino)
    problemas = _problemas(info, comentarios)
    if problemas:
        for p in problemas:
            print(f"  PROBLEMA  {p}")
        return 1

    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    kb = int(info["format"]["size"]) / 1000
    print(f"Listo: {destino.relative_to(RAIZ)}")
    print(f"  Ogg/Opus, {audio['channels']} canal, {audio['sample_rate']} Hz, "
          f"{float(info['format']['duration']):.0f} s, {kb:.0f} KB, sin metadatos")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(principal(sys.argv[1], Path(sys.argv[2]).expanduser()))
