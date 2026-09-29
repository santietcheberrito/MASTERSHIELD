from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.config import Settings, obtener_settings

URL = "postgresql://usuario:clave@host:5432/base"


def armar(**extra) -> Settings:
    """Settings sin leer .env, para que el test no dependa del disco."""
    return Settings(_env_file=None, database_url=URL, **extra)


def test_valores_por_defecto():
    s = armar()
    # 4 a 6 desde el 15/9/2026, con el prompt corto y el modelo en "low": junta
    # los mensajes seguidos y la respuesta queda cerca de los 20 segundos.
    assert s.demora_respuesta_min_seg == 4
    assert s.demora_respuesta_max_seg == 6
    assert s.horario_atencion == "08:00-17:00"
    assert s.tz == "America/Guayaquil"
    assert s.pais == "EC"
    assert s.prefijo_telefonico == "+593"
    assert s.log_level == "INFO"


def test_lee_del_entorno(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("DEMORA_RESPUESTA_MIN_SEG", "10")
    monkeypatch.setenv("DEMORA_RESPUESTA_MAX_SEG", "15")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    s = Settings(_env_file=None)
    assert s.demora_respuesta_min_seg == 10
    assert s.log_level == "DEBUG"


def test_database_url_es_obligatoria():
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("valor", ["mysql://a:b@host/base", "host:5432/base", ""])
def test_database_url_invalida(valor):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=valor)


@pytest.mark.parametrize(
    "valor",
    [
        # Los corchetes del placeholder [YOUR-PASSWORD] que quedaron pegados.
        "postgresql://postgres[clave123!]@db.abc.supabase.co:5432/postgres",
        "postgresql://postgres:[clave123!]@db.abc.supabase.co:5432/postgres",
    ],
)
def test_database_url_con_corchetes_del_placeholder(valor):
    """Pasa el chequeo del esquema pero rompe urlparse con un error sobre IPv6
    que no tiene nada que ver. Tiene que fallar aca y con un mensaje util."""
    with pytest.raises(ValidationError, match="corchetes"):
        Settings(_env_file=None, database_url=valor)


def test_database_url_sin_host():
    with pytest.raises(ValidationError, match="host"):
        Settings(_env_file=None, database_url="postgresql:///base")


@pytest.mark.parametrize("valor", [-1, 601])
def test_demora_fuera_de_rango(valor):
    with pytest.raises(ValidationError):
        armar(demora_respuesta_min_seg=valor)


def test_la_demora_puede_ser_cero():
    s = armar(demora_respuesta_min_seg=0, demora_respuesta_max_seg=0)
    assert s.demora_respuesta_min_seg == s.demora_respuesta_max_seg == 0


def test_la_demora_minima_no_puede_superar_a_la_maxima():
    with pytest.raises(ValidationError):
        armar(demora_respuesta_min_seg=200, demora_respuesta_max_seg=100)


@pytest.mark.parametrize(
    "valor",
    ["9:00-18:00", "09:00 - 18:00", "09:00", "25:00-26:00", "09:60-18:00", ""],
)
def test_horario_mal_formado(valor):
    with pytest.raises(ValidationError):
        armar(horario_atencion=valor)


def test_horario_invertido():
    with pytest.raises(ValidationError):
        armar(horario_atencion="18:00-09:00")


def test_horario_parseado():
    assert armar(horario_atencion="08:30-17:45").horario == (
        time(8, 30),
        time(17, 45),
    )


def test_log_level_invalido():
    with pytest.raises(ValidationError):
        armar(log_level="VERBOSE")


def test_tz_invalida():
    with pytest.raises(ValidationError):
        armar(tz="America/Buenos_Aires_Inventada")


def test_zona_es_la_de_quito():
    """El cliente es ecuatoriano. Si esto vuelve a Argentina, el horario de
    atencion y el agendado de llamados quedan corridos una hora."""
    assert armar().zona == ZoneInfo("America/Guayaquil")


@pytest.mark.parametrize("valor", ["EC", "ec", "Ec"])
def test_pais_se_normaliza_a_mayusculas(valor):
    assert armar(pais=valor).pais == "EC"


@pytest.mark.parametrize("valor", ["ECU", "E", "", "5", "E1"])
def test_pais_invalido(valor):
    with pytest.raises(ValidationError):
        armar(pais=valor)


@pytest.mark.parametrize("valor", ["+593", "+54", "+1"])
def test_prefijo_valido(valor):
    assert armar(prefijo_telefonico=valor).prefijo_telefonico == valor


@pytest.mark.parametrize("valor", ["593", "+", "+5934", "++593", "+59a", ""])
def test_prefijo_invalido(valor):
    with pytest.raises(ValidationError):
        armar(prefijo_telefonico=valor)


def test_credenciales_faltantes():
    s = armar(anthropic_api_key="sk-test", kommo_subdomain="cliente")
    assert "ANTHROPIC_API_KEY" not in s.credenciales_faltantes
    assert "KOMMO_SUBDOMAIN" not in s.credenciales_faltantes
    assert "WHATSAPP_TOKEN" in s.credenciales_faltantes
    assert "KOMMO_ACCESS_TOKEN" in s.credenciales_faltantes


def test_sin_credenciales_faltan_todas():
    assert len(armar().credenciales_faltantes) == 9


@pytest.mark.parametrize(
    "momento,esperado",
    [
        # Miercoles 13/08/2025. La oficina abre 08:00 y cierra 17:00.
        (datetime(2025, 8, 13, 8, 0), True),
        (datetime(2025, 8, 13, 13, 30), True),
        (datetime(2025, 8, 13, 16, 59), True),
        (datetime(2025, 8, 13, 17, 0), False),  # el limite superior queda afuera
        (datetime(2025, 8, 13, 7, 59), False),
        (datetime(2025, 8, 13, 3, 0), False),
        # Sabado y domingo
        (datetime(2025, 8, 16, 11, 0), False),
        (datetime(2025, 8, 17, 11, 0), False),
    ],
)
def test_esta_en_horario(momento, esperado):
    s = armar()
    assert s.esta_en_horario(momento.replace(tzinfo=s.zona)) is esperado


def test_esta_en_horario_convierte_zona():
    """Un momento en UTC se evalua contra la hora de Quito (UTC-5).

    El primer caso es el que atrapa una vuelta accidental a la zona argentina:
    a las 12:00 UTC en Ecuador son las 07:00 y todavia no abrieron, pero en
    Buenos Aires serian las 09:00 y daria dentro de horario.
    """
    s = armar()
    assert s.esta_en_horario(datetime(2025, 8, 13, 12, 0, tzinfo=ZoneInfo("UTC"))) is False
    # 15:00 UTC son las 10:00 en Quito: dentro.
    assert s.esta_en_horario(datetime(2025, 8, 13, 15, 0, tzinfo=ZoneInfo("UTC"))) is True
    # 22:00 UTC son las 17:00 en Quito: el limite superior queda afuera.
    assert s.esta_en_horario(datetime(2025, 8, 13, 22, 0, tzinfo=ZoneInfo("UTC"))) is False


def test_obtener_settings_cachea(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    obtener_settings.cache_clear()
    assert obtener_settings() is obtener_settings()
    obtener_settings.cache_clear()


# --- cuando lo llama el asesor ----------------------------------------------
# MasterShield atiende el telefono de lunes a viernes, 08:00 a 17:00 de Ecuador.
# El agente contesta a cualquier hora, pero no puede prometer una llamada que la
# oficina no va a hacer hasta el lunes (15/9/2026).

# Fuera de horario se dice ademas por que: "mañana" a secas deja a la persona
# preguntandose por que no la llaman hoy (pedido de MasterShield, 23/9/2026).
PORQUE = ", porque atendemos de lunes a viernes de 08:00 a 17:00"


@pytest.mark.parametrize("momento, esperado", [
    # Miercoles 26/08/2026
    (datetime(2026, 8, 26, 11, 0), "a la brevedad"),
    (datetime(2026, 8, 26, 8, 0), "a la brevedad"),
    (datetime(2026, 8, 26, 6, 30), "hoy, en cuanto abra la oficina" + PORQUE),
    (datetime(2026, 8, 26, 17, 0), "mañana" + PORQUE),
    (datetime(2026, 8, 26, 22, 30), "mañana" + PORQUE),
    # Viernes 28/08/2026: con la oficina abierta, a la brevedad; cerrada, el lunes
    (datetime(2026, 8, 28, 11, 0), "a la brevedad"),
    (datetime(2026, 8, 28, 17, 30), "el lunes" + PORQUE),
    # Fin de semana
    (datetime(2026, 8, 29, 11, 0), "el lunes" + PORQUE),
    (datetime(2026, 8, 30, 20, 0), "el lunes" + PORQUE),
])
def test_cuando_llaman(momento, esperado):
    s = armar()
    assert s.cuando_llaman(momento.replace(tzinfo=s.zona)) == esperado


def test_dentro_de_horario_no_se_explica_el_horario():
    """Con la oficina abierta, el motivo sobra: llaman ahora."""
    s = armar()
    assert s.cuando_llaman(datetime(2026, 8, 26, 11, 0, tzinfo=s.zona)) == "a la brevedad"


def test_el_horario_que_se_dice_sale_de_la_configuracion():
    """Si MasterShield cambia el horario, cambia el texto: no esta escrito a mano."""
    s = armar(horario_atencion="09:30-18:15")
    assert s.horario_legible == "09:30 a 18:15"
    assert "de 09:30 a 18:15" in s.cuando_llaman(
        datetime(2026, 8, 26, 22, 0, tzinfo=s.zona))


def test_cuando_llaman_usa_la_hora_de_ecuador():
    """22:00 UTC son las 17:00 en Quito: la oficina ya cerro."""
    s = armar()
    assert s.cuando_llaman(
        datetime(2026, 8, 26, 22, 0, tzinfo=ZoneInfo("UTC"))) == "mañana" + PORQUE
