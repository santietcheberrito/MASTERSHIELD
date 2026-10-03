-- Que respuestas se usan solo en ciertas zonas.
--
-- En Quito y sus valles la visita tecnica es gratis y esta cerca, asi que al
-- cerrar se ofrece la visita. Fuera de Quito se ofrece la llamada con un asesor
-- y es el asesor quien ve si la visita corresponde (pedido de MasterShield,
-- 29/9/2026).
--
-- NULL = la respuesta sirve en todas las zonas, que es el caso de casi todas.
ALTER TABLE respuestas ADD COLUMN IF NOT EXISTS solo_zonas text[];

COMMENT ON COLUMN respuestas.solo_zonas IS
    'Zonas donde aplica esta respuesta; NULL = todas';
