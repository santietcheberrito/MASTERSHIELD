import asyncio
import json
from app import db
from app.config import obtener_settings

async def main():
    s = obtener_settings()
    await db.iniciar(s.database_url)
    
    convs = await db.consultar("SELECT id, canal, identificador, estado, creada_en, ultimo_mensaje_en FROM conversaciones ORDER BY id DESC LIMIT 5")
    for c in convs:
        cid = c["id"]
        print(f"\n=================== CONVERSACION {cid} ({c['identificador']}) ===================")
        print("Estado:", c["estado"], "| Creada:", c["creada_en"], "| Ultimo msg:", c["ultimo_mensaje_en"])
        
        detalles = await db.consultar_una("SELECT datos, score, clasificacion, crm_referencia FROM conversaciones WHERE id = $1", cid)
        if detalles:
            print("Datos:", detalles["datos"])
            
        msgs = await db.consultar("SELECT id, rol, tipo, contenido, id_externo, procesado, creado_en FROM mensajes WHERE conversacion_id = $1 ORDER BY id ASC", cid)
        print(f"Total mensajes: {len(msgs)}")
        for m in msgs:
            print(f"[{m['creado_en'].strftime('%H:%M:%S')} | {m['rol']:<7} | id={m['id']}] {m['contenido'][:150]!r}")
            if m["id_externo"]:
                print(f"    id_externo: {m['id_externo']}")

    print("\n=================== PENDIENTES ===================")
    pends = await db.consultar("SELECT * FROM pendientes")
    for p in pends:
        print(dict(p))

    print("\n=================== EVENTOS RECIENTES ===================")
    evs = await db.consultar("SELECT id, conversacion_id, tipo, estado, detalle, creado_en FROM eventos ORDER BY id DESC LIMIT 15")
    for e in evs:
        print(f"[{e['creado_en'].strftime('%H:%M:%S')}] Conv {e['conversacion_id']} | {e['tipo']} | {e['estado']} | {e['detalle']}")

    await db.cerrar()

if __name__ == "__main__":
    asyncio.run(main())
