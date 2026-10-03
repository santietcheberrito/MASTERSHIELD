"""El formato de los mensajes, la lista de precios y el contexto de cada turno.

Desde el 15/9/2026 el modelo es el unico que escribe la conversacion: recibe las
respuestas por situacion de la base y el codigo solo manda tal cual la lista de
precios, las fichas y el video. Lo que se prueba aca es lo que queda del lado del
codigo: como se arma lo que recibe el modelo y los controles sobre lo que escribe.
"""

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from app import contexto, humanizacion, textos
from app.agente import herramientas, loop
from app.precios import informar_precios
from app.telefono import para_mostrar

RAIZ = Path(__file__).resolve().parent.parent


def _script_de_carga():
    spec = importlib.util.spec_from_file_location(
        "cargar_conocimiento", RAIZ / "scripts" / "cargar_conocimiento.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _como_lo_devuelve_la_herramienta(id_producto, zona):
    r = informar_precios(id_producto, zona, hoy=date(2026, 9, 11))
    return {
        "tipo": r.tipo,
        "descuento_pago_contado_pct": r.descuento_pago_contado,
        "calidades": [
            {"garantia_anios": c.garantia_anios,
             "precio_normal_m2_sin_iva": c.precio_normal,
             "precio_especial_m2_sin_iva": c.precio_especial,
             "vida_util_hasta_anios": c.vida_util_hasta_anios}
            for c in r.calidades
        ],
    }


# --- la lista de precios ------------------------------------------------------

def test_la_lista_de_dos_calidades_tiene_el_formato_del_cliente():
    lista = textos.lista_de_precios(
        _como_lo_devuelve_la_herramienta("control_solar_ventanas", "quito_y_valles"))
    assert lista.startswith(contexto.INICIO_DE_LA_LISTA)
    assert "1⃣ *Calidad 10 Años de Garantía*  (Hasta 15 años de vida útil)" in lista
    assert "*Precio especial SEPTIEMBRE*: $37+iva por m2" in lista
    assert "Precio normal: $32+iva por m2" in lista
    assert lista.endswith(
        "▪ Puede cancelar con tarjeta de crédito sin recargo adicional.\n"
        "▪ Pagos en efectivo/transferencia tienen un 10% de descuento adicional.")


def test_cada_zona_lleva_su_propio_precio():
    """Desde el 29/9/2026 no hay recargo plano: en la zona verde la calidad de
    10 años cuesta 55, no 42 + 10."""
    lista = textos.lista_de_precios(
        _como_lo_devuelve_la_herramienta("privacidad_arquitectonica", "zona_verde"))
    assert "Precio normal: $55+iva por m2" in lista
    assert "Precio normal: $45+iva por m2" in lista


def test_seguridad_es_una_sola_calidad_y_un_desde():
    lista = textos.lista_de_precios(
        _como_lo_devuelve_la_herramienta("seguridad_arquitectonica", "quito_y_valles"))
    assert "1⃣" not in lista
    assert "Precio desde: $24+iva por m2" in lista


def test_un_emoji_agregado_no_hace_que_un_texto_parezca_nuevo():
    """15/9: la explicacion salio dos veces porque se le agrego un 💰."""
    assert textos.normal("Con gusto le ayudo con los valores. 💰 Para darle") == \
        textos.normal("Con gusto le ayudo con los valores. Para darle")


# --- los globos de WhatsApp ---------------------------------------------------

def test_una_linea_con_tres_guiones_separa_los_globos():
    assert humanizacion.partir_globos("Muy bien Pablo ✅\n---\n¿Qué desea solucionar?") == [
        "Muy bien Pablo ✅", "¿Qué desea solucionar?"]


def test_el_renglon_libre_queda_dentro_del_globo():
    texto = "Gracias por las fotos, Pablo.\n\nEstos son los valores de este mes:"
    assert humanizacion.partir_globos(texto) == [texto]


def test_mas_de_tres_globos_se_juntan_en_el_ultimo():
    globos = humanizacion.partir_globos("a\n---\nb\n---\nc\n---\nd")
    assert globos == ["a", "b", "c\n\nd"]


def test_un_separador_suelto_no_deja_globos_vacios():
    assert humanizacion.partir_globos("---\nhola\n---\n") == ["hola"]


# --- emojis -------------------------------------------------------------------

def test_un_mensaje_largo_sin_emoji_lleva_uno_del_tema():
    texto = ("Entendido, para su domicilio busca controlar el calor. Le cuento que en Quito "
             "nuestro mínimo de instalación es de 5 m².")
    assert humanizacion.con_emoji(texto) != texto


def test_un_mensaje_corto_no_lleva_emoji():
    assert humanizacion.con_emoji("Mucho gusto, Santiago.") == "Mucho gusto, Santiago."


def test_si_ya_trae_emoji_no_se_agrega_otro():
    texto = "Muy bien Pablo, registramos Quito como su ubicación✅ y seguimos con lo que necesita para su casa."
    assert humanizacion.con_emoji(texto) == texto


# --- repetidos, renglon libre y precios ----------------------------------------

def test_una_oracion_que_ya_dijo_no_se_repite():
    previo = "Las láminas no reducen el ruido exterior; eso depende de la ventana."
    texto = "Las láminas no reducen el ruido exterior, eso depende de la ventana. ¿Desea continuar?"
    assert loop.lineas_nuevas(texto, [previo]) == "¿Desea continuar?"


def test_un_asentimiento_corto_no_se_saca():
    """Simulacion del 15/9: 'Perfecto Pablo.' se sacaba por parecerse a un
    'Perfecto, Pablo 📞' anterior y el cierre arrancaba de golpe."""
    texto = "Perfecto Pablo. Un asesor de MS se pondrá en contacto a la brevedad posible."
    assert loop.lineas_nuevas(texto, ["Perfecto, Pablo 📞\n\n¿Lo llamamos a este número?"]) == texto


def test_no_agradece_dos_veces_lo_que_ya_dijo_en_la_introduccion():
    """Pablo, 23/9/2026: "Gracias por las fotos, Daniela 📸" salio como linea
    que presenta los precios y otra vez en el mensaje siguiente. Es corta, y
    las cortas estaban exentas del filtro para no comerse los asentimientos."""
    intro = "Gracias por las fotos, Daniela 📸 Estos son los valores de este mes:"
    texto = "Gracias por las fotos, Daniela 📸\n\nEl siguiente paso es la visita técnica."

    limpio = loop.lineas_nuevas(texto, [], [intro])

    assert limpio == "El siguiente paso es la visita técnica."


def test_un_asentimiento_corto_contra_un_turno_viejo_sigue_pasando():
    """La excepcion de las frases cortas sigue valiendo para lo ya enviado: sin
    ella, el cierre arrancaba de golpe."""
    texto = "Perfecto Pablo. Un asesor de MS se pondrá en contacto a la brevedad."

    assert loop.lineas_nuevas(texto, ["Perfecto, Pablo 📞"]) == texto


def test_si_se_saca_la_pregunta_el_mensaje_la_conserva():
    """Simulacion del 15/9: pidio el precio despues de la bienvenida y la
    respuesta decia que hacian falta datos sin preguntar ninguno."""
    bienvenida = "¿Con quién tengo el gusto y desde qué ciudad nos escribe?"
    texto = "Con gusto le ayudo con los valores, necesito algunos datos.\n---\n" + bienvenida
    limpio = loop.lineas_nuevas(texto, [bienvenida])
    assert limpio.endswith(bienvenida)


def test_la_misma_respuesta_dos_veces_en_un_turno_sale_una_vez():
    texto = (
        "Las láminas no reducen el ruido exterior; eso depende de la estructura de la ventana.\n"
        "Las láminas no reducen el ruido exterior, eso depende de la estructura de la ventana. "
        "Quedo a las órdenes."
    )
    assert loop.lineas_nuevas(texto, []) == (
        "Las láminas no reducen el ruido exterior; eso depende de la estructura de la ventana.\n"
        "Quedo a las órdenes.")


def test_el_renglon_libre_y_el_separador_se_conservan():
    texto = "Mucho gusto, Pablo.\n\n¿Desde qué ciudad nos escribe?\n---\nOtra cosa."
    assert loop.lineas_nuevas(texto, []) == texto


def test_si_todo_repite_queda_la_ultima_linea():
    previo = "¿Con quién tengo el gusto y desde qué ciudad nos escribe?"
    assert loop.lineas_nuevas(previo, [previo]) == previo


def test_despues_de_asentir_queda_un_renglon_libre():
    assert loop.con_respiro("Gracias por las fotos, Pablo. Estos son los valores de este mes:") == (
        "Gracias por las fotos, Pablo.\n\nEstos son los valores de este mes:")


def test_un_precio_inventado_se_saca_y_el_resto_sale():
    """15/9: el modelo escribio 47 y 35 de memoria y se reemplazo el mensaje
    entero por uno generico que pedia el telefono."""
    texto = "Perfecto, Pablo.\n\nEn Manta la privacidad está en 47 dólares más IVA el metro."
    assert loop.sin_precios_no_autorizados(texto, set()) == "Perfecto, Pablo."


def test_con_la_lista_oficial_se_saca_la_lista_que_reescribe_el_modelo():
    """Simulacion del 15/9: el modelo copio los precios debajo de la lista."""
    texto = (
        "Muy bien, unos 12 m² entonces 📏. Le comparto la lista de precios para ventanas 💰\n\n"
        "Lámina de Control Solar para Ventanas\n"
        "- Garantía 10 años: 42 por m² (más IVA). Precio especial 37 por m² (más IVA).\n"
        "Incluye: material, mano de obra de instalación.\n"
        "Descuento por pago de contado: 10%.\n"
        "---\n"
        "Para poder darle un presupuesto cerrado, el siguiente paso es una llamada con un "
        "asesor de MasterShield. ¿Desea continuar? 📞"
    )
    limpio = loop.sin_la_lista_reescrita(texto)
    assert "42" not in limpio and "Incluye" not in limpio and "comparto" not in limpio
    assert limpio.startswith("Muy bien, unos 12 m² entonces 📏.")
    assert limpio.endswith("¿Desea continuar? 📞")


def test_sin_precios_el_texto_queda_igual():
    texto = "Perfecto, Pablo.\n---\n¿Desea continuar con la llamada? 📞"
    assert loop.sin_la_lista_reescrita(texto) == texto


@pytest.mark.parametrize("etapa, incluye", [
    ("referencia", "precio"), ("precio", "llamada"), ("superficie", "referencia"),
])
def test_el_contexto_trae_tambien_el_paso_siguiente(etapa, incluye):
    assert incluye in contexto.PASO_SIGUIENTE[etapa]


# --- telefono y pedido de precio ---------------------------------------------

@pytest.mark.parametrize("e164, local", [
    ("+593999772230", "0999772230"), ("+5491160074604", "+5491160074604"), (None, None),
])
def test_el_telefono_se_muestra_como_en_ecuador(e164, local):
    assert para_mostrar(e164) == local


@pytest.mark.parametrize("texto, pide", [
    ("Solo deseo saber el precio", True), ("cuánto cuesta?", True), ("Precios", True),
    ("Pablo de Quito", False), ("10 metros", False),
])
def test_reconoce_cuando_piden_precio(texto, pide):
    assert herramientas.pide_precio(texto) is pide


# --- el paso de la conversacion y el contexto --------------------------------

@pytest.mark.parametrize("faltan, lista, cerrada, etapa", [
    (["nombre", "zona"], False, False, "nombre_ciudad"),
    (["linea", "objetivo"], False, False, "pedido"),
    (["superficie", "referencia"], False, False, "superficie"),
    (["referencia"], False, False, "referencia"),
    ([], False, False, "precio"),
    ([], True, False, "llamada"),
    ([], True, True, "despues_del_cierre"),
])
def test_el_paso_sale_de_los_datos(faltan, lista, cerrada, etapa):
    assert contexto.etapa_segun(faltan, lista, cerrada) == etapa


def test_el_contexto_trae_las_respuestas_y_la_informacion():
    estado = contexto.Estado("pedido", ["objetivo"], apurada=False, ya_cerrada=False)
    bloque = contexto.formatear(
        estado,
        [{"clave": "pedir_pedido", "situacion": "Falta el pedido.",
          "respuesta": "¿Qué desea solucionar?", "instrucciones": ""}],
        [{"titulo": "Lo que el material NO hace", "contenido": "No reduce el ruido."}],
        "0999772230",
    )
    assert "Paso actual: Saber qué quiere resolver" in bloque
    assert "## pedir_pedido" in bloque
    assert "Responda:\n¿Qué desea solucionar?" in bloque
    assert "No reduce el ruido." in bloque
    assert "0999772230" in bloque


@pytest.mark.parametrize("adjunto, clave", [
    ("imagen", "recibio_fotos"), ("video", "recibio_un_video"), ("", None),
])
def test_una_foto_o_un_video_traen_su_respuesta(adjunto, clave):
    """La foto completa la referencia y el paso salta a los precios: sin esto la
    respuesta que la agradece se quedaba afuera (simulacion del 15/9/2026)."""
    estado = contexto.Estado("precio", [], apurada=False, ya_cerrada=False, adjunto=adjunto)
    extra = contexto.claves_extra(estado)
    assert (clave in extra) if clave else extra == []


def test_en_modo_rapido_y_con_foto_entran_las_dos():
    estado = contexto.Estado("precio", [], apurada=True, ya_cerrada=False, adjunto="imagen")
    assert set(contexto.claves_extra(estado)) == {"modo_rapido", "precio_sin_datos",
                                                  "recibio_fotos"}


def test_el_contexto_avisa_que_llego_una_foto():
    estado = contexto.Estado("precio", [], apurada=False, ya_cerrada=False, adjunto="imagen")
    assert "Acaba de enviar una foto" in contexto.formatear(estado, [], [], None)


def test_el_numero_de_otro_pais_va_tal_cual_al_contexto():
    """16/9/2026: a un numero argentino el agente le puso un cero adelante y le
    saco el +, porque el contexto decia "como se escribe en Ecuador"."""
    estado = contexto.Estado("llamada", [], apurada=False, ya_cerrada=False)
    bloque = contexto.formatear(estado, [], [], "+5491160074604")
    assert "+5491160074604" in bloque
    assert "tal cual" in bloque
    assert "como se escribe en Ecuador" not in bloque


@pytest.mark.parametrize("zona, minimo", [
    ("quito_y_valles", 5), ("pichincha_cercana", 10), ("zona_azul", 15),
    ("zona_verde", 20), ("zona_roja", 25), (None, None), ("inventada", None),
])
def test_el_minimo_sale_de_la_zona(zona, minimo):
    assert contexto.minimo_de(zona) == minimo


def test_el_contexto_trae_el_minimo_de_instalacion():
    """29/9/2026: el agente escribio "el mínimo es de {minimo} m²" tal cual.
    El minimo se dice ANTES de pedir los metros, y ahi todavia no se llamo a
    consultar_precio: si no esta en el contexto, no tiene con que completarlo."""
    estado = contexto.Estado("referencia", ["referencia"], apurada=False, ya_cerrada=False)
    bloque = contexto.formatear(estado, [], [], None, minimo_m2=20)
    assert "Mínimo de instalación en esta zona: 20 m²" in bloque


def test_sin_zona_no_se_habla_del_minimo():
    estado = contexto.Estado("nombre_ciudad", ["zona"], apurada=False, ya_cerrada=False)
    assert "Mínimo" not in contexto.formatear(estado, [], [], None)


def test_la_plantilla_de_los_metros_dice_de_donde_sale_el_minimo():
    respuestas = {r["clave"]: r for r in _script_de_carga().leer_respuestas()}
    instr = respuestas["pedir_metros"]["instrucciones"]
    assert "Minimo de instalacion en esta zona" in instr
    assert "{minimo}" in respuestas["pedir_metros"]["respuesta"]


def test_el_contexto_dice_cuando_lo_llama_el_asesor():
    """La oficina atiende de lunes a viernes: el agente no puede prometer una
    llamada un sabado a la noche (15/9/2026)."""
    estado = contexto.Estado("llamada", [], apurada=False, ya_cerrada=False)
    bloque = contexto.formatear(estado, [], [], None, "el lunes")
    assert "Cuándo lo llama el asesor" in bloque
    assert "el lunes" in bloque


def test_sin_ese_dato_el_contexto_no_lo_inventa():
    estado = contexto.Estado("llamada", [], apurada=False, ya_cerrada=False)
    assert "Cuándo lo llama" not in contexto.formatear(estado, [], [], None)


def test_en_modo_rapido_el_contexto_lo_dice():
    estado = contexto.Estado("nombre_ciudad", ["zona"], apurada=True, ya_cerrada=False)
    assert "Modo rápido" in contexto.formatear(estado, [], [], None)


# --- la fuente de las respuestas ---------------------------------------------

def test_las_respuestas_se_leen_y_estan_completas():
    carga = _script_de_carga()
    respuestas = carga.leer_respuestas()
    claves = {r["clave"] for r in respuestas}
    for clave in ("dio_nombre_y_ciudad", "pedir_superficie", "pedir_metros", "dar_precios",
                  "precio_sin_datos", "modo_rapido", "acepto_la_llamada",
                  "confirmo_el_numero", "despedida_final"):
        assert clave in claves, clave


def test_los_textos_del_cliente_estan_tal_cual():
    respuestas = {r["clave"]: r for r in _script_de_carga().leer_respuestas()}
    assert "registramos {ciudad} como su ubicación✅" in respuestas["dio_nombre_y_ciudad"]["respuesta"]
    # El texto de la visita, tal como lo escribio MasterShield (23/9/2026).
    oferta = respuestas["dar_precios"]["respuesta"]
    assert "agendar una *visita técnica* en el lugar de instalación ✅" in oferta
    assert "En ese mismo momento le entregaremos el presupuesto exacto" in oferta
    assert "¿Desearía agendar esta *visita técnica*?" in oferta
    assert "---" in oferta, "son dos mensajes, no uno"
    assert oferta == respuestas["ofrecer_llamada"]["respuesta"], "el mismo texto en los dos casos"
    assert "Eso sería todo entonces. Nos comunicaremos pronto 👌" in respuestas["despedida_final"]["respuesta"]
    assert "en qué ciudad necesita la instalación" in respuestas["pedir_nombre_y_ciudad"]["respuesta"]
    assert "asesor MS" in respuestas["recibio_fotos"]["respuesta"], "las fotos se las lleva el asesor"
    assert "asesor MS" in respuestas["recibio_un_video"]["respuesta"]
    assert "{cuando}" in respuestas["confirmo_el_numero"]["respuesta"], "el dia sale del horario"
    # El numero ya viene formateado: pedirle que lo reescriba lo rompe.
    assert "TAL CUAL" in respuestas["acepto_la_llamada"]["instrucciones"]
    assert "cero adelante, nunca" not in respuestas["acepto_la_llamada"]["instrucciones"]


def test_la_ciudad_se_guarda_con_su_nombre():
    """16/9/2026: dijo "Cuenca", el agente guardo zona=otra_ciudad —que es una
    categoria de precio, no un lugar— y volvio a preguntarle la ciudad."""
    import yaml
    campos = yaml.safe_load((RAIZ / "config" / "calificacion.yaml").read_text(
        encoding="utf-8"))["campos"]
    assert campos["ciudad"]["tipo"] == "texto"
    assert campos["zona"]["valores"] == [
        "quito_y_valles", "pichincha_cercana", "zona_azul", "zona_verde", "zona_roja",
        "galapagos", "fuera_del_pais"]


def test_la_plantilla_pide_guardar_ciudad_y_zona():
    respuestas = {r["clave"]: r for r in _script_de_carga().leer_respuestas()}
    instr = respuestas["dio_nombre_y_ciudad"]["instrucciones"]
    assert "ciudad con el nombre tal como lo dijo" in instr
    assert "la zona de precios la deduce el sistema" in instr
    # Con la ciudad ya sabida, pedirla de nuevo es el bug que se arreglo.
    assert "no la vuelva a preguntar" in respuestas["pedir_ciudad"]["situacion"]


# --- que se ofrece al cerrar, segun la zona ----------------------------------
# En Quito y sus valles la visita tecnica es gratis y esta cerca; fuera de Quito
# se ofrece la llamada y el asesor ve si la visita corresponde (29/9/2026).

@pytest.mark.parametrize("zona, clave, entra", [
    ("quito_y_valles", "dar_precios", True),
    ("quito_y_valles", "dar_precios_fuera_de_quito", False),
    ("zona_verde", "dar_precios", False),
    ("zona_verde", "dar_precios_fuera_de_quito", True),
    ("pichincha_cercana", "dar_precios_fuera_de_quito", True),
    ("zona_roja", "ofrecer_llamada", False),
])
def test_cada_zona_recibe_solo_su_oferta(zona, clave, entra):
    respuestas = {r["clave"]: r for r in _script_de_carga().leer_respuestas()}
    assert contexto.corresponde_a_la_zona(respuestas[clave], zona) is entra


def test_una_respuesta_sin_zonas_sirve_en_todas():
    assert contexto.corresponde_a_la_zona({"clave": "despedida_final"}, "zona_roja") is True
    assert contexto.corresponde_a_la_zona({"clave": "x", "solo_zonas": None}, "") is True


def test_sin_zona_no_se_ofrece_la_visita():
    """Todavia no dijo la ciudad: no se le puede prometer una visita gratis."""
    respuestas = {r["clave"]: r for r in _script_de_carga().leer_respuestas()}
    assert contexto.corresponde_a_la_zona(respuestas["dar_precios"], "") is False


def test_fuera_de_quito_no_se_promete_la_visita_gratis():
    """Prometerla en Loja es prometer algo que la empresa no sostiene."""
    respuestas = {r["clave"]: r for r in _script_de_carga().leer_respuestas()}
    fuera = respuestas["dar_precios_fuera_de_quito"]
    assert "visita" not in fuera["respuesta"].lower()
    assert "sin costo" not in fuera["respuesta"].lower()
    assert "llamada con un asesor de MasterShield" in fuera["respuesta"]
    # Y la de Quito sigue ofreciendo la visita, con el texto del cliente.
    assert "*visita técnica*" in respuestas["dar_precios"]["respuesta"]


def test_las_zonas_de_las_plantillas_existen():
    """Una zona mal escrita deja al agente sin nada que ofrecer al cerrar."""
    import yaml
    validas = set(yaml.safe_load(
        (RAIZ / "config" / "productos.yaml").read_text(encoding="utf-8"))["zonas"])
    for r in _script_de_carga().leer_respuestas():
        for zona in r.get("solo_zonas") or []:
            assert zona in validas, f"{r['clave']} apunta a una zona que no existe: {zona}"


def test_el_conocimiento_se_parte_por_seccion():
    partes = _script_de_carga().fragmentos()
    titulos = [p["titulo"] for p in partes]
    assert "Lo que el material NO hace" in titulos
    assert next(p for p in partes if p["titulo"] == "Lo que el material NO hace")["prioridad"] == 2


def test_el_prompt_es_corto():
    """Eran unos 12.600 tokens por llamada. Lo que depende del caso va en la base."""
    assert len((RAIZ / "prompts" / "agente.md").read_text(encoding="utf-8")) < 8000
