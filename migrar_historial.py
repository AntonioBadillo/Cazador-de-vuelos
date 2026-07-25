#!/usr/bin/env python3
"""
Migracion de un solo uso: conserva el historial de precios acumulado cuando
se renombran o reestructuran tramos en config.json.

Que hace:
  1. Renombra "China -> Los Angeles" a "China -> Costa Oeste USA"
     (conserva las lecturas de PVG-LAX, HKG-LAX, etc.)
  2. Elimina "Los Angeles -> Panama (Tocumen)" del historial.
  3. Conserva "Tokio -> China" intacto.

Ejecutalo UNA vez, localmente o subelo y corre el workflow. Despues puedes
borrarlo del repo.
"""
import json
from pathlib import Path

H = Path(__file__).parent / "history.json"
data = json.loads(H.read_text())

# 1. Renombrar el tramo China (conserva por_ruta con sus lecturas)
viejo = "China -> Los Angeles"
nuevo = "China -> Costa Oeste USA"
if viejo in data and nuevo not in data:
    data[nuevo] = data.pop(viejo)
    print(f"OK: '{viejo}' renombrado a '{nuevo}' "
          f"({len(data[nuevo].get('por_ruta', {}))} rutas conservadas)")
elif nuevo in data:
    print(f"'{nuevo}' ya existe; nada que renombrar.")
else:
    print(f"No se encontro '{viejo}'.")

# 2. Eliminar el tramo LAX -> PTY
pty = "Los Angeles -> Panama (Tocumen)"
if pty in data:
    del data[pty]
    print(f"OK: '{pty}' eliminado del historial.")

# Guardar
H.write_text(json.dumps(data, indent=2, ensure_ascii=False))
print("Historial migrado. Ya puedes borrar este script.")
