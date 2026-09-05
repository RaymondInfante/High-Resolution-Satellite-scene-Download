"""Prueba puntual de sp_acolite.py sobre las 4 escenas ya descargadas -- NO toca
scenes.csv (la descarga real sigue corriendo de fondo y lo reescribe). Solo mide
tiempo/tamano real de ACOLITE. Salida en storage_root/_smoke_test/, separada del
layout real, para poder borrarla despues sin afectar nada."""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as spc
import sp_acolite as spa

cfg = spc.load_config()
root = spc.require_storage(cfg, prefix="smoke")
s2_dir = root / "S2" / "L1C"

jobs = [
    ("2026-08-28_merge", [
        str(s2_dir / "S2A_MSIL1C_20260828T145751_N0512_R039_T19QHA_20260829T010322.SAFE"),
        str(s2_dir / "S2A_MSIL1C_20260828T145751_N0512_R039_T19QHV_20260829T010322.SAFE"),
    ]),
    ("2026-08-09_single", [
        str(s2_dir / "S2C_MSIL1C_20260809T150721_N0512_R082_T20QKE_20260809T183127.SAFE"),
    ]),
    ("2026-08-04_single", [
        str(s2_dir / "S2B_MSIL1C_20260804T150719_N0512_R082_T20QKE_20260804T182348.SAFE"),
    ]),
]

for tag, tiles in jobs:
    out_dir = root / "_smoke_test" / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = spa.build_settings(cfg, "pr_main_east", "S2", tiles, out_dir, ["wq"], None)
    print(f"\n=== {tag} ({len(tiles)} tile(s)) ===")
    print("inputfile:", [Path(t).name for t in tiles])
    t0 = time.time()
    ok = spa.run_acolite_subprocess(cfg, settings, out_dir=out_dir, prefix="smoke")
    if ok:
        for nc in sorted(out_dir.glob("*_L2W.nc")):
            if not spa.export_l2w_geotiffs(cfg, nc, prefix="smoke"):
                ok = False
    elapsed = time.time() - t0
    outputs = spa.find_output_geotiffs(out_dir)
    size_mb = sum(f.stat().st_size for f in outputs) / (1024*1024) if outputs else 0
    print(f"RESULTADO {tag}: ok={ok} tiempo={elapsed:.0f}s salidas={len(outputs)} tamano={size_mb:.1f}MB")
