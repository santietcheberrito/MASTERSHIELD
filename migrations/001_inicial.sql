-- 001_inicial.sql — esquema base del agente de calificacion.
--
-- Decisiones que aplican a todo el archivo:
--
--   * Todas las marcas de tiempo son timestamptz. Railway corre en UTC y el
--     negocio vive en UTC-5: la base guarda el instante y la conversion a hora
--     de Ecuador se hace en Python, nunca al reves.
--   * Los estados son text + CHECK y no un enum de PostgreSQL. Agregar un
--     estado nuevo con enum obliga a un ALTER TYPE que no corre dentro de una
--     transaccion en versiones viejas, y asyncpg lo devolveria como objeto
--     propio en vez de str. Con text + CHECK es una migracion de una linea.
--   * Las claves son bigserial. No hay sharding ni IDs que viajen fuera del
--     sistema; un UUID solo agregaria 8 bytes por fila y ruido en los logs.

CREATE EXTENSION IF NOT EXISTS vector;

-- Mantiene actualizada_en sin depender de que cada UPDATE de la app se acuerde.
CREATE OR REPLACE FUNCTION tocar_actualizada_en() RETURNS trigger AS $$
BEGIN
    NEW.actualizada_en := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ---------------------------------------------------------------------------
-- conversaciones — una por telefono, para siempre.
--
-- La reactivacion despues de 24hs retoma esta misma fila en vez de crear otra
-- (CLAUDE.md, "Handoff y pausa"), por eso el telefono es unico y no hay una
-- nocion de "sesion" separada.
--
-- Los datos relevados van en un jsonb y no en columnas: los campos a relevar
-- son una hipotesis que se valida en el kick off, y no quiero una migracion
-- cada vez que el cliente cambia un criterio comercial.
--
-- El reintento de Kommo vive aca (kommo_pendiente / kommo_reintentar_en) y no
-- en `eventos`: asi `eventos` queda como auditoria pura y no se convierte en
-- una cola de trabajo disfrazada.
-- ---------------------------------------------------------------------------
CREATE TABLE conversaciones (
    id                      bigserial PRIMARY KEY,
    telefono                text        NOT NULL,
    nombre                  text,
    estado                  text        NOT NULL DEFAULT 'activa'
        CHECK (estado IN ('activa', 'pausada', 'calificada', 'derivada', 'cerrada')),

    datos                   jsonb       NOT NULL DEFAULT '{}'::jsonb,

    score                   smallint    CHECK (score BETWEEN 0 AND 100),
    clasificacion           text,
    detalle_score           jsonb,

    kommo_contacto_id       bigint,
    kommo_lead_id           bigint,
    kommo_sincronizada_en   timestamptz,
    kommo_pendiente         boolean     NOT NULL DEFAULT false,
    kommo_reintentar_en     timestamptz,
    kommo_intentos          smallint    NOT NULL DEFAULT 0,

    ultimo_mensaje_en       timestamptz,
    creada_en               timestamptz NOT NULL DEFAULT now(),
    actualizada_en          timestamptz NOT NULL DEFAULT now()
);

-- El telefono se guarda normalizado en E.164 (+593...). La unicidad es lo que
-- hace que dos mensajes simultaneos del mismo numero no creen dos conversaciones.
CREATE UNIQUE INDEX conversaciones_telefono_uk ON conversaciones (telefono);

-- Solo las que esperan reintento de Kommo: son pocas y se consultan seguido.
CREATE INDEX conversaciones_kommo_pendiente_ix
    ON conversaciones (kommo_reintentar_en) WHERE kommo_pendiente;

-- Para /metricas: conversaciones del dia.
CREATE INDEX conversaciones_creada_en_ix ON conversaciones (creada_en DESC);

CREATE TRIGGER conversaciones_tocar_actualizada_en
    BEFORE UPDATE ON conversaciones
    FOR EACH ROW EXECUTE FUNCTION tocar_actualizada_en();


-- ---------------------------------------------------------------------------
-- mensajes — historial completo, entrante y saliente.
--
-- `rol` tiene tres valores y no dos: 'vendedor' es el mensaje saliente que el
-- agente no genero, que es como se detecta la intervencion humana para pasar
-- la conversacion a 'pausada'.
--
-- `wa_message_id` es unico pero acepta NULL, y eso es deliberado: los mensajes
-- que produce el agente todavia no tienen id del BSP cuando se guardan, y en
-- PostgreSQL un indice unico admite varios NULL. La deduplicacion del webhook
-- se hace con INSERT ... ON CONFLICT DO NOTHING RETURNING id y no con un
-- SELECT previo: si el BSP reintenta en paralelo, el chequeo previo tiene
-- carrera y el indice no.
--
-- `payload` guarda el mensaje crudo del BSP. Todavia no sabemos que BSP se va
-- a usar; guardar el original permite volver a parsear sin pedirle al cliente
-- que reproduzca la conversacion.
-- ---------------------------------------------------------------------------
CREATE TABLE mensajes (
    id              bigserial PRIMARY KEY,
    conversacion_id bigint      NOT NULL REFERENCES conversaciones (id) ON DELETE CASCADE,
    rol             text        NOT NULL CHECK (rol IN ('cliente', 'agente', 'vendedor')),
    tipo            text        NOT NULL DEFAULT 'texto'
        CHECK (tipo IN ('texto', 'imagen', 'audio', 'video', 'documento', 'ubicacion', 'otro')),
    contenido       text        NOT NULL DEFAULT '',
    wa_message_id   text,
    payload         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    procesado       boolean     NOT NULL DEFAULT false,
    creado_en       timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX mensajes_wa_message_id_uk ON mensajes (wa_message_id);

-- El historial se arma ordenando por id, no por creado_en: now() es constante
-- dentro de una transaccion y una rafaga de mensajes puede compartir timestamp.
CREATE INDEX mensajes_conversacion_ix ON mensajes (conversacion_id, id);

-- El worker junta el turno con esta consulta; el indice parcial la deja barata
-- aunque el historial crezca.
CREATE INDEX mensajes_sin_procesar_ix ON mensajes (conversacion_id, id) WHERE NOT procesado;


-- ---------------------------------------------------------------------------
-- pendientes — el buffer de debounce.
--
-- conversacion_id es la clave primaria, y eso es justamente lo que hace posible
-- el debounce: ON CONFLICT (conversacion_id) DO UPDATE SET procesar_despues =
-- now() + ventana reinicia el timer en una sola query, sin leer antes.
--
-- El lock es un timestamptz y no un boolean: si Railway mata el proceso a mitad
-- de un turno, bloqueado_hasta vence solo y la conversacion se retoma. Un
-- boolean quedaria en true para siempre y esa conversacion no se contestaria
-- nunca mas.
-- ---------------------------------------------------------------------------
CREATE TABLE pendientes (
    conversacion_id  bigint PRIMARY KEY REFERENCES conversaciones (id) ON DELETE CASCADE,
    procesar_despues timestamptz NOT NULL,
    bloqueado_hasta  timestamptz,
    intentos         smallint    NOT NULL DEFAULT 0,
    ultimo_error     text,
    creado_en        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX pendientes_procesar_despues_ix ON pendientes (procesar_despues);


-- ---------------------------------------------------------------------------
-- catalogo — chunks de la base de conocimiento.
--
-- `prioridad` existe porque las negaciones del documento del cliente ("no aisla
-- termicamente", "no reduce el ruido") son la parte mas importante del material
-- y tienen que poder pesar mas que un parrafo comercial cualquiera.
--
-- No se crea indice ivfflat ni hnsw a proposito: el catalogo son decenas o
-- cientos de chunks, un seq scan sobre eso es instantaneo, y un ivfflat con
-- pocas filas devuelve peores resultados que no tenerlo. Se agrega el dia que
-- el catalogo crezca de verdad.
-- ---------------------------------------------------------------------------
CREATE TABLE catalogo (
    id        bigserial PRIMARY KEY,
    documento text         NOT NULL,
    titulo    text,
    contenido text         NOT NULL,
    prioridad smallint     NOT NULL DEFAULT 0,
    metadata  jsonb        NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(1536) NOT NULL,
    creado_en timestamptz  NOT NULL DEFAULT now()
);

-- Para poder recargar un documento entero sin tocar los demas.
CREATE INDEX catalogo_documento_ix ON catalogo (documento);


-- ---------------------------------------------------------------------------
-- eventos — auditoria. Que se mando a Kommo, que fallo, que se reintento.
--
-- `tipo` no tiene CHECK: la lista crece con cada modulo y no quiero una
-- migracion por cada tipo de evento nuevo. `estado` si lo tiene, porque son
-- tres y de ahi sale la metrica de errores.
-- ---------------------------------------------------------------------------
CREATE TABLE eventos (
    id              bigserial PRIMARY KEY,
    conversacion_id bigint      REFERENCES conversaciones (id) ON DELETE SET NULL,
    tipo            text        NOT NULL,
    estado          text        NOT NULL DEFAULT 'ok'
        CHECK (estado IN ('ok', 'error', 'reintento')),
    detalle         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    creado_en       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX eventos_conversacion_ix ON eventos (conversacion_id, creado_en DESC);
CREATE INDEX eventos_errores_ix ON eventos (creado_en DESC) WHERE estado = 'error';
