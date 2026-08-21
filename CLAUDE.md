# Agente de calificación WhatsApp → Kommo

Agente de IA que atiende consultas entrantes de WhatsApp, responde preguntas
iniciales sobre productos, releva datos comerciales, asigna un puntaje de
calificación y carga todo en Kommo CRM para que un vendedor llame.

## Cliente

**MasterShield** — asesoría, distribución e instalación de laminados de
grado arquitectónico para vidrio. 15 años en el mercado **ecuatoriano**,
con base en **Quito**. Importador directo de HÜPER OPTIK, MIDAS FILMS,
CONCO, MADICO, 3M y otras.

3 vendedores. El handoff es telefónico: el vendedor **llama**, no sigue
el chat.

> **El cliente es de Ecuador, no de Argentina.** Esto define el registro
> del idioma (usted, no voseo), la zona horaria (`America/Guayaquil`,
> UTC-5) y el prefijo telefónico (+593). Ver la sección Humanización.

### Dos líneas de negocio, no una

1. **Arquitectónico** — el núcleo del negocio. Vidrio de domicilios,
   oficinas, pérgolas. Control solar, privacidad y seguridad.
2. **Vehicular** — línea secundaria, también la venden.

Son líneas **separadas** y el material de una no se usa en la otra. El
documento de preguntas frecuentes se despega con fuerza del "polarizado
de carro" sobre vidrio arquitectónico, pero sí venden lámina vehicular
como producto propio. El agente no debe confundir las dos cosas ni
rechazar consultas vehiculares: son negocio.

### El evento de conversión es la visita técnica

No es la llamada: es agendar la visita técnica. Es gratuita en Quito y
alrededores, y su única condición declarada es que el cliente entregue
información detallada del pedido. También se puede cotizar de forma
rápida con fotos o una referencia de metros cuadrados.

Todo el relevamiento del agente apunta a eso: conseguir la información
que habilita la visita o la cotización rápida.

---

## Reglas de trabajo

- Español en código, comentarios, commits y mensajes al usuario.
- **No escribir código sin que exista un test o una forma de verificarlo.**
  Cada módulo se valida con datos reales antes de pasar al siguiente.
- No inventar IDs de campos de Kommo, ni endpoints del BSP, ni nombres de
  productos, marcas, precios, plazos ni garantías. Si falta un dato,
  pararse y preguntar.
- El contenido técnico del rubro sale **únicamente** del material del
  cliente. No completar con conocimiento general sobre películas para
  vidrio: buena parte de lo que es cierto en el rubro en general es
  falso para estos productos. Ver "Guardarraíles de producto".
- No agregar dependencias sin justificarlo. El stack está cerrado.
- No sobre-diseñar. Este sistema atiende decenas de conversaciones por día,
  no miles. Nada de colas distribuidas, microservicios ni abstracciones
  "por si mañana".
- Un módulo por sesión. Al terminar, correr los tests y hacer commit.

---

## Stack

| Capa | Tecnología |
|---|---|
| Servicio | Python 3.12 + FastAPI + uvicorn |
| Modelo | Anthropic SDK (Claude) con tool use |
| Base de datos | PostgreSQL (Supabase) + pgvector |
| Canal | WhatsApp Business API vía BSP |
| CRM | Kommo API v4 |
| Deploy | Railway (cuenta del cliente), Docker |
| Tests | pytest + respx (mock de HTTP) |

Dependencias: `fastapi`, `uvicorn`, `anthropic`, `httpx`, `asyncpg`,
`pydantic`, `pydantic-settings`, `pyyaml`, `structlog`, `pytest`,
`pytest-asyncio`, `respx`.

---

## Arquitectura

```
BSP webhook ──> POST /webhook/whatsapp
                  │
                  ├─ valida firma
                  ├─ deduplica por wa_message_id
                  ├─ guarda mensaje en DB
                  ├─ agenda procesamiento (ahora + VENTANA_BUFFER)
                  └─ responde 200 en <500ms   ← NO llamar al LLM acá

worker (loop cada 1s)
                  │
                  ├─ toma conversaciones con procesar_despues <= now()
                  ├─ arma el turno con TODOS los mensajes sin procesar
                  ├─ corre el agente (loop de tool use)
                  ├─ envía respuesta al BSP con delays humanos
                  └─ si se completó la calificación → push a Kommo
```

### Por qué el webhook no llama al modelo

Los BSP reintentan si el webhook tarda o falla, y eso genera respuestas
duplicadas. El webhook solo persiste y agenda. Todo el trabajo pesado va
al worker.

### Buffer de mensajes (debounce) — es el corazón de la humanización

La gente escribe en ráfagas: "buenas" / "necesito lámina" / "para una
oficina en Cumbayá". Responder a cada uno delata el bot al instante.

Al llegar un mensaje se hace `UPSERT` en `pendientes` con
`procesar_despues = now() + VENTANA_BUFFER` (default **30 segundos**). Cada
mensaje nuevo **reinicia** el timer. El worker procesa recién cuando
la ventana venció, tomando todos los mensajes acumulados como un solo turno.

La ventana es larga porque desde que el cliente pidió el retraso de 1–2 minutos
antes de responder, recolectar durante más tiempo no cuesta nada: entra dentro
de ese presupuesto. Con 6 segundos las ráfagas lentas se partían — medido con
mensajes reales, la gente deja huecos de ~9 segundos entre líneas.

Va en base de datos, no en memoria, porque Railway reinicia el proceso
en cada deploy.

---

## Modelo de datos

- `conversaciones` — una por teléfono. Estado, datos relevados (jsonb),
  score, IDs de Kommo.
- `mensajes` — historial completo. `wa_message_id` con índice único
  para idempotencia. Flag `procesado`.
- `pendientes` — buffer de debounce. Una fila por conversación pendiente,
  con `procesar_despues` y `bloqueado_hasta` (lock del worker).
- `catalogo` — chunks de la base de conocimiento con embedding vector(1536).
- `eventos` — auditoría: qué se envió a Kommo, qué falló, qué se reintentó.

Estados de conversación: `activa` · `pausada` · `calificada` · `derivada` · `cerrada`.

---

## El agente

Loop de tool use, no un flujo lineal de preguntas. El agente decide qué
preguntar según lo que ya sabe.

Herramientas:

- `buscar_catalogo(consulta)` → búsqueda semántica en `catalogo`.
  **El agente no responde nada técnico sobre productos sin llamar esta
  herramienta primero.** Si no encuentra información, lo dice y deriva.
- `guardar_dato(campo, valor)` → persiste incrementalmente en
  `conversaciones.datos`. Se llama apenas el cliente menciona el dato,
  no al final.
- `finalizar_calificacion()` → cierra el relevamiento y dispara scoring + Kommo.
- `escalar_a_humano(motivo)` → pausa el agente y notifica al equipo.

El prompt del sistema vive en `prompts/agente.md`, versionado. No hardcodearlo.

### Campos a relevar

Hipótesis derivada del documento de preguntas frecuentes. **Se valida y
se ajusta en el kick off**; hasta entonces va en `config/calificacion.yaml`
marcada como provisoria.

| Campo | Valores | Por qué importa |
|---|---|---|
| `linea` | arquitectónico · vehicular | Rutea todo lo demás |
| `zona` | Quito y alrededores · otra ciudad · fuera del país | Eje más fuerte |
| `aplicacion` | domicilio · oficina · pérgola · local · vehículo | Define producto |
| `objetivo` | control solar · privacidad · seguridad · decorativo | Líneas distintas |
| `medidas` | m² aprox., cantidad de ventanas o fotos | Habilita cotización |
| `tipo_cliente` | particular · empresa · constructora o arquitecto | A confirmar |
| `urgencia` | inmediato · semanas · explorando | A confirmar |

**Descarte:** fuera de Ecuador. **Fricción alta:** ciudades fuera de
Quito, donde existen montos mínimos de instalación — el monto exacto es
un dato que falta y hay que pedirlo.

`medidas` es el campo crítico: sin fotos ni referencia de metros no hay
cotización rápida ni visita agendable.

---

## Guardarraíles de producto

La mitad del documento de preguntas frecuentes son **negaciones**, y son
la parte más importante del catálogo. Un modelo sin restricción va a
afirmar varias de estas cosas porque son ciertas para otros productos
del rubro. Acá no lo son.

El agente **nunca** debe afirmar que las láminas:

- rechazan el frío o aíslan térmicamente
- enfrían un ambiente
- reducen el ruido exterior
- evitan la condensación en el vidrio
- son antibalas o blindaje (son anti motín: golpes, palos, piedras)
- oscurecen los espacios

Y debe saber que:

- La lámina de privacidad **pierde el efecto de noche**, cuando se
  invierte la iluminación interior-exterior. Funciona hasta las 18h aprox.
- La instalación **exterior** en pérgolas (Roof Shield) dura **1 a 3 años**,
  contra los 15, 10 o 5 años de garantía de la instalación interna. Siempre
  se recomienda instalar del lado interno.
- No se recomienda material combinado solar + seguridad sobre vidrio
  arquitectónico; cada característica va en una capa especializada.
- El material vehicular **no** va sobre vidrio arquitectónico.

**Regla dura para el prompt:** ante cualquier consulta sobre qué hace o
qué no hace el material, el agente llama `buscar_catalogo` antes de
responder. Si el catálogo no lo cubre, dice que lo confirma un asesor y
no improvisa.

Estas negaciones se cargan como chunks propios y de alta prioridad en
`catalogo`, no diluidas dentro de textos largos.

### Riesgo abierto: rotura térmica

El documento no menciona el riesgo de rotura térmica al laminar vidrio
templado o laminado, que es un riesgo real del rubro. **Hasta que el
cliente defina cómo lo maneja, el agente no confirma factibilidad de
instalación sobre ningún vidrio**: eso lo determina la visita técnica.
Pendiente de resolver en el kick off.

---

## Scoring

**El modelo extrae, Python puntúa.** El score no lo decide el LLM.

Los campos relevados entran a una función de reglas definida en
`config/calificacion.yaml` (pesos por campo y valor, umbrales de
clasificación). Así se recalibra editando un YAML, sin tocar código ni
redesplegar — que es exactamente lo que se vendió como mantenimiento.

Salida: `score` 0–100 + `clasificacion` en tres niveles + el detalle de
qué sumó cada campo (se guarda, se manda a Kommo en la nota).

---

## Humanización

Requisito central del proyecto. Reglas duras:

- Respuestas cortas. Máximo ~2 líneas por mensaje.
- Si la respuesta es larga, partirla en 2–3 mensajes con pausas entre medio.

**Hay dos escalas de tiempo distintas y no hay que confundirlas.**

1. **Retraso de respuesta: 60–120 segundos**, decidido por el cliente para dar
   realismo. Se mide desde el último mensaje del cliente hasta el primer
   mensaje del agente, y la ventana de recolección va **dentro** de ese
   presupuesto, no encima: si se sumaran, el peor caso serían 150 segundos y
   dejaría de ser "1 a 2 minutos". Aplica siempre, también a la primera
   respuesta de una conversación. El valor se sortea por turno, con variación
   real: un retraso fijo es un patrón detectable.
2. **Pausa entre mensajes partidos: 1.5–7 segundos**, proporcional al largo del
   texto (aprox. 25–40 caracteres por segundo). Es el tiempo que tarda una
   persona en tipear la línea siguiente.

- Indicador de "escribiendo" solo en los últimos segundos antes de enviar.
  Nadie tipea durante dos minutos: dejarlo prendido todo el retraso delata
  tanto como no ponerlo.
- **Supersesión obligatoria.** Si llega un mensaje del cliente mientras hay una
  respuesta esperando a ser enviada, esa respuesta se descarta y el turno se
  rehace con todo. Con un retraso de 90 segundos esto no es un caso de borde:
  es lo que va a pasar seguido.
- **Español de Ecuador. Trato de usted, nunca voseo ni tuteo.** Registro
  formal pero cálido, como el del documento de preguntas frecuentes del
  cliente. Un agente que vosea delata al instante que no es de ahí.
- Sin emojis salvo que el cliente los use primero.
- Prohibido: "¿En qué más puedo ayudarte?", "Estoy aquí para asistirte",
  listas con viñetas, mayúsculas de encabezado, respuestas que arrancan
  con "¡Claro!".
- Se dice **lámina** o **laminado**, nunca "polarizado" al hablar de
  arquitectónico: el cliente se despega activamente de ese término.
- La marca se escribe **MasterShield®** y los vendedores son
  **asesores MS**. Usar esa terminología.
- No repreguntar lo que ya está en `conversaciones.datos`.

**Si le preguntan si es un bot, no miente.** Responde que es el asistente
de la empresa y ofrece pasar con un vendedor. Está acordado con el cliente.

---

## Handoff y pausa

- `escalar_a_humano` → estado `derivada`, notificación al equipo.
- Si un vendedor responde manualmente desde el número → estado `pausada`,
  el agente deja de contestar esa conversación.
- Fuera de horario: el agente igual atiende y califica (es una ventaja de
  venta), pero la tarea de llamado se agenda para el próximo día hábil.
  El horario se evalúa siempre en hora de Ecuador (UTC-5), no en la del
  servidor.
- Reactivación: si el cliente vuelve a escribir después de 24hs, se retoma
  la conversación existente, no se crea una nueva.

---

## Integración Kommo

API v4, OAuth2 con token de larga duración. Se escribe cuando la
calificación se completa:

1. Buscar contacto por teléfono; crear si no existe.
2. Crear o actualizar el lead vinculado.
3. Cargar campos personalizados: score, clasificación y cada dato relevado.
4. Adjuntar nota con resumen de la conversación + transcripción completa.
5. Mover a la etapa correspondiente según clasificación.
6. Crear tarea de llamado, sin responsable asignado (los 3 vendedores la
   ven y el primero que la toma se la asigna).

Los IDs numéricos de campos, etapas y pipeline van en `config/kommo.yaml`,
nunca hardcodeados. Se completan una vez que estén las credenciales.

Toda escritura a Kommo se registra en `eventos` y reintenta con backoff.
**Si Kommo falla, la conversación no se pierde**: queda marcada para
reintento y el agente sigue funcionando.

---

## Variables de entorno

```
DATABASE_URL=
ANTHROPIC_API_KEY=
KOMMO_SUBDOMAIN=
KOMMO_ACCESS_TOKEN=
BSP_API_URL=
BSP_TOKEN=
BSP_WEBHOOK_SECRET=
VENTANA_BUFFER_SEG=30
DEMORA_RESPUESTA_MIN_SEG=60
DEMORA_RESPUESTA_MAX_SEG=120
HORARIO_ATENCION=09:00-18:00
TZ=America/Guayaquil
PAIS=EC
PREFIJO_TELEFONICO=+593
LOG_LEVEL=INFO
```

Nunca commitear `.env`. Mantener `.env.example` actualizado.

---

## Estructura

```
app/
  main.py              FastAPI, rutas, arranque del worker
  config.py            settings con pydantic-settings
  db.py                pool asyncpg, helpers
  webhook.py           recepción, validación de firma, dedup
  worker.py            loop de procesamiento del buffer
  agente/
    loop.py            ciclo de tool use
    herramientas.py    las 4 tools
    catalogo.py        embeddings + búsqueda vectorial
  scoring.py           reglas desde YAML
  kommo/
    cliente.py         auth + HTTP
    sincronizacion.py  contacto, lead, nota, tarea, etapa
  whatsapp/
    cliente.py         envío al BSP
    humanizacion.py    partido de mensajes y delays
prompts/
  agente.md
config/
  calificacion.yaml
  kommo.yaml
migrations/
tests/
scripts/
  cargar_catalogo.py
  simular_conversacion.py
```

---

## Definición de terminado

Un módulo está listo cuando: tiene tests que pasan, fue probado con datos
reales (no sintéticos), los errores se loguean con contexto y no rompen
el flujo, y está commiteado.

---

## Datos pendientes del cliente

No inventar ninguno de estos. Si el flujo los necesita antes de tenerlos,
el agente deriva a un asesor.

- Montos mínimos de instalación fuera de Quito (dato duro de calificación).
- Rangos de precio o "desde cuánto", aunque el agente no cotice.
- Metros cuadrados mínimos en Quito, si existen.
- Plazos de instalación y formas de pago.
- Horario de atención confirmado, en hora Ecuador.
- Manejo del riesgo de rotura térmica.
- Remoción de lámina vieja: ¿la hacen? ¿se cobra aparte?
- ¿Venden material sin instalación, a terceros o instaladores?
- ¿Trabajan con constructoras y arquitectos? Sería el lead de mayor valor
  y no aparece en el material.
- Vigencia del documento de preguntas frecuentes: es de 2025. Confirmar
  marcas, garantías y formatos de rollo.

## Nota administrativa

El cliente es del exterior: la facturación es **exportación de servicios
(Factura E)**, no Factura C en pesos. Corregir en la propuesta antes de
emitir.
