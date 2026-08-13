# Pendientes

Cosas que bloquean módulos y no se pueden inventar. Se van tachando a medida
que aparecen los datos.

## Bloqueantes

- [ ] **Verificar las migraciones contra una base real.** En la máquina de
  desarrollo no hay Postgres, ni psql, ni Docker, y `catalogo` necesita la
  extensión `pgvector`. Hasta resolverlo no se escriben `migrations/` ni
  `db.py`: quedarían sin forma de verificarse. Opciones: `brew install
  postgresql@16 pgvector` local, o la `DATABASE_URL` de Supabase.
- [ ] **`DATABASE_URL` de Supabase.** Bloquea `db.py`, migraciones, worker,
  buffer de mensajes y todo lo que persiste.
- [ ] **Qué BSP de WhatsApp se usa** (360dialog, Gupshup, Meta directo, otro).
  Bloquea `webhook.py` (esquema del payload y validación de firma) y
  `whatsapp/cliente.py` (endpoints de envío, indicador de "escribiendo").
- [ ] **Campos a relevar y criterios comerciales** (salen del kick off).
  Bloquean `config/calificacion.yaml`, `scoring.py` y el prompt del agente.
- [ ] **IDs numéricos de Kommo**: pipeline, etapas, campos personalizados de
  contacto y de lead. Bloquean `config/kommo.yaml` y `kommo/sincronizacion.py`.
- [ ] **Credenciales**: `ANTHROPIC_API_KEY`, `KOMMO_ACCESS_TOKEN`,
  `BSP_TOKEN`, `BSP_WEBHOOK_SECRET`.

## Decisiones a confirmar con el cliente

- [ ] A quién y por qué medio se notifica un `escalar_a_humano`.
- [ ] Cómo se detecta que un vendedor contestó manualmente desde el número,
  para pasar la conversación a `pausada` (depende del BSP).

## Módulos que se pueden avanzar sin nada de lo anterior

- `whatsapp/humanizacion.py` — partido de mensajes y cálculo de delays.
  Python puro, se testea entero sin DB ni credenciales.
