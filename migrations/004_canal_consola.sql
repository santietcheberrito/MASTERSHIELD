-- 004_canal_consola.sql — un canal mas para el simulador.
--
-- `scripts/simular_conversacion.py` conversa con el agente por consola y
-- necesita persistir la conversacion como cualquier otra: el historial, los
-- datos relevados y el estado salen de las mismas tablas. Sin un canal propio
-- habria que ensuciar las conversaciones de Telegram con pruebas.

ALTER TABLE conversaciones DROP CONSTRAINT conversaciones_canal_check;
ALTER TABLE conversaciones ADD CONSTRAINT conversaciones_canal_check
    CHECK (canal IN ('whatsapp', 'telegram', 'consola'));
