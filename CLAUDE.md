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
Kommo, con toda la información del pedido.
Con eso llama un vendedor y cierra la visita.

Esto define el final de cada conversación y hay que tenerlo presente en el
prompt: el agente no promete una fecha, no confirma un horario y no dice que
alguien va a ir tal día. Dice que un asesor MS se comunica para coordinar.

La visita es gratuita en Quito y sus valles, y su única condición declarada es
que el cliente entregue información detallada del pedido.

**El agente vende la visita, aunque no la agende.** El documento del 4/9/2026
pide que la presente por lo que la persona gana —se despeja cualquier duda y se
ve el material funcionando, se define el material adecuado, se toman las medidas
exactas y sale un presupuesto preliminar— y que proponga siempre ese paso
siguiente. Pero la coordinación y la confirmación las hace un asesor por
teléfono.

### Cinco zonas de venta, cada una con su precio

Desde el 29/9/2026, por el documento **"UBICACIONES Y PRECIOS MS 2027"**. Antes
eran dos categorías —Quito y "otra ciudad"— con un recargo plano de USD 10 por
m². Ese modelo se terminó.

| Zona | Mínimo | 10 años | 5 años | Seguridad |
|---|---|---|---|---|
| Quito y valles | 5 m² | 42 / 37 | 32 / 25 | desde 24 |
| Pichincha, zonas cercanas | 10 m² | 48 / 39 | 35 / 29 | desde 25 |
| Azul | 15 m² | 52 / 45 | 39 / 35 | desde 29 |
| Verde | 20 m² | 55 / 49 | 45 / 39 | desde 35 |
| Rojo | 25 m² | 55 / 49 | 45 / 39 | desde 35 |

Sin IVA, por m². El segundo número es la promoción del mes. **Verde y rojo tienen
los mismos precios a propósito** y se diferencian sólo por el mínimo: confirmado
por el cliente.

**Los precios viven en la zona, no en el producto.** Cada zona trae su tabla y no
hay fórmula que las relacione: en verde la calidad de 5 años cuesta 45, no 42.
Control solar y privacidad comparten tabla y seguridad tiene la suya, como los
agrupa el cliente en su lista; cada producto declara de qué tabla come
(`tabla_de_precios`).

**La zona la deduce el código, no el modelo.** `config/ubicaciones.yaml` tiene 218
ciudades indexadas por ciudad y por provincia —la gente dice las dos cosas— y
`app/ubicaciones.py` las resuelve ignorando tildes y mayúsculas, y encontrando el
nombre dentro de una frase ("la instalación es en Cuenca"). Al guardar `ciudad`,
`guardar_dato` deduce `zona`, igual que deduce la línea. Saber que Gualaceo es
Azuay y que Azuay es zona verde es un dato duro, no criterio del modelo.

Dos detalles que el resolvedor tiene que respetar y están cubiertos por tests:
gana el nombre más largo —"San Miguel de los Bancos" es Pichincha y "San Miguel"
es Bolívar, con mínimos distintos— y una ciudad que no está en la tabla devuelve
`None` en vez de una zona al azar, porque eso sería informar un precio
equivocado.

**Descarte:** fuera de Ecuador y **Galápagos**, que no aparece en ninguna zona del
documento (confirmado el 29/9/2026).

### Precios: el agente informa el metro, no calcula

**El agente no hace cuentas.** Informa cuánto vale el metro cuadrado del
material que la persona necesita, con sus dos calidades, y ahí termina. El
total sale de las medidas exactas que toma el asesor en la visita.

Es un cambio de septiembre de 2026 sobre el diseño original, y está sostenido
por código, no sólo por el prompt: `consultar_precio` no devuelve ningún
subtotal, y la verificación de precios sólo autoriza los valores por m² que la
herramienta devolvió en ese turno. Un total no lo autoriza nadie, aunque la
cuenta esté bien.

`cotizar` sigue existiendo pero sólo para el CRM: el vendedor necesita saber si
va a un trabajo de 10 m² o de 200 antes de levantar el teléfono. Es un estimado
interno y el cliente nunca lo escucha.

**El precio especial es una promoción mensual.** `vigencia_precio_especial` en
`config/productos.yaml` dice de qué mes es; vencido, el agente vuelve solo al
precio normal.

### Cómo se actualizan los precios

Los precios viven en `config/productos.yaml` y **se leen una vez al arrancar el
proceso** (`configuracion()` está cacheada). Editar el archivo no alcanza: hay
que reiniciar. En producción eso lo hace el deploy, así que el procedimiento es:

1. Editar `precio_normal` y `precio_especial` **en cada zona** que cambie, y
   `vigencia_precio_especial` si cambia el mes. Los precios están dentro de
   `zonas`, no de `productos`: son cinco tablas.
2. Correr los tests: `tests/test_precios.py` compara contra números escritos a
   mano y se cae si alguien se equivoca en un dígito. Es a propósito.
3. Commit y push. El deploy reinicia el proceso y los precios nuevos rigen.

Que pase por git no es burocracia: deja el historial de qué precio regía cada
mes, que es exactamente lo que hace falta cuando un cliente reclama que le
dijeron otra cosa.

**Nadie tiene que acordarse del 1 de cada mes.** `/metricas` avisa cinco días
antes de que la promoción venza, y si ya venció lo dice con todas las letras
junto con qué hay que hacer. Es la misma vía por la que salen las demás
alertas.

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

> **Desde el 15/9/2026 la ventana es de 4 a 6 segundos** (`DEMORA_RESPUESTA_MIN_SEG`
> y `_MAX_SEG`). El 11/9 se había bajado a 0, pero así el mensaje se marcaba leído
> al instante y el "escribiendo" se cortaba con cada turno descartado. El cliente
> confirmó que unos 20 segundos de respuesta total están bien.
>
> Con una ventana corta, quien junta las ráfagas es sobre todo la **supersesión**:
> si llega un mensaje mientras el agente piensa, la respuesta se descarta y el
> turno se rehace con todo. Una ráfaga cuesta más llamadas al modelo, pero no se
> contesta de a partes.

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

Son seis. `buscar_catalogo` ya no existe: desde el 15/9/2026 la información de
producto se le inyecta en el contexto de cada turno, sin gastar una vuelta de
modelo en pedirla (ver "Respuestas por situación").

- `guardar_dato(campo, valor)` → persiste incrementalmente en
  `conversaciones.datos`. Se llama apenas el cliente menciona el dato,
  no al final.
- `consultar_precio()` → el precio por m² del producto. No calcula totales.
- `enviar_ficha(producto)` → pide que salga la descripción oficial del
  producto. No la manda: la manda el worker, palabra por palabra, antes del
  texto del modelo. Ver "Fichas de producto y video".
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
| `ciudad` | texto, como la dijo la persona | De acá sale la zona |
| `zona` | 5 zonas de venta · Galápagos · fuera de Ecuador | Define mínimo y precio |
| `aplicacion` | domicilio · oficina · pérgola · local · vehículo | Define producto |
| `objetivo` | control solar · privacidad · seguridad | Es el producto |
| `medidas` | m² aprox., cantidad de ventanas o fotos | Habilita cotización |
| `garantia` | 10 años · 5 años | Cambia el precio, solo solar y privacidad |
| `modelo_vehiculo` | texto | Solo vehicular: define el tipo y el material |
| `nivel_seguridad` | texto | Solo vehicular |
| `disponibilidad` | mañana · tarde | **Ya no se pregunta** (15/9/2026): el asesor llama a la brevedad |
| `telefono` | E.164 | En WhatsApp viene en el payload y se confirma; en Telegram hay que pedirlo |
| `tipo_cliente` | particular · empresa · constructora o arquitecto | A confirmar |
| `urgencia` | inmediato · semanas · explorando | A confirmar |

`objetivo` mapea uno a uno con los productos, así que relevarlo es elegir qué
se vende: control solar, privacidad o seguridad.

**Filtro duro:** el mínimo de instalación va de **5 a 25 m² según la zona** (ver
"Cinco zonas de venta"). Alguien en Loja con 15 m² no llega al mínimo de 25, y eso
pesa más que cualquier otro campo. Por debajo del mínimo **no se dan precios**: se
dice cuál es y se pregunta si suma otro ambiente; si suma y llega, ahí salen.

`medidas` sigue siendo crítico: sin fotos ni referencia de metros no hay
cotización ni visita.

### Orden obligatorio y precio al final

Definido por MasterShield en la reunión del 11/9/2026. Los datos son obligatorios
y se piden en este orden: **nombre y ciudad** (juntos, en la bienvenida: la
ciudad define el precio y el mínimo, y el cliente pidió tenerla desde el
principio) **→ pedido** (control solar, privacidad, seguridad o vehicular) **→
referencia en metros o fotos**, explicando el mínimo de su zona **→ fotos del área**
(se ofrecen, son opcionales) **→ precios del mes → la visita técnica**.

Está sostenido por código, no sólo por el prompt:

- `antes_del_precio` en `config/calificacion.yaml` lista lo que tiene que estar
  para dar el precio. `consultar_precio` se niega mientras falte algo y devuelve
  lo que falta en ese orden, así el agente pregunta lo primero y no salta pasos.
- `referencia` no es un campo: se cumple con `metros_cuadrados` o con una foto o
  documento del cliente en `mensajes`.
- `requeridos_por_linea` sigue el mismo orden y ahora incluye `nombre`, así que
  `finalizar_calificacion` tampoco cierra sin él.

**`telefono` no se pregunta en WhatsApp: se confirma.** El número del remitente
viene en cada mensaje, así que la ingesta lo siembra en `datos` al crear la
conversación y el agente solo ofrece la alternativa —"¿lo llamamos a este mismo
número o prefiere dejar otra línea?"—. Pedirle a alguien que tipee el número
desde el que está escribiendo es de formulario. En Telegram no viene en el
payload y ahí sí hay que pedirlo. Si el cliente da otro, `guardar_dato` pisa al
del canal y gana el relevado.

**`disponibilidad` ya no se pregunta** (15/9/2026: MasterShield pidió sacar la
franja; el asesor llama a la brevedad, y desde el 23/9 el agente explica el
horario de oficina cuando la llamada cae al día siguiente). Lo que sigue es el
historial.

**`disponibilidad` era requisito para cerrar, y la elegía el cliente entre mañana y
tarde.** Hasta el 11/9/2026 era un día y una hora libres; MasterShield pidió
ofrecer la franja, porque un horario puntual es una promesa que el asesor no
siempre cumple. Se guarda `manana` o `tarde`, y en Kommo se lee "por la mañana"
o "por la tarde" (`documento.franja_de_llamado`). Es requisito por una razón
operativa además de comercial —
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

**Regla dura:** lo que no está en la información que el agente recibe, lo
confirma un asesor MS. No improvisa. Desde el 15/9/2026 no la pide con una
herramienta: los fragmentos que se parecen a lo que escribió la persona le llegan
en el contexto del turno, buscados por similitud en `catalogo` (ver "Respuestas
por situación").

Estas negaciones se cargan como fragmentos propios y de alta prioridad, no
diluidas dentro de textos largos: son la parte del catálogo que más importa.

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

1. **Retraso de respuesta: 4 a 6 segundos de ventana**, más lo que tarda el
   agente en razonar. Arrancó en 60–120 segundos, bajó a 0 el 11/9/2026 y volvió
   a una ventana corta el 15/9: unos 20 segundos en total.

   Ese retraso **es** la ventana de recolección, no se suma a ella: cada
   mensaje nuevo lo reinicia. Si se vuelve a poner, se sortea por turno porque
   un retraso fijo es un patrón detectable.
2. **Pausa entre mensajes partidos: 1.5–7 segundos**, proporcional al largo del
   texto (aprox. 25–40 caracteres por segundo). Es el tiempo que tarda una
   persona en tipear la línea siguiente.

- Indicador de "escribiendo" solo en los últimos segundos antes de enviar.
  Nadie tipea durante dos minutos: dejarlo prendido todo el retraso delata
  tanto como no ponerlo.
- **Y también antes del segundo mensaje**, pedido explícito del cliente: un
  globo que aparece de la nada delata tanto como el primero. Ojo con WhatsApp:
  Meta apaga el indicador cuando uno responde, y reactivarlo significa volver a
  marcar como leído un mensaje que ya lo está. Si falla, se loguea en warning a
  partir del segundo mensaje.
- **Supersesión obligatoria.** Si llega un mensaje del cliente mientras hay una
  respuesta esperando a ser enviada, esa respuesta se descarta y el turno se
  rehace con todo. El corte se comprueba antes de cada mensaje y otra vez
  pegado al envío, porque la pausa entre globos dura varios segundos y es justo
  cuando la persona está escribiendo. Sin ventana de recolección es lo que
  junta las ráfagas, así que va a pasar seguido.
- **Español de Ecuador. Trato de usted, nunca voseo ni tuteo.** Registro
  formal pero cálido, como el del documento de preguntas frecuentes del
  cliente. Un agente que vosea delata al instante que no es de ahí.
- **Un emoji por mensaje, cuando suma.** El cliente los pidió: dan calidez y
  son la forma normal de escribir por WhatsApp en Ecuador. No en cada mensaje y
  nunca en un precio ni en una disculpa.
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

## Respuestas por situación: el modelo es el único autor

Desde el 15/9/2026. Antes cada caso vivía en el prompt (628 renglones, unos 12.600
tokens por llamada) y varias respuestas —preguntas fijas, explicación del precio,
oferta de llamada, cierre— las metía el código en el mismo mensaje que escribía el
modelo. Los dos autores chocaban y salían mensajes duplicados, y cada filtro que
se agregaba tapaba un caso y dejaba pasar otro.

Ahora:

- **Prompt corto** (`prompts/agente.md`, ~1.000 tokens): identidad, tono y reglas
  duras. Nada de casos.
- **Respuestas por situación** en la tabla `respuestas`. La fuente editable es
  `conocimiento/respuestas.yaml`: clave, etapa, cuándo aplica, qué responder (con
  `{variables}` y emojis) e instrucciones. Los textos del cliente van tal cual.
- **Información técnica** en la tabla `catalogo`, un fragmento por sección de
  `prompts/conocimiento.md`.
- **Carga:** `.venv/bin/python scripts/cargar_conocimiento.py` después de editar
  cualquiera de los dos archivos. El agente lee la base en cada turno, así que no
  hace falta reiniciar.
- **`app/contexto.py`** arma lo que recibe el modelo en cada turno, antes de
  llamarlo (como herramienta costaría una vuelta de modelo más):
  1. el paso de la conversación, calculado por el código con los datos que ya hay
     (`nombre_ciudad → pedido → superficie → referencia → precio → llamada →
     despues_del_cierre`);
  2. las respuestas de ese paso, más las que se parecen a lo que escribió la persona
     (pgvector, `text-embedding-3-small`);
  3. los fragmentos de información relacionados.
- **Lo único que manda el código tal cual** son bloques que el modelo nunca escribe:
  la bienvenida (primer turno), las fichas con el video y la lista de precios.
- **Globos:** el modelo separa un mensaje de WhatsApp del siguiente con una línea
  que dice `---`. Los renglones libres quedan dentro del mismo globo.
- **Redes de seguridad que quedan:** el control de precios (saca solo las
  oraciones con un precio que no salió de `consultar_precio`), el filtro de
  oraciones repetidas y el emoji automático en los globos largos.
- **Reglas en código que siguen:** el orden de los datos antes del precio
  (`requisitos_del_precio`), el modo rápido cuando la persona insiste con el
  precio, la línea deducida de otros datos y lo necesario para cerrar.

## Fichas de producto y video

**Los productos son cinco** desde septiembre de 2026: control solar para
ventanas, control solar para techos de vidrio, privacidad y seguridad
arquitectónica, y seguridad vehicular. Control solar se distingue por
`superficie` (`ventanas` · `techo`), que es requisito para cerrar cuando el
objetivo es control solar. `precios.producto_para(datos)` filtra el catálogo
por línea, objetivo y superficie hasta que queda uno: sumar un producto es
editar el YAML.

**Techos de vidrio cuesta lo mismo que ventanas**, por decisión del cliente. En
el YAML es un alias (`*precios_control_solar`), no una copia. Por eso
`precios.producto_para_cotizar` da precio sin saber la superficie: compara los
precios de los candidatos que quedan, y si algún día difieren devuelve None y
el agente vuelve a preguntar.

**La garantía es requisito para cerrar** cuando el producto tiene dos calidades
(control solar y privacidad). Se decide por el catálogo, no por una lista:
`consultar_precio` le indica al agente que pregunte con cuál se queda la
persona, y `finalizar_calificacion` no cierra sin `garantia_anios`.

**Cada producto tiene una descripción oficial** en `prompts/fichas/<id>.txt`,
escrita por MasterShield. Cuando alguien pregunta por un producto recibe ese
bloque entero, no un resumen. Es texto de marca, igual que la bienvenida, y por
eso no pasa por el modelo: el modelo llama a `enviar_ficha` y el worker manda el
archivo tal cual, en un solo mensaje, sin `partir`, antes de lo que el modelo
escriba. Máximo dos por turno, y una ficha que la persona ya recibió no se
repite (se mira en `mensajes`, que es lo que de verdad salió).

**El video** (`media/video-productos.mp4`) acompaña a la primera ficha
arquitectónica de la conversación; el vehicular no sale en el clip. Lleva el
mensaje del cliente como leyenda, dentro del mismo mensaje
(`prompts/fichas/leyenda-del-video.txt`), que desde el 11/9/2026 habla de los trabajos y las terminaciones. Se sube a
Meta la primera vez, se reusa el id durante 25 días (Meta lo guarda 30), y si
Meta rechaza el id guardado se sube de nuevo y se reintenta una vez. Si falla,
la respuesta sale igual sin él y no queda registrado como enviado.

Un video grabado con el teléfono no le sirve a WhatsApp tal como viene: pesa
más de 16 MB y viene en H.264 High con B-frames, que Android no reproduce.
`scripts/preparar_video.py` lo deja listo; cambiar el clip es correrlo con el
archivo nuevo.

---

## Archivos que manda el cliente

Un cliente que manda fotos de sus ventanas espera que alguien las mire, y el
agente le dice que las revisa un asesor. Esa promesa se cumple: las fotos se
descargan de Meta y se adjuntan al lead de Kommo.

**Se descargan al armar el documento, no al recibirlas.** El webhook tiene
medio segundo de presupuesto y bajar una foto no entra. El margen alcanza: un
archivo recibido por webhook vive **siete días** en Meta, y la sincronización
corre a los minutos. La URL de descarga que da Meta dura sólo cinco, así que se
resuelve y se baja en el momento.

**Sólo imágenes y documentos.** Un audio o un video no le sirven a nadie para
tomar medidas y sólo llenan el drive del cliente.

`crm_referencia.archivos` mapea el id del archivo en Meta al uuid que quedó en
Kommo. Es lo que evita que un reintento —hay hasta seis— suba la misma foto
seis veces.

Si la subida falla, el lead se carga igual y el archivo queda pendiente para el
próximo intento: perder una foto es malo, perder el lead entero por una foto
sería peor.

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
DEMORA_RESPUESTA_MIN_SEG=4
DEMORA_RESPUESTA_MAX_SEG=6
HORARIO_ATENCION=08:00-17:00
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
  config.py            settings, horario de oficina, cuándo llama el asesor
  db.py                pool asyncpg, helpers
  webhook.py           recepción, validación de firma, dedup
  worker.py            loop de procesamiento del buffer
  contexto.py          qué recibe el modelo en cada turno (de la base)
  ubicaciones.py       de qué zona de precios es cada ciudad
  precios.py           precios por zona, mínimos, cotización interna
  textos.py            la lista de precios y la comparación de textos
  fichas.py            descripciones oficiales y el video
  audio.py             notas de voz pasadas a texto
  agente/
    loop.py            ciclo de tool use y los controles de la respuesta
    herramientas.py    las seis tools
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
  calificacion.yaml    campos, pesos, qué hace falta antes del precio
  productos.yaml       productos, zonas, mínimos y precios
  ubicaciones.yaml     218 ciudades por zona
  kommo.yaml
conocimiento/
  respuestas.yaml      qué responder en cada situación
migrations/
tests/
scripts/
  cargar_conocimiento.py   respuestas y catálogo a la base
  preparar_kommo.py        descubre IDs y carga el catálogo de precios
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

- Plazos de instalación.
- **¿La visita es sin costo en todo el país?** El texto que MasterShield escribió
  para la oferta dice "no tiene ningún costo" sin condición, pero el material
  anterior la limitaba a Quito y sus valles. Mientras no se confirme, el agente
  omite esa línea en provincias.
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
