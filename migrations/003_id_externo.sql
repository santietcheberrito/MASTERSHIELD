-- 003_id_externo.sql — el id del mensaje deja de ser especifico de WhatsApp.
--
-- `wa_message_id` pasa a llamarse `id_externo` porque ahora guarda tambien el
-- id de Telegram. Y ahi hay una trampa: el message_id de Telegram es unico
-- **por chat**, no globalmente, asi que dos personas distintas pueden mandar
-- mensajes con el mismo numero. Guardarlo crudo bajo un indice unico global
-- haria que el mensaje de una persona descarte el de otra como si fuera un
-- duplicado.
--
-- Por eso el valor se guarda calificado por canal y chat:
--   whatsapp -> "whatsapp:wamid.ABC"
--   telegram -> "telegram:<chat_id>:<message_id>"

ALTER TABLE mensajes RENAME COLUMN wa_message_id TO id_externo;
ALTER INDEX mensajes_wa_message_id_uk RENAME TO mensajes_id_externo_uk;
