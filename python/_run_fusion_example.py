"""Procesa el ejemplo de fusion real S2+Landsat: 4 tiles S2 (26-ago-2026) +
4 escenas Landsat (paths 004+005, 25/26-ago-2026, tratadas como UNA sola
pasada ya que juntas cubren el AOI). No toca scenes.csv en vivo -- foto fija
en memoria, igual que _run_example_acolite.py."""
import csv, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as spc
import sp_acolite as spa

cfg = spc.load_config()
with open(spc.inventory_path(cfg), newline="") as f:
    snapshot = list(csv.DictReader(f))

s2_rows = [r for r in snapshot if r["sensor"] == "S2" and r["aoi"] == "pr_main_east"
           and spc.parse_acquisition_datetime("S2", r["scene_id"])[:10] == "2026-08-26"]
l89_rows = [r for r in snapshot if r["sensor"] == "L89" and r["aoi"] == "pr_main_east"
            and spc.parse_acquisition_datetime("L89", r["scene_id"])[:10] in ("2026-08-25", "2026-08-26")]

print(f"S2 (26-ago): {len(s2_rows)} tile(s)")
for r in s2_rows: print(" ", r["scene_id"])
print(f"L89 (25/26-ago, una sola pasada): {len(l89_rows)} escena(s)")
for r in l89_rows: print(" ", r["scene_id"])

print("\n=== Procesando S2 ===")
spa.process_group(cfg, "pr_main_east", "S2", s2_rows,
                   product_sets=["wq", "sargassum"], res_override=None,
                   keep_l1=True, dry_run=False)

print("\n=== Procesando L89 (fecha unificada 2026-08-26 para calzar con S2) ===")
l89_rows_relabeled = [dict(r, datetime="2026-08-26") for r in l89_rows]
spa.process_group(cfg, "pr_main_east", "L89", l89_rows_relabeled,
                   product_sets=["wq", "sargassum"], res_override=None,
                   keep_l1=True, dry_run=False)
