# Consultas a MasterShield®

Lo que falta para terminar el agente. Ordenado por qué bloquea: lo de arriba
impide escribir código, lo de abajo se puede completar después.

Nada de esto se puede deducir ni estimar. Son datos de negocio.

---

## 1. Precios · BLOQUEA

Sin esto no existe la herramienta que cotiza.

- **Valor por m² de cada uno de los 4 productos.**
- **¿Cuáles son las regiones?** El precio varía por región del país, pero no sé
  si son Sierra / Costa / Amazonía, si es por ciudad, o si es "Quito y
  alrededores" contra "resto del país". Necesito la lista y el precio de cada
  producto en cada una.
- **¿El precio incluye la instalación o es solo el material?**
- **¿Incluye IVA?** Es 15%. Si el agente dice "son 400" y al cliente le llegan
  460, es un problema con un cliente real, no un detalle de redacción.
- **¿Está en dólares?**
- **Roof Shield®** (control solar para instalación exterior en pérgolas):
  ¿tiene el mismo precio que el control solar normal o uno distinto?
- **Fuera de Quito hay montos mínimos de instalación por ciudad.** ¿Cuáles son?
  ¿Siguen existiendo aparte del precio por región, o ya están incluidos ahí?

## 2. El mínimo de venta · BLOQUEA

Ya sabemos que son 5 m². Falta:

- **¿Es por producto o por pedido?** Si alguien quiere 3 m² de una lámina y
  4 m² de otra, ¿son 7 y se puede, o no llega ninguno de los dos?
- **¿Aplica a la línea vehicular?** Ahí el m² no es la unidad natural.
- **¿Cómo se cotiza un trabajo vehicular?** ¿Por vehículo? ¿Cambia según el
  tipo de auto?

## 3. Qué pasa cuando el agente termina · BLOQUEA

Esta es la que más cambia el diseño y todavía no está definida.

- **¿El agente agenda la visita técnica, o solo releva y después llama un
  asesor?** El material dice que el evento importante es agendar la visita,
  pero también que el handoff es telefónico. Si el agente agenda, necesito
  saber días, horarios disponibles y si hay un calendario que consultar.
- **¿Qué información necesita SÍ o SÍ un asesor para agendar la visita?** El
  documento dice "información detallada del pedido y referencias", que es
  demasiado vago para programarlo. Concretamente: ¿qué tiene que saber el
  asesor antes de aceptar ir?
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

## 8. Kommo · cuando haya acceso

- Subdominio de la cuenta y un token de larga duración.
- Qué embudo y qué etapas usan hoy para estas consultas.
- Qué campos personalizados quieren que se completen.
