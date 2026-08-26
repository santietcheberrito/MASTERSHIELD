-- 005_crm_generico.sql — las columnas de sincronizacion dejan de ser de Kommo.
--
-- Mientras no haya acceso a Kommo, el destino es una base de Notion que hace de
-- CRM de prueba. Son dos destinos distintos para el mismo dato, asi que llamar
-- `kommo_pendiente` a la marca de reintento seria mentira la mitad del tiempo.
--
-- `crm_referencia` es jsonb y no un bigint porque cada destino identifica el
-- registro a su manera: Notion con el id de una pagina, Kommo con dos ids —el
-- del contacto y el del lead—.

ALTER TABLE conversaciones RENAME COLUMN kommo_pendiente TO crm_pendiente;
ALTER TABLE conversaciones RENAME COLUMN kommo_reintentar_en TO crm_reintentar_en;
ALTER TABLE conversaciones RENAME COLUMN kommo_intentos TO crm_intentos;
ALTER TABLE conversaciones RENAME COLUMN kommo_sincronizada_en TO crm_sincronizada_en;

ALTER TABLE conversaciones DROP COLUMN kommo_contacto_id;
ALTER TABLE conversaciones DROP COLUMN kommo_lead_id;
ALTER TABLE conversaciones ADD COLUMN crm_referencia jsonb;

ALTER INDEX conversaciones_kommo_pendiente_ix RENAME TO conversaciones_crm_pendiente_ix;
