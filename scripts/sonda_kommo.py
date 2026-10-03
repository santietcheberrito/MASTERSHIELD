#!/usr/bin/env python3.12
"""Sonda para ver que manda Kommo de verdad, y no lo que dice el manual.

    .venv/bin/python scripts/sonda_kommo.py

Levanta un servidor que acepta cualquier ruta y cualquier metodo, y escribe
entero lo que recibe —metodo, ruta, cabeceras y cuerpo— en la consola y en un
archivo. Sirve para dos cosas distintas:

- **Webhooks de cuenta.** Se suscriben apuntando aca y se ve que llega cuando
  entra un mensaje con una foto o una nota de voz: si viene la URL del archivo,
  si viene solo el tipo, o si no viene nada.
- **Llamadas del bot.** El bot de Kommo exige respuesta en 2 segundos y despues
  uno le contesta por la `return_url`. La sonda confirma al toque y, si en el
  cuerpo viene una `return_url`, le manda lo que diga `RESPUESTA` para ver que
  manejadores acepta de verdad.

No es parte del agente: es una herramienta de diagnostico y vive aparte a
proposito.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

PUERTO = 8150
REGISTRO = Path("/tmp/claude-501/ms/sonda.jsonl")

# Lo que se le devuelve al bot por la `return_url`. Se cambia aca para probar
# que manejadores acepta: texto, botones, saltar a otro paso.
RESPUESTA = {
    "data": {},
    "execute_handlers": [
        {"handler": "show", "params": {"type": "text", "value": "sonda: te leo"}},
    ],
}

app = FastAPI()


def _anotar(entrada: dict) -> None:
    REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    with REGISTRO.open("a") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    print("\n" + "=" * 70)
    print(f"{entrada['cuando']}  {entrada['metodo']} {entrada['ruta']}")
    print("-" * 70)
    print(json.dumps(entrada["cuerpo"], ensure_ascii=False, indent=2)[:4000])
    sys.stdout.flush()


@app.api_route("/{ruta:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def todo(ruta: str, request: Request) -> JSONResponse:
    crudo = await request.body()
    tipo = request.headers.get("content-type", "")

    # Kommo manda los webhooks de cuenta como formulario y los del bot como
    # JSON. Se guardan los dos parseados, para poder leerlos.
    if "json" in tipo:
        try:
            cuerpo = json.loads(crudo)
        except ValueError:
            cuerpo = {"_sin_parsear": crudo.decode("utf-8", "replace")}
    elif crudo:
        from urllib.parse import parse_qs
        cuerpo = {k: v if len(v) > 1 else v[0]
                  for k, v in parse_qs(crudo.decode("utf-8", "replace")).items()}
    else:
        cuerpo = dict(request.query_params)

    entrada = {
        "crudo": crudo.decode("utf-8", "replace"),
        "cuando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metodo": request.method,
        "ruta": "/" + ruta,
        "cabeceras": {k: v for k, v in request.headers.items()
                      if k.lower() not in ("authorization", "cookie")},
        "cuerpo": cuerpo,
    }
    _anotar(entrada)

    # Si es una llamada del bot, se le contesta por la via que corresponde: el
    # 200 ahora y el contenido despues, por la `return_url`.
    destino = cuerpo.get("return_url") if isinstance(cuerpo, dict) else None
    if destino:
        try:
            async with httpx.AsyncClient(timeout=10) as cli:
                r = await cli.post(destino, json=RESPUESTA)
            print(f"[return_url] respondido -> {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"[return_url] FALLO: {e}")
        sys.stdout.flush()

    return JSONResponse({"ok": True})


if __name__ == "__main__":
    print(f"sonda escuchando en :{PUERTO}, registrando en {REGISTRO}")
    uvicorn.run(app, host="127.0.0.1", port=PUERTO, log_level="warning")
