# Consultas a MasterShield®

Lo que falta para terminar el agente. Ordenado por qué bloquea: lo de arriba
impide escribir código, lo de abajo se puede completar después.

Nada de esto se puede deducir ni estimar. Son datos de negocio.

---

## 1. Precios · lo que falta después de la última respuesta

Ya está cargado: $42 y $32 más IVA para control solar y privacidad según
garantía, $24 más IVA de piso para seguridad, $10 más IVA adicional por m²
fuera de Quito, mínimos de 5 y 20 m², descuento del 10% por efectivo o
transferencia, y que el precio incluye instalación, traslado, andamios y
limpieza previa. Con eso el agente ya cotiza.

Falta esto:

- ~~**¿Qué determina si va el "Precio Normal" o el "Precio especial"?**~~
  **Resuelto** con el documento del 4/9/2026: es una **promoción mensual**
  ("Precio especial SEPTIEMBRE"). Está en `vigencia_precio_especial` y el
  código la respeta: vencido el mes, el agente vuelve solo al precio normal.
  **Hay que actualizar esa línea cada mes**, junto con los valores. Si nadie la
  actualiza, cotiza al normal, que es el comportamiento seguro.
- **¿Los precios especiales de octubre son los mismos?** Septiembre quedó en
  $37 (10 años) y $25 (5 años). Conviene que nos avisen unos días antes de cada
  cambio, o definir de una vez si la promoción es permanente.
- **¿El traslado del personal y el alquiler de escaleras o andamios siguen
  incluidos?** Estaban en el material anterior y no aparecen en el documento de
  septiembre, que lista material, mano de obra, limpieza previa y garantía con
  respaldo posventa. Son argumentos de venta fuertes: si siguen incluidos,
  vuelven a la lista.
- **El descuento del 10% por efectivo o transferencia, ¿se calcula sobre el
  subtotal sin IVA o sobre el total con IVA?** Da montos distintos.
- **Confirmar el criterio de zona.** Se está tomando: todo lo que esté en Quito
  y sus valles paga precio estándar con mínimo de 5 m²; todo el resto del país
  paga el adicional de $10 por m² con mínimo de 20, seguridad incluida. Si
  alguna ciudad o valle es excepción, avisar.
- **El documento menciona materiales de 15, 10 y 5 años, pero la lista de
  precios solo trae 10 y 5.** ¿Sigue existiendo el de 15 años? ¿A qué precio?
- **Seguridad arquitectónica, ¿solo viene en 10 años de garantía?**
- ~~**Control solar para techos de vidrio: precio, garantías y mínimo.**~~
  **Resuelto:** mismo precio y mismas dos calidades que ventanas, y los mismos
  mínimos por zona.
- **Roof Shield®** (control solar exterior para pérgolas): ¿sigue existiendo
  ahora que techos es un producto propio? ¿Tiene precio distinto?
- **El mínimo de 20 m² fuera de Quito, ¿aplica también a un trabajo vehicular?**

## 2. Vehicular · resuelto, con una duda

Confirmado: el agente no cotiza vehicular, solo releva el **modelo de vehículo**
—que define si es sedán, SUV, camioneta simple o doble cabina, furgoneta— y el
**nivel de seguridad** que busca. Después llama un asesor.

- **¿Hay algún rango o "desde" que el agente pueda mencionar**, para no dejar
  la conversación sin ninguna referencia de precio?

## 3. Qué pasa cuando el agente termina · resuelto

El agente **no agenda la visita**. Releva toda la información, pregunta qué día
le queda cómodo al cliente, y sube todo a Kommo para que llame un vendedor y
cierre la visita. No hace falta calendario ni disponibilidad en tiempo real.

Falta:

- **¿Qué información necesita SÍ o SÍ un asesor para llamar con sentido?** El
  documento dice "información detallada del pedido y referencias", que es
  demasiado vago para programarlo.
- **¿A quién y por qué medio se avisa cuando el agente deriva a un humano?**
- **Si un vendedor entra a contestar manualmente, el agente se calla.** ¿Cómo
  se dan cuenta ustedes de que eso pasó?

## 4. Operación · IMPORTANTE

- **Horario de atención real**, en hora de Ecuador. Hoy está puesto 09:00-18:00
  de lunes a viernes, que es un valor por defecto, no un dato de ustedes.
- **Plazos de instalación** típicos, desde que se aprueba el presupuesto.
- **Formas de pago.** ¿Piden anticipo? ¿Qué porcentaje?

## 5. Cosas que el agente hoy no puede responder · IMPORTANTE

Cada una de estas es una consulta real que va a llegar y que hoy termina en
"lo confirma un asesor". Cuantas menos queden así, más útil es el agente.

- **Rotura térmica.** Laminar sobre vidrio templado o laminado tiene un riesgo
  de rotura por acumulación de calor. El documento no lo menciona. ¿Cómo lo
  manejan? Hasta que esté definido, el agente no confirma factibilidad de
  instalación sobre ningún vidrio: eso lo determina la visita.
- **Remoción de lámina vieja.** ¿La hacen? ¿Se cobra aparte?
- **¿Venden material sin instalación**, a terceros o instaladores?
- **¿Trabajan con constructoras y arquitectos?** No aparece en el material y
  sería el lead de mayor valor: hay que saber si se los trata distinto.
- **Consultas de fuera de Ecuador.** ¿Se descartan sin más, o hay algo que
  ofrecerles?

## 6. Para calibrar la calificación · KICK OFF

El puntaje que el agente le pone a cada consulta tiene que coincidir con el
criterio de los vendedores. Para eso necesito su criterio, no el mío.

- **¿Qué pregunta un asesor en los primeros dos minutos de una llamada?** Esas
  son, casi seguro, las preguntas que tiene que hacer el agente.
- **Cinco consultas reales que valieron la pena y cinco que no.** Con eso se
  calibra el puntaje y se verifica que dé lo mismo que darían ustedes.
- **¿Qué hace que una consulta sea buena?** ¿Los metros? ¿La zona? ¿Que sea
  empresa? ¿Que tenga apuro?
- **¿Cuánto es un trabajo chico, uno normal y uno grande**, en metros?

## 7. Revisión del material · IMPORTANTE

- **Alguien de MasterShield tiene que leer y aprobar `conocimiento.md`**, el
  resumen técnico que usa el agente. Es su única fuente de verdad: si ahí hay
  un error, se lo va a repetir a cada cliente con total seguridad.
- **El documento de preguntas frecuentes es de 2025.** ¿Siguen vigentes las
  marcas, las garantías de 15/10/5 años y los formatos de rollo de 1.82 y
  1.52 m?
- **El documento dice que también venden control solar vehicular**, pero el
  sitio solo lista seguridad vehicular. ¿Cuál de los dos está desactualizado?
- **Las descripciones de producto salen tal cual, con cuatro retoques.** Se
  corrigieron "eliminado", "remplaza" y "mateniendo", y "tus ventanas" pasó a
  "sus ventanas": el agente nunca tutea y la ficha sale con su voz. Si prefieren
  el texto original exacto, se vuelve atrás.
- **El control solar "ayuda a mantener espacios más frescos"**, dice la
  descripción nueva, y el documento de preguntas frecuentes insiste en que la
  lámina no enfría. Las dos cosas se pueden sostener —entra menos calor, pero no
  enfría como un aire acondicionado— y así lo explica el agente si le preguntan.
  Conviene que lo confirmen.
- **El video tiene un error de tipeo:** dice "satisfación" en lugar de
  "satisfacción". Y hoy es el institucional de 28 segundos: cuando esté el clip
  de los cuatro productos, se reemplaza el archivo y no hay que tocar código.

## 8. Kommo · cuando haya acceso

- Subdominio de la cuenta y un token de larga duración.
- Qué embudo y qué etapas usan hoy para estas consultas.
- Qué campos personalizados quieren que se completen.
