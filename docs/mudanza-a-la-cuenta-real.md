# Mudar el agente a la cuenta de Kommo de MasterShield

Todo lo que se probó hasta acá vive en una cuenta de prueba nuestra. Los IDs de
etapas, campos y catálogo son de esa cuenta y **no significan nada en otra**: no
se copian, se vuelven a descubrir.

El código no cambia. Lo que cambia es `config/kommo.yaml`, que se regenera solo.

---

## Antes de empezar, pedirle a MasterShield

1. **Un usuario en su Kommo para la integración**, con permisos de leads,
   contactos, tareas, listas y **archivos**. El de archivos es un scope aparte y
   se olvida: sin él, las fotos que manda un cliente no se pueden adjuntar al
   lead. El verificador lo detecta, pero mejor pedirlo de entrada.
2. **El token de larga duración** de ese usuario, y el subdominio de la cuenta.
3. **A quién se le asignan las tareas de llamado.** Kommo exige un responsable
   en toda tarea; hoy quedan a nombre del dueño del token. Puede ser una persona
   o un usuario "Mesa de entrada" que los vendedores se reasignen al tomarlas.
4. **Los precios del mes en curso**, para cargar el catálogo con valores reales
   y no con los de la cuenta de prueba.

---

## La mudanza

```bash
# 1. Las credenciales nuevas en el .env
KOMMO_SUBDOMAIN=elsubdominiodeellos
KOMMO_ACCESS_TOKEN=...

# 2. Ver qué va a hacer, sin tocar nada
python3.12 scripts/preparar_kommo.py --revisar

# 3. Hacerlo
python3.12 scripts/preparar_kommo.py

# 4. Confirmar que quedó lista
python3.12 scripts/preparar_kommo.py --verificar
```

El paso 2 es el que importa: descubre la cuenta y el embudo, y lista **exactamente**
qué etapas, campos y productos va a crear. Si algo se ve raro ahí, se para antes
de tocar la cuenta del cliente.

El script es idempotente y reconoce lo que ya existe, así que correrlo dos veces
no duplica nada. Si el cliente ya tiene una etapa llamada igual, la reutiliza.

### Qué hace cada paso

**Descubre** la cuenta, el embudo principal y las etapas de sistema —la de
entrada y la de perdido, que se reutilizan en vez de crear unas propias—. Antes
esto se leía del YAML y por eso fallaba con un 404 en una cuenta nueva.

**Crea las cinco etapas del agente** y las ordena juntas después de la de
entrada. Dos cosas aprendidas peleándose con esto y que están en el código: el
PATCH de Kommo *reemplaza* la etapa en vez de fusionarla —si va solo `sort`, se
pierden nombre y color— y hay que darle posiciones bien espaciadas o Kommo las
renormaliza y salen invertidas.

**Crea los 16 campos del lead**, con sus listas de opciones.

**Carga el catálogo de productos** con una fila por calidad y el SKU que la
enlaza al producto interno (`id_del_producto:garantia`). Ese SKU es la clave: sin
él, un renombre en Kommo dejaría al agente sin saber qué fila es cuál.

**Guarda todos los IDs** en `config/kommo.yaml`.

---

## Después

**Completar `usuarios.responsable_tareas`** en `config/kommo.yaml` con el id que
imprime `--verificar`. Sin eso, el aviso de que alguien necesita atención le
suena a la persona equivocada.

**Configurar el Digital Pipeline**, que es de ellos y no requiere código:
disparador "el lead entra a la etapa Derivado a un asesor", con la acción que
prefieran —notificar, cambiar responsable, mandar correo—. Nosotros ya movemos
el lead a esa etapa; esto decide a quién le llega.

**Una conversación de prueba de punta a punta** antes de dar por terminado: que
el lead aparezca en la etapa correcta, con los campos llenos, la nota con la
transcripción, la tarea de llamado y —si se mandó una foto— el archivo adjunto.

---

## Lo que queda para más adelante

**Que MasterShield edite sus precios desde Kommo.** El catálogo ya queda cargado
con la estructura correcta, así que la mitad del trabajo está hecha. Falta que el
agente los lea de ahí en vez del YAML, con tres guardarraíles: cache corto,
validación de rango —un precio en cero o diez veces el anterior se rechaza— y el
YAML como respaldo si Kommo no responde. Medio día.

Hoy cambiar un precio son cinco minutos nuestros, y `/metricas` avisa cinco días
antes de que venza la promoción del mes, así que no urge.

**Ojo con una limitación de Kommo:** los elementos de un catálogo **no se borran
por API**, sólo desde la interfaz. Si el script carga algo de más, hay que sacarlo
a mano desde Listas → Productos.
