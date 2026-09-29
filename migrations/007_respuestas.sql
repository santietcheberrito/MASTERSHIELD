-- 007_respuestas.sql — las respuestas por situacion, en la base.
--
-- Hasta el 15/9/2026 cada caso de la conversacion vivia en el prompt (628
-- renglones) o en textos fijos que el codigo metia en el mismo mensaje que el
-- modelo. Los dos autores chocaban y salian mensajes duplicados.
--
-- Ahora el modelo es el unico que escribe. En cada turno se le pasan solo las
-- respuestas del paso en el que esta la conversacion, mas las que se parecen a
-- lo que escribio la persona. La fuente editable es conocimiento/respuestas.yaml
-- y se carga con scripts/cargar_conocimiento.py.

CREATE TABLE respuestas (
    id             bigserial PRIMARY KEY,
    -- Identificador estable de la situacion: el script actualiza por clave.
    clave          text        NOT NULL UNIQUE,
    -- En que paso del relevamiento aplica. `general` es para lo que puede
    -- pasar en cualquier momento (una pregunta tecnica, pedir un asesor).
    etapa          text        NOT NULL,
    -- Cuando usarla, dicho como lo leeria una persona.
    situacion      text        NOT NULL,
    -- La respuesta a usar, con {variables} y emojis. Una linea con --- separa
    -- un globo de WhatsApp del siguiente.
    respuesta      text        NOT NULL DEFAULT '',
    -- Lo que hay que hacer ademas: que herramienta llamar, que no decir.
    instrucciones  text        NOT NULL DEFAULT '',
    activa         boolean     NOT NULL DEFAULT true,
    embedding      vector(1536),
    actualizado_en timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX respuestas_etapa_ix ON respuestas (etapa) WHERE activa;
