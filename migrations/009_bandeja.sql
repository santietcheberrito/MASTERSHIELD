-- Donde vive cada conversacion del lado de la bandeja humana.
--
-- Se guarda aparte de `crm_referencia` porque son dos destinos distintos con
-- ciclos de vida distintos: el CRM recibe el lead una vez cerrada la
-- calificacion, la bandeja espeja cada mensaje en el momento.
ALTER TABLE conversaciones
    ADD COLUMN IF NOT EXISTS bandeja_referencia jsonb;
