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

### 3b. Catálogo: 4 productos, confirmado

El cliente confirmó que son los 4 del sitio. Queda:

- [x] **Roof Shield®** — no es un quinto producto: es el control solar
  instalado por el lado exterior. Queda como `variante_exterior` en
  `productos.yaml`.
- [ ] ¿Roof Shield tiene precio propio o el mismo que la instalación interna?
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

## Kommo

- [x] Cliente HTTP, sincronización, campos, etapas, nota y tarea. Probado
  contra la cuenta real de prueba.
- [ ] **La tarea de llamado no puede quedar sin responsable.** Kommo exige un
  usuario responsable en toda tarea; hoy queda a nombre del dueño del token.
  `CLAUDE.md` pedía que no tuviera responsable para que los 3 vendedores la
  vieran y se la quedara el primero. Hay que definir con MasterShield a quién
  se asignan, o si prefieren verlas por otro mecanismo.
- [ ] **La cuenta de prueba es de Kommo, no de MasterShield.** Cuando den
  acceso a la suya hay que correr `mapear_kommo.py` y `preparar_kommo.py` de
  nuevo: los IDs del YAML son de esta cuenta y no sirven en otra.

## Decisiones a confirmar con el cliente

- [ ] A quién y por qué medio se notifica un `escalar_a_humano`.
- [ ] Cómo se detecta que un vendedor contestó manualmente desde el número,
  para pasar la conversación a `pausada` (depende del BSP). El esquema ya lo
  contempla: `mensajes.rol = 'vendedor'`.
- [ ] Horario de atención real, en hora de Ecuador. Por ahora 09:00–18:00 de
  lunes a viernes, que es un default, no un dato del cliente.
- [ ] Manejo del riesgo de rotura térmica (ver CLAUDE.md).

## Deuda del código, sin bloqueos externos

- [x] **El reintento del CRM.** `app/crm/reintentos.py`, con backoff de 1 min a
  6 h y un tope de cinco intentos. Agotado no es resuelto: la fila queda
  pendiente con `crm_reintentar_en` en NULL, fuera del loop pero contada.
- [x] **El aviso de que alguien necesita una persona.** Vía tarea urgente en
  Kommo, que es la única notificación que su API deja provocar.
- [x] **La pausa por intervención humana**, con vencimiento de 6 horas y
  razonamiento al despertar.
- [ ] **Confirmar cómo van a trabajar los vendedores de MasterShield.**
  `smb_message_echoes` cubre la app de WhatsApp Business. Si contestan desde
  Kommo no hay eco de Meta y la detección tiene que venir de Kommo.
- [ ] **La demora está en 12–18 segundos, no en 60–120.** Bajada para poder
  testear. Antes de producción vuelve a `DEMORA_RESPUESTA_MIN_SEG=60` y
  `MAX=120`, que es lo que pidió el cliente.
- [ ] **Falta el deploy en Railway**, el logging estructurado con structlog y
  el endpoint `/metricas`.

## Módulos que se pueden avanzar sin nada de lo anterior

- `whatsapp/humanizacion.py` — partido de mensajes y cálculo de delays.
  Python puro, se testea entero sin DB ni credenciales.
