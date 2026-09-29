#!/usr/bin/env python3.12
"""Deja un video listo para mandarlo por WhatsApp.

    python3.12 scripts/preparar_video.py "~/Downloads/Info MS.MP4"

Lo que sale de un telefono no le sirve a la Cloud API tal como viene, por tres
motivos que ya aparecieron con el primer video del cliente:

- **Pesa demasiado.** WhatsApp acepta hasta 16 MB; un clip de 28 segundos
  grabado en 4K pesaba 87.
- **No se ve en Android.** Meta avisa que H.264 en perfil High con B-frames no
  lo reproducen los clientes Android, y es justo lo que graba un iPhone.
- **Sobra resolucion.** 720x1280 es mas que suficiente en una pantalla de
  telefono y es lo que hace que el archivo entre en el limite.

Deja el resultado en `media/video-productos.mp4`, que es el que manda el agente
junto con la descripcion de un producto arquitectonico. Cuando llegue el clip
definitivo con los cuatro productos, es correr esto con ese archivo: el codigo
no cambia.

Necesita ffmpeg en la maquina donde se corre, no en el servidor.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "media" / "video-productos.mp4"

# El tope de WhatsApp es 16 MB. Se apunta mas abajo para tener margen.
LIMITE_MB = 15.0


def _comprimir(origen: Path, destino: Path, crf: int) -> None:
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", str(origen),
        "-vf", "scale=720:-2,format=yuv420p",
        "-c:v", "libx264", "-profile:v", "main", "-level", "3.1",
        # Sin B-frames: con ellos el video no se reproduce en Android.
        "-bf", "0",
        "-preset", "slow", "-crf", str(crf),
        # Los metadatos al principio, para que empiece a reproducirse antes de
        # terminar de bajar.
        "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        str(destino),
    ], check=True)


def _describir(archivo: Path) -> dict:
    salida = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_type,codec_name,profile,width,height,has_b_frames",
        "-of", "json", str(archivo),
    ], check=True, capture_output=True, text=True)
    return json.loads(salida.stdout)


def _problemas(info: dict) -> list[str]:
    problemas = []
    mb = int(info["format"]["size"]) / 1_000_000
    if mb > LIMITE_MB:
        problemas.append(f"pesa {mb:.1f} MB y el limite es {LIMITE_MB:.0f}")
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if not video or video.get("codec_name") != "h264":
        problemas.append("el video no es H.264")
    elif video.get("profile") not in ("Main", "Constrained Baseline", "Baseline"):
        problemas.append(f"perfil {video.get('profile')}: Android no lo reproduce")
    elif int(video.get("has_b_frames") or 0) > 0:
        problemas.append("tiene B-frames: Android no lo reproduce")
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if len(audios) > 1:
        problemas.append("tiene mas de una pista de audio")
    if audios and audios[0].get("codec_name") != "aac":
        problemas.append("el audio no es AAC")
    return problemas


def principal(origen: Path) -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("Falta ffmpeg: brew install ffmpeg")
        return 1
    if not origen.exists():
        print(f"No existe {origen}")
        return 1

    DESTINO.parent.mkdir(parents=True, exist_ok=True)

    # Se empieza con buena calidad y se baja si no entra en el limite.
    for crf in (26, 29, 32):
        _comprimir(origen, DESTINO, crf)
        info = _describir(DESTINO)
        problemas = _problemas(info)
        mb = int(info["format"]["size"]) / 1_000_000
        print(f"calidad {crf}: {mb:.1f} MB")
        if not problemas:
            break
    else:
        for p in problemas:
            print(f"  PROBLEMA  {p}")
        return 1

    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    print(f"\nListo: {DESTINO.relative_to(RAIZ)}")
    print(f"  {video['width']}x{video['height']}, {float(info['format']['duration']):.1f} s, "
          f"{mb:.1f} MB, H.264 {video['profile']}, sin B-frames")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    raise SystemExit(principal(Path(sys.argv[1]).expanduser()))
