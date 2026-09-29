"""Normalizacion de telefonos a E.164.

Kommo busca el contacto por telefono. Si cada conversacion lo guarda de una
forma distinta —con cero adelante, con guiones, sin codigo de pais— la busqueda
no encuentra nada y se crean contactos duplicados para la misma persona.

La normalizacion es conservadora a proposito: ante la duda devuelve None y el
numero crudo queda igual en `datos`, para que una persona lo mire. Un telefono
mal normalizado es peor que uno sin normalizar: el vendedor llama a otro lado.

Las reglas de largo son las de Ecuador, que es donde opera el cliente. El
prefijo se toma de la configuracion y define el codigo que se antepone, pero
cambiarlo no convierte a este modulo en generico: para otro pais habria que
escribir sus largos validos.
"""

from __future__ import annotations

import re

# Largos validos en Ecuador, sin el prefijo internacional:
#   celular  9XXXXXXXX  (9 digitos, empieza en 9)
#   fijo     XXXXXXXX   (8 digitos, empieza en el codigo de provincia 2-7)
_CELULAR_EC = re.compile(r"^9\d{8}$")
_FIJO_EC = re.compile(r"^[2-7]\d{7}$")

# E.164 admite hasta 15 digitos incluyendo el codigo de pais, y ningun numero
# real tiene menos de 8. Sirve para descartar basura que empieza con "00".
_LARGO_INTERNACIONAL = range(8, 16)


def normalizar(crudo: str | None, prefijo: str = "+593") -> str | None:
    """Devuelve el numero en E.164, o None si no se puede afirmar cual es."""
    if not crudo:
        return None

    texto = crudo.strip()
    internacional = texto.startswith("+") or texto.startswith("00")
    digitos = re.sub(r"\D", "", texto)
    if not digitos:
        return None

    if internacional:
        # Ya vino con codigo de pais: se respeta, sea de donde sea.
        if texto.startswith("00"):
            digitos = digitos[2:]
        # Sin este chequeo, "0000" entraba por aca y salia como "+00".
        return f"+{digitos}" if len(digitos) in _LARGO_INTERNACIONAL else None

    codigo = prefijo.lstrip("+")

    # Escrito sin el "+" pero con el codigo de pais adelante.
    if digitos.startswith(codigo):
        resto = digitos[len(codigo):]
        if _CELULAR_EC.match(resto) or _FIJO_EC.match(resto):
            return f"+{codigo}{resto}"

    # Formato local: el cero inicial se reemplaza por el codigo de pais.
    if digitos.startswith("0"):
        resto = digitos[1:]
        if _CELULAR_EC.match(resto) or _FIJO_EC.match(resto):
            return f"+{codigo}{resto}"
        return None

    if _CELULAR_EC.match(digitos) or _FIJO_EC.match(digitos):
        return f"+{codigo}{digitos}"

    # No se parece a un numero ecuatoriano y no trae codigo de pais. Antes que
    # inventarle uno, se deja sin normalizar para que alguien lo revise.
    return None


def para_mostrar(e164: str | None, prefijo: str = "+593") -> str | None:
    """El numero como lo escribe una persona en Ecuador: 0999772230.

    E.164 es para la base y para Kommo. Decirle "+593999772230" a alguien de
    Quito suena a maquina leyendo un campo. Un numero de otro pais sale tal cual.
    """
    if not e164:
        return None
    if e164.startswith(prefijo):
        resto = e164[len(prefijo):]
        if _CELULAR_EC.match(resto) or _FIJO_EC.match(resto):
            return f"0{resto}"
    return e164
