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

- [x] **`VENTANA_BUFFER_SEG`**. Movida a 30s. Con 6 las ráfagas se partían:
  medido con mensajes reales, la gente deja huecos de ~9s entre líneas. Es
  gratis subirla porque entra dentro del retraso de respuesta.
- [ ] **`VENTANA_BUFFER_SEG` final.** Los 30s son una estimación con una sola
  persona probando. El número sale de las 20 conversaciones de prueba con el
  equipo del cliente.
- [ ] **Supersesión de respuestas.** Pasa a ser obligatoria, no opcional: con
  un retraso de 60–120s, que el cliente escriba mientras hay una respuesta
  esperando es lo esperable, no un caso de borde. El worker ya detecta la
  carrera y reencola; falta cancelar la respuesta en vuelo.
- [ ] **`/start` de Telegram** llega como un mensaje de texto cualquiera. El
  prompt de la sesión 4 tiene que tratarlo como saludo inicial y no responderlo
  literalmente.

## Decidido por el cliente, a implementar en la sesión 6

**Retraso de respuesta de 1–2 minutos**, para dar realismo. Parámetros ya
cerrados con el cliente:

| Parámetro | Valor | Nota |
|---|---|---|
| `DEMORA_RESPUESTA_MIN_SEG` | 60 | sorteado por turno, no fijo |
| `DEMORA_RESPUESTA_MAX_SEG` | 120 | |
| Aplica a la primera respuesta | **sí** | decisión explícita del cliente |
| `VENTANA_BUFFER_SEG` | 30 | va **dentro** del retraso, no encima |

Cómo se mide: desde el **último** mensaje del cliente hasta el **primer**
mensaje del agente. La recolección de 30s y el tiempo que tarda el agente en
producir la respuesta se descuentan de ese total; si se sumaran, el peor caso
serían 150s y dejaría de ser "1 a 2 minutos".

El indicador de "escribiendo" va solo en los últimos segundos antes de enviar.

**Riesgo asentado.** Le planteé al cliente que dos minutos de silencio en la
*primera* respuesta es donde más gente se pierde, y que un vendedor real
contesta rápido una vez que tiene el chat abierto. El cliente eligió igual
aplicar el retraso siempre, sin excepción. Queda anotado para poder revisarlo
con datos después de las 20 conversaciones de prueba: si hay abandono en el
primer mensaje, este es el primer parámetro a mirar.

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
