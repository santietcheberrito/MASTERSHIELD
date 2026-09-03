-- 006_pausa_con_vencimiento.sql — la pausa por intervencion humana termina sola.
--
-- Hasta ahora `pausada` era un estado sin salida: un vendedor contestaba a
-- mano, el agente se callaba, y no habia forma de que volviera. Eso esta bien
-- durante la conversacion con la persona, y muy mal a la semana siguiente,
-- cuando el mismo cliente escribe por otra cosa y nadie le contesta.
--
-- `pausada_hasta` le pone vencimiento. Cada mensaje del vendedor lo corre hacia
-- adelante, igual que un mensaje del cliente corre la ventana del debounce: si
-- el asesor sigue conversando, el agente sigue callado.

ALTER TABLE conversaciones ADD COLUMN pausada_hasta timestamptz;

COMMENT ON COLUMN conversaciones.pausada_hasta IS
    'Hasta cuando el agente no atiende esta conversacion. Lo corre cada '
    'mensaje del vendedor. NULL en cualquier estado que no sea pausada.';

-- El indice es parcial porque la consulta del despertador siempre filtra por
-- las dos condiciones, y las conversaciones pausadas son una minoria minuscula
-- de la tabla.
CREATE INDEX conversaciones_pausa_vencida_ix
    ON conversaciones (pausada_hasta)
    WHERE estado = 'pausada' AND pausada_hasta IS NOT NULL;
