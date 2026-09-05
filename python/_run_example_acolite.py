"""Corrida de ejemplo sobre las escenas YA descargadas hasta el momento (11 S2,
pr_main_east, agosto 2026). No toca inventory/scenes.csv -- la descarga real
sigue de fondo y lo reescribe en cada checkpoint con su propia vista (sin
acolite_status), asi que cualquier escritura aqui se perderia. Esta corrida usa
una foto fija en memoria y escribe los L2W reales a disco (eso si es seguro,
solo el proceso de descarga escribe scenes.csv, no la carpeta L2W/).
Salida en storage_root/L2W/... (ruta real de produccion, no _smoke_test)."""
import csv, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as spc
import sp_acolite as spa

cfg = spc.load_config()
inv_path = spc.inventory_path(cfg)
with open(inv_path, newline="") as f:
    snapshot = list(csv.DictReader(f))

rows = [r for r in snapshot if r["download_status"] == "done"]
print(f"Escenas 'done' en la foto actual: {len(rows)}")

groups = defaultdict(list)
for r in rows:
    acq = spc.parse_acquisition_datetime(r["sensor"], r["scene_id"])
    r = dict(r)
    r["datetime"] = acq
    groups[(r["aoi"], r["sensor"], acq[:10])].append(r)

print(f"Agrupadas en {len(groups)} jobs (aoi, sensor, fecha):")
for k, v in sorted(groups.items()):
    print(f"  {k} -> {len(v)} tile(s)")

for (aoi_name, sensor, date), grouped_rows in sorted(groups.items()):
    print(f"\n=== Procesando {aoi_name}/{sensor}/{date} ({len(grouped_rows)} tile(s)) ===")
    spa.process_group(cfg, aoi_name, sensor, grouped_rows,
                       product_sets=["wq", "sargassum"], res_override=None,
                       keep_l1=True, dry_run=False)
