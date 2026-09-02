"""Aisla al loop de tool use del proveedor del modelo.

El proyecto arranco con Anthropic. A mitad de camino el cliente se quedo sin
creditos y hubo que pasar a OpenAI. Lo que cambia entre uno y otro no es como
razona el agente: es la forma de los mensajes, donde va el prompt del sistema y
como se devuelven los resultados de las herramientas.

Todo eso vive aca. El loop no sabe con cual esta hablando, y volver a Claude es
cambiar PROVEEDOR_MODELO.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Llamada:
    id: str
    nombre: str
    argumentos: dict[str, Any]


@dataclass
class Salida:
    """Lo que devolvio el modelo en una vuelta, ya normalizado."""

    # Vacio cuando el modelo solo llamo herramientas y no escribio nada.
    texto: str = ""
    llamadas: list[Llamada] = field(default_factory=list)
    tokens_entrada: int = 0
    tokens_salida: int = 0
    tokens_cache: int = 0
    # El mensaje del asistente tal como lo espera este proveedor, para
    # devolverselo en la vuelta siguiente.
    crudo: Any = None


class Proveedor(Protocol):
    def mensajes_iniciales(self, sistema: list[str], historial: list[dict]) -> list[dict]:
        """Arma la lista de mensajes con la que arranca el turno."""

    async def completar(self, mensajes: list[dict], herramientas: list[dict]) -> Salida:
        ...

    def continuar(
        self, mensajes: list[dict], salida: Salida, resultados: list[tuple[Llamada, dict]]
    ) -> list[dict]:
        """Agrega la respuesta del modelo y los resultados de las herramientas."""


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

class ProveedorAnthropic:
    """El prompt del sistema va aparte de los mensajes, y se puede marcar para
    cachear explicitamente con un TTL."""

    def __init__(self, api_key: str, modelo: str, max_tokens: int = 1024) -> None:
        from anthropic import AsyncAnthropic

        self._cliente = AsyncAnthropic(api_key=api_key)
        self._modelo = modelo
        self._max_tokens = max_tokens
        self._sistema: list[dict] = []

    def mensajes_iniciales(self, sistema: list[str], historial: list[dict]) -> list[dict]:
        # El primer bloque es el estatico: identico en cada turno de cada
        # conversacion, asi que se cachea con TTL de una hora.
        self._sistema = [
            {"type": "text", "text": sistema[0],
             "cache_control": {"type": "ephemeral", "ttl": "1h"}}
        ] + [{"type": "text", "text": t} for t in sistema[1:]]
        return [{"role": "user" if m["rol"] == "cliente" else "assistant",
                 "content": m["texto"]} for m in historial]

    async def completar(self, mensajes: list[dict], herramientas: list[dict]) -> Salida:
        mensaje = await self._cliente.messages.create(
            model=self._modelo,
            max_tokens=self._max_tokens,
            system=self._sistema,
            tools=[{"name": h["nombre"], "description": h["descripcion"],
                    "input_schema": h["esquema"]} for h in herramientas],
            messages=mensajes,
        )
        return Salida(
            texto="".join(b.text for b in mensaje.content if b.type == "text").strip(),
            llamadas=[Llamada(b.id, b.name, b.input)
                      for b in mensaje.content if b.type == "tool_use"],
            tokens_entrada=mensaje.usage.input_tokens,
            tokens_salida=mensaje.usage.output_tokens,
            tokens_cache=getattr(mensaje.usage, "cache_read_input_tokens", 0) or 0,
            crudo=mensaje.content,
        )

    def continuar(self, mensajes, salida, resultados):
        mensajes.append({"role": "assistant", "content": salida.crudo})
        mensajes.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": llamada.id,
             "content": json.dumps(resultado, ensure_ascii=False, default=str)}
            for llamada, resultado in resultados
        ]})
        return mensajes


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

class ProveedorOpenAI:
    """El prompt del sistema es un mensaje mas, y el cache es automatico sobre
    el prefijo: no se marca, pero se gana igual si lo estatico va primero, que
    es como esta armado."""

    def __init__(self, api_key: str, modelo: str, max_tokens: int = 4096) -> None:
        from openai import AsyncOpenAI

        self._cliente = AsyncOpenAI(api_key=api_key)
        self._modelo = modelo
        # Los modelos de razonamiento cuentan lo que "piensan" dentro del mismo
        # presupuesto que lo que escriben. Con 1024 gastaban todo razonando y
        # devolvian el mensaje vacio, sin error: la conversacion se quedaba muda.
        self._max_tokens = max_tokens
        self._razona = modelo.startswith(("gpt-5", "o1", "o3", "o4"))

    def mensajes_iniciales(self, sistema: list[str], historial: list[dict]) -> list[dict]:
        mensajes = [{"role": "system", "content": t} for t in sistema]
        mensajes += [{"role": "user" if m["rol"] == "cliente" else "assistant",
                      "content": m["texto"]} for m in historial]
        return mensajes

    async def completar(self, mensajes: list[dict], herramientas: list[dict]) -> Salida:
        extra: dict[str, Any] = {}
        if self._razona:
            # Esto es una conversacion de ventas, no un problema de logica: no
            # hace falta que razone largo, y razonar cuesta tokens y latencia.
            extra["reasoning_effort"] = "low"

        respuesta = await self._cliente.chat.completions.create(
            model=self._modelo,
            max_completion_tokens=self._max_tokens,
            messages=mensajes,
            tools=[{"type": "function", "function": {
                "name": h["nombre"], "description": h["descripcion"],
                "parameters": h["esquema"]}} for h in herramientas],
            **extra,
        )
        eleccion = respuesta.choices[0].message
        llamadas = []
        for llamada in (eleccion.tool_calls or []):
            try:
                argumentos = json.loads(llamada.function.arguments or "{}")
            except json.JSONDecodeError:
                argumentos = {}
            llamadas.append(Llamada(llamada.id, llamada.function.name, argumentos))

        detalles = getattr(respuesta.usage, "prompt_tokens_details", None)
        return Salida(
            texto=(eleccion.content or "").strip(),
            llamadas=llamadas,
            tokens_entrada=respuesta.usage.prompt_tokens,
            tokens_salida=respuesta.usage.completion_tokens,
            tokens_cache=getattr(detalles, "cached_tokens", 0) or 0,
            crudo=eleccion,
        )

    def continuar(self, mensajes, salida, resultados):
        mensajes.append({
            "role": "assistant",
            "content": salida.texto or None,
            "tool_calls": [
                {"id": llamada.id, "type": "function",
                 "function": {"name": llamada.nombre,
                              "arguments": json.dumps(llamada.argumentos, ensure_ascii=False)}}
                for llamada in salida.llamadas
            ],
        })
        # Cada resultado es un mensaje aparte, no bloques dentro de uno.
        mensajes += [
            {"role": "tool", "tool_call_id": llamada.id,
             "content": json.dumps(resultado, ensure_ascii=False, default=str)}
            for llamada, resultado in resultados
        ]
        return mensajes


def crear(proveedor: str, api_key: str, modelo: str) -> Proveedor:
    if proveedor == "anthropic":
        return ProveedorAnthropic(api_key, modelo)
    if proveedor == "openai":
        return ProveedorOpenAI(api_key, modelo)
    raise ValueError(f"proveedor de modelo desconocido: {proveedor!r}")
