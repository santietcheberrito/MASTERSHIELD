# Pendientes

Cosas que bloquean módulos y no se pueden inventar. Se van tachando a medida
que aparecen los datos.

## Bloqueantes

- [ ] **Con qué se generan los embeddings del catálogo.** CLAUDE.md fija
  `vector(1536)` pero no el proveedor, y Anthropic no tiene API de embeddings
  propia. 1536 es el tamaño de `text-embedding-3-small` de OpenAI. Si se usa
  otro proveedor cambia la dimensión y hay que ajustar la migración. Bloquea
  `agente/catalogo.py` (sesión 4), no la sesión 1.
- [ ] **Credenciales de la Cloud API de WhatsApp** (Meta directo, ya decidido):
  token, phone number id y app secret para validar `X-Hub-Signature-256`.
  Bloquean el adaptador de WhatsApp. La costura ya está: alcanza con un
  `parsear()` en `app/canales/` que devuelva un `MensajeEntrante`.
- [ ] **Campos a relevar y criterios comerciales** (salen del kick off).
  Bloquean `config/calificacion.yaml`, `scoring.py` y el prompt del agente.
- [ ] **IDs numéricos de Kommo**: pipeline, etapas, campos personalizados de
  contacto y de lead. Bloquean `config/kommo.yaml` y `kommo/sincronizacion.py`.
- [ ] **Credenciales**: `ANTHROPIC_API_KEY`, `KOMMO_ACCESS_TOKEN`,
  `BSP_TOKEN`, `BSP_WEBHOOK_SECRET`.

## Resueltos

- [x] **`DATABASE_URL` de Supabase.** Proyecto en `us-west-2`. Se conecta por el
  Session pooler (`aws-0-us-west-2.pooler.supabase.com:5432`, usuario
  `postgres.<ref>`); el host directo solo tiene IPv6 y no resuelve desde acá.
  Migración `001_inicial.sql` aplicada, pgvector 0.8.2.

## Calibración pendiente

- [ ] **`VENTANA_BUFFER_SEG`**. Está en 8. En dos pruebas reales los mensajes
  llegaron con huecos de 9.1s y 8.9s, o sea justo en el borde. Subirla evita
  cortar ráfagas pero retrasa toda respuesta, incluso la de un mensaje suelto.
  El número sale de las 20 conversaciones de prueba con el equipo del cliente.
- [ ] **Supersesión de respuestas (sesión 6).** Si llega un mensaje mientras el
  agente redacta, hay que descartar esa respuesta y rehacer el turno. Es el
  arreglo bueno del problema de arriba: con supersesión, una ventana corta deja
  de ser riesgosa. El worker ya detecta el caso y vuelve a encolar.
- [ ] **`/start` de Telegram** llega como un mensaje de texto cualquiera. El
  prompt de la sesión 4 tiene que tratarlo como saludo inicial y no responderlo
  literalmente.

## Decisiones a confirmar con el cliente

- [ ] A quién y por qué medio se notifica un `escalar_a_humano`.
- [ ] Cómo se detecta que un vendedor contestó manualmente desde el número,
  para pasar la conversación a `pausada` (depende del BSP). El esquema ya lo
  contempla: `mensajes.rol = 'vendedor'`.
- [ ] Horario de atención real, en hora de Ecuador. Por ahora 09:00–18:00 de
  lunes a viernes, que es un default, no un dato del cliente.
- [ ] Manejo del riesgo de rotura térmica (ver CLAUDE.md).

## Módulos que se pueden avanzar sin nada de lo anterior

- `whatsapp/humanizacion.py` — partido de mensajes y cálculo de delays.
  Python puro, se testea entero sin DB ni credenciales.
