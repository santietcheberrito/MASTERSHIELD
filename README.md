# Agente de calificación WhatsApp → Kommo

Atiende consultas entrantes de WhatsApp, responde preguntas iniciales sobre
productos, releva datos comerciales, asigna un puntaje de calificación y carga
todo en Kommo CRM para que un vendedor llame.

## Puesta en marcha

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env   # completar
```

## Tests

```bash
.venv/bin/pytest
```

Los tests marcados con `db` necesitan una base PostgreSQL real; se corren
poniendo `DATABASE_URL_TEST` en el entorno.

## Estado

Ver [PENDIENTES.md](PENDIENTES.md) para lo que falta y qué bloquea cada cosa.

| Módulo | Estado |
|---|---|
| `app/config.py` | listo, con tests |
| `app/db.py` + `migrations/` | pendiente: falta base para verificar |
| resto | sin empezar |
