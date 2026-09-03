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

### El agente releva; el vendedor cierra

Para el negocio, el evento de conversión es la visita técnica. **Para el agente
no**: el agente no agenda nada. Su entregable es una calificación completa en
Kommo, con toda la información del pedido y qué día le queda cómodo al cliente.
Con eso llama un vendedor y cierra la visita.

Esto define el final de cada conversación y hay que tenerlo presente en el
prompt: el agente no promete una fecha, no confirma un horario y no dice que
alguien va a ir tal día. Dice que un asesor MS se comunica para coordinar.

La visita es gratuita en Quito y sus valles, y su única condición declarada es
que el cliente entregue información detallada del pedido. También se puede
cotizar de forma rápida con fotos o una referencia de metros cuadrados: eso
sí lo hace el agente, con `calcular_precio`.

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
| Modelo | OpenAI (`gpt-5`) con tool use · ver nota |
| Base de datos | PostgreSQL (Supabase) + pgvector |
| Canal | WhatsApp Business API vía BSP |
| CRM | Kommo API v4 |
| Deploy | Railway (cuenta del cliente), Docker |
| Tests | pytest + respx (mock de HTTP) |

Dependencias: `fastapi`, `uvicorn`, `anthropic`, `openai`, `httpx`, `asyncpg`,
`pydantic`, `pydantic-settings`, `pyyaml`, `structlog`, `pytest`,
`pytest-asyncio`, `respx`.

> **Nota sobre el proveedor del modelo.** El proyecto arrancó sobre Anthropic,
> como decía este documento. A mitad de camino el cliente se quedó sin créditos
> y hubo que pasar a OpenAI. Para que eso no fuera una reescritura, el loop de
> tool use quedó detrás de `app/agente/proveedor.py`: lo que cambia entre uno y
> otro es la forma de los mensajes, dónde va el prompt del sistema y cómo se
> devuelven los resultados de las herramientas, no cómo razona el agente.
> Volver a Claude es cambiar `PROVEEDOR_MODELO` y `MODELO_AGENTE`.
>
> `gpt-5` es un modelo de razonamiento: cuenta lo que piensa dentro del mismo
> presupuesto que lo que escribe. Con el límite de tokens que servía para
> Sonnet, devolvía mensajes vacíos sin error. Corre con `reasoning_effort` bajo
> —esto es una conversación de ventas, no un problema de lógica— y con margen
> de salida más amplio.

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
  Es idempotente: si ya se cerró, no vuelve a sincronizar.
- `escalar_a_humano(motivo)` → pausa el agente y crea la tarea urgente en Kommo.
- `cerrar_sin_responder(motivo)` → termina el turno sin mandar nada. Existe para
  que "no contestar" sea una decisión registrada y no un turno vacío, que es
  indistinguible de uno que falló.

El prompt del sistema vive en `prompts/agente.md`, versionado. No hardcodearlo.

### Campos a relevar

Hipótesis derivada del documento de preguntas frecuentes. **Se valida y
se ajusta en el kick off**; hasta entonces va en `config/calificacion.yaml`
marcada como provisoria.

| Campo | Valores | Por qué importa |
|---|---|---|
| `linea` | arquitectónico · vehicular | Rutea todo lo demás |
| `zona` | Quito y valles · otra ciudad del país · fuera de Ecuador | Eje más fuerte |
| `aplicacion` | domicilio · oficina · pérgola · local · vehículo | Define producto |
| `objetivo` | control solar · privacidad · seguridad | Es el producto |
| `medidas` | m² aprox., cantidad de ventanas o fotos | Habilita cotización |
| `garantia` | 10 años · 5 años | Cambia el precio, solo solar y privacidad |
| `modelo_vehiculo` | texto | Solo vehicular: define el tipo y el material |
| `nivel_seguridad` | texto | Solo vehicular |
| `disponibilidad` | qué día y hora le quedan cómodos | Lo que el vendedor necesita para llamar. **Lo elige el cliente** |
| `telefono` | E.164 | En WhatsApp viene en el payload y se confirma; en Telegram hay que pedirlo |
| `tipo_cliente` | particular · empresa · constructora o arquitecto | A confirmar |
| `urgencia` | inmediato · semanas · explorando | A confirmar |

`objetivo` mapea uno a uno con los productos, así que relevarlo es elegir qué
se vende: control solar, privacidad o seguridad.

**Descarte:** fuera de Ecuador. **Filtro duro:** el mínimo de instalación es de
5 m² en Quito y sus valles, pero **20 m² en el resto del país**. Alguien en otra
ciudad con 15 m² no es cliente, y eso pesa más que cualquier otro campo.

`medidas` sigue siendo crítico: sin fotos ni referencia de metros no hay
cotización ni visita.

**`telefono` no se pregunta en WhatsApp: se confirma.** El número del remitente
viene en cada mensaje, así que la ingesta lo siembra en `datos` al crear la
conversación y el agente solo ofrece la alternativa —"¿lo llamamos a este mismo
número o prefiere dejar otra línea?"—. Pedirle a alguien que tipee el número
desde el que está escribiendo es de formulario. En Telegram no viene en el
payload y ahí sí hay que pedirlo. Si el cliente da otro, `guardar_dato` pisa al
del canal y gana el relevado.

**`disponibilidad` es requisito para cerrar, y la pone el cliente.** El agente
no propone un horario ni completa el silencio con "lo llamamos hoy a la tarde":
pregunta y espera. Es requisito por una razón operativa además de comercial —
sin él el agente cerraba, sincronizaba, y volvía a cerrar cuando el cliente
contestaba el horario, dejando el lead con dos notas en Kommo y un paso por la
etapa equivocada.

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

- `escalar_a_humano` → estado `derivada`.

### Cómo se avisa de que alguien necesita una persona

**Kommo no tiene endpoint de notificaciones.** Su centro de notificaciones es
JavaScript que corre dentro de un widget, en el navegador, y las suscripciones
son un callback JS. Desde el backend no se puede llamar. Lo único que la API
deja provocar es el aviso de tarea por vencer, que sí llega como campana, push
al móvil y mail al responsable. **Así que la tarea urgente es el aviso.**

Una derivación crea tarea aunque no esté calificada, vence en 15 minutos en vez
de a fin de día, y el texto arranca con `ATENDER` y lleva el motivo. Si ya había
una tarea de llamado se adelanta, en vez de dejar dos filas para la misma
persona.

La segunda capa la configura MasterShield sin código: Digital Pipeline,
disparador "el lead entra a la etapa Derivado a un asesor", acciones crear
tarea, cambiar responsable o mandar correo. Nosotros ya movemos el lead ahí.

### La pausa por intervención humana

Si un vendedor responde manualmente desde el número → estado `pausada`, el
agente deja de contestar esa conversación.

**La pausa vence.** `pausada_hasta` arranca en 6 horas (`PAUSA_POR_HUMANO_HORAS`)
y cada mensaje del vendedor lo corre hacia adelante, igual que un mensaje del
cliente corre la ventana del debounce. Sin vencimiento, `pausada` era un estado
sin salida: a la semana siguiente el mismo cliente escribía por otra cosa y no
le contestaba nadie.

Al vencer, la conversación vuelve a `activa` **y el agente no escribe**. Sólo se
agenda un turno si el cliente dejó algo sin contestar. Y ahí tampoco se contesta
automáticamente: "sin contestar" no alcanza como criterio, porque un "gracias"
no es una consulta. Esa diferencia la decide el agente, que recibe el aviso de
que hubo un asesor en la conversación y puede llamar a `cerrar_sin_responder`.

**Cómo nos enteramos.** El campo `smb_message_echoes` del webhook de Meta, que
se suscribe aparte de `messages`. Cubre la app de WhatsApp Business y los
dispositivos vinculados: un vendedor que conteste **desde Kommo** no genera eco,
y esa detección tendría que venir de Kommo. Telegram no lo soporta.
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
PROVEEDOR_MODELO=openai
MODELO_AGENTE=gpt-5
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
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
LOG_FORMATO=texto
PAUSA_POR_HUMANO_HORAS=6
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

## Observabilidad

**Logging estructurado.** `app/registro.py` engancha structlog como formateador
del logging estándar, así que los módulos siguen usando
`logging.getLogger(__name__)` y también salen estructurados los logs de httpx,
uvicorn y asyncpg. `LOG_FORMATO=json` en producción, `texto` en la terminal.

Lo que importa no es el formato sino el contexto: `registro.con_conversacion`
mete el id en un contextvar y desde ahí **todas** las líneas del turno lo llevan
solas —el worker, el loop, las herramientas, la sincronización con el CRM—. Es
lo que permite reconstruir un turno entero en vez de buscar por hora.

**Métricas.** `GET /metricas` responde el estado del sistema ahora, no lo que
pasó. Devuelve 200 siempre, incluso con alertas: un 503 haría que el healthcheck
de Railway reiniciara el proceso, y un lead que no llegó al CRM no se arregla
reiniciando.

La métrica que justifica el resto es `crm.agotadas`: conversaciones que
agotaron los reintentos y ya no se reintentan solas. Cada una es un lead que no
está en Kommo. Las otras cuatro son conversaciones por estado, turnos trabados,
la actividad del día y los eventos por tipo.

`alertas` traduce los números a castellano. Un tablero de números crudos obliga
a saber cuál está mal, y eso lo sabe quien escribió el sistema, no quien lo mira
un martes a la mañana.

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
