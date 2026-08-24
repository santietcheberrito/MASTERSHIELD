# Agente de calificación WhatsApp → Kommo

Atiende consultas entrantes de WhatsApp, responde preguntas iniciales sobre
productos, releva datos comerciales, asigna un puntaje de calificación y carga
todo en Kommo CRM para que un asesor llame.

Cliente: **MasterShield**, Quito, Ecuador. Las reglas del proyecto están en
[CLAUDE.md](CLAUDE.md).

## Puesta en marcha

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env   # completar, como mínimo DATABASE_URL
```

## Conexión a Supabase

El host directo `db.<ref>.supabase.co` **solo publica registro AAAA**: si la red
no tiene IPv6, no resuelve. Hay que usar el pooler:

| Uso | Endpoint |
|---|---|
| Migraciones y desarrollo | Session pooler, puerto **5432** |
| Servicio en Railway | Transaction pooler, puerto **6543** |

En el pooler el usuario es `postgres.<ref>`, no `postgres`. En el puerto 6543
`db.py` desactiva solo el cache de prepared statements de asyncpg, que ahí
rompe con `prepared statement _pg_N already exists`.

## Migraciones

```bash
.venv/bin/python scripts/migrar.py --estado   # qué está aplicado
.venv/bin/python scripts/migrar.py            # aplica lo pendiente
```

Cada archivo de `migrations/` se aplica una sola vez, dentro de una
transacción, y queda registrado en la tabla `migraciones`.

## Correr el servicio

```bash
.venv/bin/uvicorn app.main:app --reload
```

### Canales

Telegram es el canal de la etapa de pruebas; WhatsApp Cloud API entra después.
`TELEGRAM_MODO` decide cómo se reciben los mensajes:

- `polling` — `getUpdates`, sin URL pública. Es lo cómodo para desarrollar.
- `webhook` — Telegram pega en `POST /webhook/telegram`. Necesita URL pública y
  `TELEGRAM_WEBHOOK_SECRET`, el mismo valor que se registró con `setWebhook`.
- `off` — no se escucha Telegram.

No se pueden usar los dos a la vez: con un webhook registrado, Telegram rechaza
`getUpdates`.

`GET /health` devuelve 200 si la base responde y 503 si no. El proceso levanta
igual con la base caída, a propósito: así el healthcheck distingue "el deploy
no arrancó" de "la base no responde".

## Conversar con el agente

```bash
.venv/bin/python scripts/simular_conversacion.py --reiniciar
```

Usa la base real y las mismas tablas que una conversación de verdad. Después de
cada respuesta muestra qué herramientas se llamaron y cómo quedaron los datos
relevados, que es lo que hace falta para ver si está entendiendo.

## Tests

```bash
.venv/bin/pytest
```

Los tests marcados con `db` necesitan una base PostgreSQL real con las
migraciones aplicadas. Se saltean solos, con el motivo a la vista, si no hay
`DATABASE_URL` o si la base no es alcanzable; en cambio una clave mal puesta
**falla**, no se saltea. Todos corren dentro de una transacción que se
revierte, así que no dejan nada escrito ni contra la base del cliente.

Tardan ~90s porque cada consulta va hasta `us-west-2`. Para iterar rápido en
lo que no toca la base: `.venv/bin/pytest -m "not db"`.

Para apuntarlos a otra base sin tocar `.env`:

```bash
DATABASE_URL_TEST=postgresql://... .venv/bin/pytest -m db
```

## Estado

Ver [PENDIENTES.md](PENDIENTES.md) para lo que falta y qué bloquea cada cosa.

| Módulo | Estado |
|---|---|
| `app/config.py` | listo, con tests |
| `app/db.py` | listo, con tests |
| `migrations/001_inicial.sql` + `scripts/migrar.py` | listo, aplicado contra Supabase |
| `app/main.py` (`/health`) | listo, con tests |
| `app/webhook.py` + `app/worker.py` + `app/ingesta.py` | listo, probado con Telegram real |
| `app/canales/` (Telegram) | listo, con tests |
| `app/canales/` (WhatsApp Cloud API) | falta: sin credenciales de Meta |
| `app/kommo/` | sesión 3 |
| `app/agente/` | listo, probado contra el modelo real |
| `app/precios.py` + `config/productos.yaml` | listo, con la lista real del cliente |
| `prompts/` | provisorio: falta la revisión de MasterShield |
| `app/scoring.py` | sesión 5, requiere kick off |
| `app/whatsapp/` | sesión 6, requiere kick off |
| deploy y `/metricas` | sesión 7 |
