-- 002_canales.sql — soporte de mas de un canal de mensajeria.
--
-- Por que: la etapa de pruebas corre sobre Telegram y produccion sobre la
-- Cloud API de WhatsApp. En WhatsApp el telefono viene en el payload; en
-- Telegram no existe, hay un chat_id y el numero solo si la persona decide
-- compartirlo.
--
-- Consecuencia de negocio, no solo tecnica: el handoff es telefonico, el
-- vendedor llama. Sobre Telegram el telefono pasa a ser un dato mas a relevar,
-- y hasta que el cliente lo de no hay a quien llamar. Por eso `telefono` deja
-- de ser obligatorio y la identidad de la conversacion pasa a ser el par
-- (canal, identificador).

ALTER TABLE conversaciones
    ADD COLUMN canal text NOT NULL DEFAULT 'whatsapp'
        CHECK (canal IN ('whatsapp', 'telegram')),
    ADD COLUMN identificador text;

-- En WhatsApp el identificador es el propio telefono en E.164; en Telegram es
-- el chat_id. Las filas que ya existan (ninguna al momento de escribir esto)
-- son de WhatsApp por el DEFAULT de arriba.
UPDATE conversaciones SET identificador = telefono WHERE identificador IS NULL;

ALTER TABLE conversaciones
    ALTER COLUMN identificador SET NOT NULL,
    ALTER COLUMN telefono DROP NOT NULL;

-- La unicidad se mueve al par. Dos personas distintas pueden tener el mismo
-- identificador en canales distintos sin pisarse.
DROP INDEX IF EXISTS conversaciones_telefono_uk;
CREATE UNIQUE INDEX conversaciones_canal_identificador_uk
    ON conversaciones (canal, identificador);

-- Kommo busca el contacto por telefono, asi que sigue habiendo indice, pero
-- ahora parcial: sobre Telegram la mayoria de las filas lo tiene en NULL hasta
-- que el agente lo consigue.
CREATE INDEX conversaciones_telefono_ix
    ON conversaciones (telefono) WHERE telefono IS NOT NULL;
