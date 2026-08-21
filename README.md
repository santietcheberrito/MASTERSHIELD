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

`GET /health` devuelve 200 si la base responde y 503 si no. El proceso levanta
igual con la base caída, a propósito: así el healthcheck distingue "el deploy
no arrancó" de "la base no responde".

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
| `app/webhook.py` + `app/worker.py` | sesión 2 |
| `app/kommo/` | sesión 3 |
| `app/agente/` | sesión 4 |
| `app/scoring.py` | sesión 5, requiere kick off |
| `app/whatsapp/` | sesión 6, requiere kick off |
| deploy y `/metricas` | sesión 7 |
