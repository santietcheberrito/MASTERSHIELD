# Pendientes

Cosas que bloquean módulos y no se pueden inventar. Se van tachando a medida
que aparecen los datos.

## Bloqueantes

- [ ] **Credenciales de la Cloud API de WhatsApp** (Meta directo, ya decidido):
  token, phone number id y app secret para validar `X-Hub-Signature-256`.
  Bloquean el adaptador de WhatsApp. La costura ya está: alcanza con un
  `parsear()` en `app/canales/` que devuelva un `MensajeEntrante`.
- [ ] **Campos a relevar y criterios comerciales** (salen del kick off).
  Bloquean `config/calificacion.yaml`, `scoring.py` y el prompt del agente.
- [ ] **IDs numéricos de Kommo**: pipeline, etapas, campos personalizados de
  contacto y de lead. Bloquean `config/kommo.yaml` y `kommo/sincronizacion.py`.
- [ ] **Credenciales**: `KOMMO_ACCESS_TOKEN`, y las de la Cloud API de
  WhatsApp. `ANTHROPIC_API_KEY` y `TELEGRAM_BOT_TOKEN` ya están.

## Lo que hay que pedirle al cliente para la sesión 4

Sin esto el agente no se puede escribir: son datos de negocio, no decisiones
técnicas, y `CLAUDE.md` prohíbe inventarlos.

### 1. Precios · cargados

- [x] Matriz de precios cargada en `config/productos.yaml` y verificada con 25
  tests. El agente ya cotiza control solar, privacidad y seguridad.
- [ ] Falta el criterio del precio especial, la base del descuento del 10%, si
  el recargo de otras ciudades aplica a seguridad, y qué valles cuentan como
  Quito. Ver `docs/consultas-al-cliente.md`.

### 2. El mínimo de 5 m²

Ya confirmado: no venden menos de 5 m². Lo aplica el código, no el prompt, para
que el modelo no "haga una excepción" porque el cliente insistió. Cuando no se
llega, el agente propone sumar otro sector en vez de cortar.

- [x] **Por pedido, y se puede combinar productos.** 3 m² de una lámina más
  4 m² de otra suman 7 y el trabajo se hace.
- [x] **Vehicular no usa metros.** Se releva modelo de vehículo y nivel de
  seguridad, y cotiza un asesor.
- [x] **Fuera de Quito el mínimo es 20 m²**, cuatro veces el de Quito. Es el
  filtro de calificación más duro del negocio y hay que reflejarlo en el
  scoring.

### 3. El documento de preguntas frecuentes

Es la fuente de las negaciones —no aísla térmicamente, no reduce ruido, no es
antibalas— y es la pieza que impide que el modelo conteste desde su
conocimiento general del rubro, que para estos productos es falso.

**No va crudo al contexto.** El documento queda en el repo como fuente, y de él
se destila `prompts/conocimiento.md`: 60-80 líneas con las negaciones como
reglas explícitas, qué resuelve cada producto, garantías y qué queda fuera del
alcance del agente. Un documento de preguntas frecuentes está escrito para que
lo lea una persona, con prosa y ejemplos; enterradas ahí, las negaciones pesan
menos que si están como afirmaciones sueltas. La versión destilada sale más
chica **y** más confiable, no es un compromiso entre las dos cosas.

Ese prefijo estático va con caché de prompt de Anthropic, TTL de una hora: con
decenas de conversaciones por día los huecos entre mensajes superan los 5
minutos del caché por defecto y se estaría pagando la escritura todo el tiempo.

- [x] Conseguir el documento. Está en `docs/`, en .docx y en texto plano.
- [x] Destilarlo a `prompts/conocimiento.md`, con las negaciones como reglas
  explícitas y una sección de lo que el agente no sabe y tiene que derivar.
  17 tests protegen que ninguna negación se pierda en futuras ediciones.
- [ ] **Que lo revise alguien de MasterShield antes de producción.** Es la
  única fuente de verdad técnica del agente: un error ahí se lo va a repetir
  con total seguridad a cada cliente. El archivo lo dice arriba de todo.
- [ ] Confirmar vigencia: es de 2025. Marcas, garantías y formatos de rollo.

### 3b. Catálogo: 5 productos desde septiembre de 2026

Control solar se partió en dos: ventanas y techos de vidrio. Los otros tres
siguen igual. Cada producto tiene su descripción oficial en `prompts/fichas/`,
que el agente manda tal cual con `enviar_ficha`, y los arquitectónicos llevan
el video de `media/video-productos.mp4` la primera vez.

- [x] Separar control solar en ventanas y techos (`superficie`).
- [x] Fichas de producto enviadas palabra por palabra, sin repetirse.
- [x] Video comprimido para WhatsApp y enviado una vez por conversación.
- [x] Techos de vidrio: mismo precio y calidades que ventanas (decisión del
  cliente, 10/9).
- [x] El video lleva el mensaje del cliente como leyenda.
- [x] La garantía (10 o 5 años) es requisito para cerrar cuando el producto
  tiene dos calidades. En la prueba del 10/9 no se había ofrecido.
- [ ] Roof Shield®: ¿sigue existiendo ahora que techos es un producto propio?
- [ ] El clip definitivo con los 4 productos (hoy va el institucional, que
  además dice "satisfación"). Se reemplaza con `scripts/preparar_video.py`.
- [x] Probar el envío del video contra Meta (10/9, salió bien).
- [ ] **Control solar vehicular** — el documento dice que lo venden, el sitio
  solo lista seguridad vehicular. Cuál de los dos está desactualizado.

**La lista completa de consultas para el cliente está en
[docs/consultas-al-cliente.md](docs/consultas-al-cliente.md).**

## Resueltos

- [x] **`DATABASE_URL` de Supabase.** Proyecto en `us-west-2`. Se conecta por el
  Session pooler (`aws-0-us-west-2.pooler.supabase.com:5432`, usuario
  `postgres.<ref>`); el host directo solo tiene IPv6 y no resuelve desde acá.
  Migración `001_inicial.sql` aplicada, pgvector 0.8.2.

## CRM de prueba en Notion

Mientras no haya acceso a Kommo, el pipeline se prueba en Notion:
**MasterShield — CRM de prueba → Consultas**.

El documento del lead (`app/crm/documento.py`) no sabe de Notion ni de Kommo:
arma qué contacto, qué lead, qué nota y qué tarea corresponden a una
conversación, y cada destino lo traduce a sus nombres de campo. Por eso pasar a
Kommo es agregar un destino, no rehacer nada.

- [x] **Token de integración de Notion.** El push es automático: cuando el
  agente cierra una calificación, o deriva a un humano, la fila aparece sola.
- [ ] **Que el equipo de MasterShield mire el tablero** y diga qué falta, qué
  sobra y si las etapas son las correctas. Es la validación que importa antes
  de tocar el CRM real.
- [ ] **Los pesos del score son provisorios.** Ver la sección del kick off.

## Para cerrar la sesión 4

- [ ] **Que MasterShield revise `prompts/conocimiento.md`.** Sigue siendo lo
  único que bloquea usar esto con clientes reales.
- [ ] **Qué determina el precio Normal contra el especial.** Mientras tanto el
  agente cotiza con el normal, el más alto.
- [x] **Acortar los mensajes.** Se parten en hasta 3 globos con pausas de
  1.5 a 7 segundos.
- [ ] **Detección automática de intervención humana.** El mecanismo está
  (`ingesta.registrar_intervencion_humana`: pausa la conversación, guarda el
  mensaje como del vendedor y cancela el turno agendado), pero el disparador
  depende del canal: la Cloud API de WhatsApp avisa de los mensajes salientes
  que no mandamos nosotros, la API de bots de Telegram no. Hoy hay que pausar
  a mano.

## Seguridad

- [x] **Tope de uso por conversación.** 30 mensajes por hora, 10 por minuto, un
  mensaje de más de 4000 caracteres, o 200 mensajes en total: cualquiera de esos
  corta antes de llamar al modelo, deriva la conversación y la sube al CRM con
  etapa "Derivado a un asesor", que es por donde el equipo se entera.
- [x] **Verificación del precio antes de enviar.** Si el mensaje menciona plata
  que no salió de `calcular_precio` en ese turno, no se envía.
- [ ] **Límite de gasto en la consola de Anthropic.** Es el único que funciona
  aunque nuestro código tenga un bug. Se pone en la configuración de la cuenta,
  no requiere código, y conviene hacerlo antes del deploy.
- [ ] **Guardar los tokens de cada turno.** Hoy se loguean pero no se
  persisten, así que no sabemos cuánto cuesta una conversación. Va con el
  endpoint de métricas de la sesión 7.

## Calibración pendiente

- [x] **Metros cuadrados mínimos.** Son 5 m². Lo dijo el cliente.
- [x] **Búsqueda semántica descartada.** Con 4 productos y un documento de
  preguntas frecuentes, el corpus entra en el contexto. RAG sobre algo tan
  chico es peor, no mejor: su modo de falla es recuperar el chunk equivocado y
  contestar desde los priores del modelo, que es justo lo que hay que evitar.
  Se caen `catalogo.py`, los embeddings, pgvector y la tabla `catalogo`.
- [x] **`VENTANA_BUFFER_SEG`**. Movida a 30s. Con 6 las ráfagas se partían:
  medido con mensajes reales, la gente deja huecos de ~9s entre líneas. Es
  gratis subirla porque entra dentro del retraso de respuesta.
- [ ] **`VENTANA_BUFFER_SEG` final.** Los 30s son una estimación con una sola
  persona probando. El número sale de las 20 conversaciones de prueba con el
  equipo del cliente.
- [x] **Supersesión de respuestas.** El worker ya detectaba la carrera y
  reencolaba, pero los mensajes en vuelo salían igual. Se vio en una
  conversación real: preguntó dos veces la ciudad, con dos redacciones
  distintas, porque el cliente escribió dos segundos después de que se tomara
  el turno. Ahora se comprueba antes de cada mensaje y otra vez pegado al
  envío — la pausa entre globos dura varios segundos y es justo cuando la
  persona está escribiendo.
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

## Kommo

- [x] Cliente HTTP, sincronización, campos, etapas, nota y tarea. Probado
  contra la cuenta real de prueba.
- [ ] **La tarea de llamado no puede quedar sin responsable.** Kommo exige un
  usuario responsable en toda tarea; hoy queda a nombre del dueño del token.
  `CLAUDE.md` pedía que no tuviera responsable para que los 3 vendedores la
  vieran y se la quedara el primero. Hay que definir con MasterShield a quién
  se asignan, o si prefieren verlas por otro mecanismo.
- [ ] **La cuenta de prueba es de Kommo, no de MasterShield.** Cuando den
  acceso a la suya, el procedimiento está escrito en
  `docs/mudanza-a-la-cuenta-real.md` y son tres comandos: `--revisar`, correrlo,
  `--verificar`. El script descubre la cuenta y el embudo solos, crea etapas y
  campos, y carga el catálogo de precios. Probado de punta a punta.
- [ ] **Qué pedirle a MasterShield antes de la mudanza:** un usuario para la
  integración con permiso de **archivos** —es un scope aparte y sin él las fotos
  no se adjuntan—, el token de larga duración, a quién se le asignan las tareas,
  y los precios del mes en curso.

## Del documento del 4 de septiembre de 2026

- [ ] **El precio especial es mensual y hay que actualizarlo cada mes.**
  `vigencia_precio_especial` en `config/productos.yaml` dice de qué mes son los
  valores. Vencido, el agente vuelve solo al precio normal, que es el
  comportamiento seguro — pero deja de ofrecer la promoción. Conviene que
  MasterShield avise unos días antes de cada cambio.
- [ ] **¿El agente tiene que agendar la visita?** El documento dice "puede
  separar una cita a través de la agenda que maneje el agente". No existe
  ninguna agenda, y el `CLAUDE.md` dice lo contrario. Por ahora el agente vende
  la visita y pregunta qué día y en qué horario prefiere que lo llamen; la
  coordina un asesor. Si de verdad quieren agendado, hay que definir contra qué
  calendario.
- [ ] **¿El traslado y los andamios siguen incluidos en el precio?** Estaban en
  el material anterior y no aparecen en el nuevo. Ver
  `docs/consultas-al-cliente.md`.
- [ ] **Selección múltiple para elegir producto.** El documento la sugiere para
  que sea más fácil escoger entre los 4 productos. WhatsApp lo soporta con
  mensajes interactivos, pero es otro formato de envío: hoy mandamos solo texto.

## Decisiones a confirmar con el cliente

- [ ] A quién y por qué medio se notifica un `escalar_a_humano`.
- [ ] Cómo se detecta que un vendedor contestó manualmente desde el número,
  para pasar la conversación a `pausada` (depende del BSP). El esquema ya lo
  contempla: `mensajes.rol = 'vendedor'`.
- [ ] Horario de atención real, en hora de Ecuador. Por ahora 09:00–18:00 de
  lunes a viernes, que es un default, no un dato del cliente.
- [ ] Manejo del riesgo de rotura térmica (ver CLAUDE.md).

## Que MasterShield edite sus propios precios

- [x] **El catálogo de Kommo queda cargado** con una fila por calidad, precio
  normal, oferta especial y el SKU que enlaza cada fila al producto interno.
  Kommo trae ese catálogo de fábrica, así que no hay que enseñarles ninguna
  herramienta nueva: es una pantalla que ya usan.
- [ ] **Que el agente lea los precios de ahí en vez del YAML.** Medio día, con
  tres guardarraíles: cache corto, validación de rango —un precio en cero o diez
  veces el anterior se rechaza— y el YAML como respaldo si Kommo no responde.
  Conviene hacerlo junto con la mudanza, porque los IDs del catálogo cambian.
- [ ] **Dónde va la vigencia mensual.** El campo "Oferta especial 1" no tiene
  fecha. O se agrega un campo al catálogo y sigue venciendo sola, o se simplifica
  a "si hay oferta cargada, rige" y ellos la borran — más fácil para ellos, pero
  si se olvidan siguen ofreciendo en diciembre el precio de septiembre.

## Archivos del cliente

- [x] **Las fotos llegan al lead de Kommo.** Se bajan de Meta al armar el
  documento y se suben al drive de la cuenta. Probado de punta a punta contra
  el Kommo real, incluido que un reintento no las duplique.
- [ ] **Estimar metros con visión, más adelante.** Hoy el agente ve
  `[el cliente envio un archivo de tipo imagen]` y pide el aproximado igual, que
  es lo correcto: una estimación sacada de una foto termina en una cotización
  que no se sostiene. Cuando haya conversaciones reales se puede medir cuánto
  se equivocaría y decidir con datos.
- [ ] **Los archivos duran siete días en Meta.** Si una conversación se
  sincroniza más tarde que eso —sólo pasaría con el CRM caído mucho tiempo—, la
  foto ya no se puede bajar. Queda anotado el fallo y el lead se carga igual.

## Deuda del código, sin bloqueos externos

- [x] **El reintento del CRM.** `app/crm/reintentos.py`, con backoff de 1 min a
  6 h y un tope de cinco intentos. Agotado no es resuelto: la fila queda
  pendiente con `crm_reintentar_en` en NULL, fuera del loop pero contada.
- [x] **El aviso de que alguien necesita una persona.** Vía tarea urgente en
  Kommo, que es la única notificación que su API deja provocar. **Verificado de
  punta a punta el 3/9/2026**: una derivación de prueba llegó como mail al
  responsable de la tarea. El push al móvil y la campana usan el mismo
  mecanismo.
- [ ] **A quién se le asigna la tarea en la cuenta de MasterShield.** Hoy la
  notificación llega al dueño del token, que somos nosotros. Es el mismo
  pendiente de la sección Kommo, y hasta resolverlo el aviso funciona pero le
  suena a la persona equivocada.
- [x] **La pausa por intervención humana**, con vencimiento de 6 horas y
  razonamiento al despertar.
- [ ] **Confirmar cómo van a trabajar los vendedores de MasterShield.**
  `smb_message_echoes` cubre la app de WhatsApp Business. Si contestan desde
  Kommo no hay eco de Meta y la detección tiene que venir de Kommo.
- [x] **La demora quedó en 18–22 segundos**, que es lo que el cliente pidió el
  4/9/2026: unos 20 desde que deja de escribir. Ya no hay que volver a 60–120.
- [x] **Logging estructurado y métricas.** `app/registro.py` con structlog y
  `conversacion_id` atado a todo el turno; `GET /metricas` con las alertas en
  castellano. Los agotados del CRM ya son visibles.
- [ ] **Base de datos aparte para los tests.** Hoy la suite corre contra la
  misma base que el agente en vivo, y eso muerde de dos maneras. Los conteos ya
  filtran por las conversaciones del propio test —sin eso, una persona
  escribiéndole al agente marcaba ocho fallos que no existían—, pero
  `worker.una_vuelta()` sigue tomando las conversaciones reales que encuentra:
  **correr la suite mientras alguien usa el agente puede consumirle un turno**.
  Hace falta `DATABASE_URL_TEST` y que `conftest.py` falle si coincide con la
  de producción.
- [ ] **Falta el deploy en Railway.** Al desplegar hay que poner
  `LOG_FORMATO=json` y apuntar el healthcheck a `/health`, no a `/metricas`.
- [ ] **Falta que alguien mire `/metricas`.** El endpoint existe; lo que no hay
  es un chequeo periódico que avise. Puede ser tan simple como un cron que pegue
  una vez por día y mande las alertas al mismo destino que las derivaciones.

## Módulos que se pueden avanzar sin nada de lo anterior

- `whatsapp/humanizacion.py` — partido de mensajes y cálculo de delays.
  Python puro, se testea entero sin DB ni credenciales.
