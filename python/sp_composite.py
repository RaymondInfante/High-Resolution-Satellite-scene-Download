#!/usr/bin/env python3
"""
sp_composite.py -- Codigo 4: mosaicos semanales/mensuales sobre la "Super
Imagen" fusionada (salida de sp_fusion.py en storage_root/Fused/<aoi>/<date>/).

Adapta el patron ya maduro de compute_monthly_composite() en
Water2Coast/R/SN3_Anomaly_Generation.R (apilar, colapsar por estadistico,
checkpoint por unidad) a Python/rasterio, pero con mejor-pixel-por-cobertura
en vez de media -- para no perder detalle de eventos puntuales (sargazo)
dentro de la ventana de composicion.

Uso:
    sp_composite.py --cadence weekly|monthly [--aoi ...] [--dry-run]

Metadata QA por mosaico (numero de fechas contribuyentes, cobertura valida,
sensores de origen) queda en un <periodo>/composite_meta.json junto a los
GeoTIFF -- corresponde al bloque "4.4 Metadatos y calidad" del diagrama.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date as date_cls
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as spc  # noqa: E402

PREFIX = "sp_composite"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--cadence", choices=["weekly", "monthly"], required=True)
    p.add_argument("--aoi", default=None)
    p.add_argument("--input-root", default="Fused",
                   help="Subcarpeta bajo storage_root a componer (default: Fused, "
                        "la salida de sp_fusion.py). Tambien puede apuntar directo a "
                        "'L2W/S2' o 'L2W/L89' si todavia no hay fusion multi-sensor.")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def period_key(d: date_cls, cadence: str) -> str:
    if cadence == "monthly":
        return f"{d.year:04d}-{d.month:02d}"
    iso = d.isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


def find_date_dirs(root: Path, input_root: str, aoi: str) -> dict[str, Path]:
    base = root / input_root / aoi
    if not base.exists():
        return {}
    out = {}
    for d in base.iterdir():
        if not d.is_dir():
            continue
        try:
            date_cls.fromisoformat(d.name)
        except ValueError:
            continue
        out[d.name] = d
    return out


def group_by_period(date_dirs: dict[str, Path], cadence: str) -> dict[str, dict[str, Path]]:
    groups = defaultdict(dict)
    for date_str, path in date_dirs.items():
        d = date_cls.fromisoformat(date_str)
        groups[period_key(d, cadence)][date_str] = path
    return groups


def composite_band(band_name: str, contributing: dict[str, Path], prefix=PREFIX):
    """contributing: {date_str: dir_path}. Devuelve (array, profile, meta) o
    (None, None, None) si ninguna fecha tiene esa banda."""
    import numpy as np
    import rasterio

    arrays, dates_used, profile = [], [], None
    for date_str, d in sorted(contributing.items()):
        tif = d / f"{band_name}.tif"
        if not tif.exists():
            continue
        with rasterio.open(tif) as src:
            arr = src.read(1).astype("float64")
            nodata = src.nodata
            if profile is None:
                profile = src.profile
        if nodata is not None and not (isinstance(nodata, float) and nodata != nodata):
            arr = np.where(arr == nodata, np.nan, arr)
        arrays.append(arr)
        dates_used.append(date_str)

    if not arrays:
        return None, None, None

    fracs = [float(np.mean(~np.isnan(a))) for a in arrays]
    order = sorted(range(len(arrays)), key=lambda i: fracs[i], reverse=True)
    composite = np.full_like(arrays[0], np.nan)
    for i in order:
        mask = np.isnan(composite) & ~np.isnan(arrays[i])
        composite[mask] = arrays[i][mask]

    meta = {
        "n_dates_contributing": len(arrays),
        "dates_contributing": dates_used,
        "coverage": float(np.mean(~np.isnan(composite))),
    }
    return composite, profile, meta


def source_sensor_breakdown(contributing: dict[str, Path]) -> dict:
    """Lee source_sensor.tif de cada fecha (si existe, de sp_fusion.py) y
    resume que fraccion del periodo vino de cada sensor."""
    import numpy as np
    import rasterio

    counts = {"S2": 0, "L89": 0, "none": 0}
    total = 0
    for d in contributing.values():
        tif = d / "source_sensor.tif"
        if not tif.exists():
            continue
        with rasterio.open(tif) as src:
            arr = src.read(1)
        counts["none"] += int(np.sum(arr == 0))
        counts["S2"] += int(np.sum(arr == 1))
        counts["L89"] += int(np.sum(arr == 2))
        total += arr.size
    if total == 0:
        return {}
    return {k: round(100 * v / total, 1) for k, v in counts.items()}


def process_period(cfg: dict, aoi: str, cadence: str, period: str,
                    contributing: dict[str, Path], out_root: Path, prefix=PREFIX):
    import rasterio

    all_bands = set()
    for d in contributing.values():
        for f in d.glob("*.tif"):
            if f.name != "source_sensor.tif" and not f.name.startswith("._"):
                all_bands.add(f.stem)
    if not all_bands:
        spc.log(prefix, f"  [{aoi}/{period}] sin bandas encontradas en las fechas contribuyentes -- omitido.")
        return

    out_dir = out_root / "Composites" / cadence / aoi / period
    out_dir.mkdir(parents=True, exist_ok=True)

    band_meta = {}
    for band in sorted(all_bands):
        composite, profile, meta = composite_band(band, contributing)
        if composite is None:
            continue
        out_profile = profile.copy()
        out_profile.update(dtype="float32", nodata=float("nan"), count=1)
        with rasterio.open(out_dir / f"{band}.tif", "w", **out_profile) as dst:
            dst.write(composite.astype("float32"), 1)
        band_meta[band] = meta

    period_meta = {
        "aoi": aoi, "cadence": cadence, "period": period,
        "n_dates_available": len(contributing),
        "dates": sorted(contributing.keys()),
        "sensor_breakdown_pct": source_sensor_breakdown(contributing),
        "bands": band_meta,
    }
    (out_dir / "composite_meta.json").write_text(json.dumps(period_meta, indent=2))
    spc.log(prefix, f"  [{aoi}/{period}] {cadence}: {len(contributing)} fecha(s) -> "
                     f"{out_dir} ({len(band_meta)} bandas)")


def main():
    args = parse_args()
    cfg = spc.load_config()
    root = spc.require_storage(cfg, prefix=PREFIX)

    all_aois = [k for k in cfg["aois"] if not k.startswith("_")]
    aois = [a.strip() for a in args.aoi.split(",")] if args.aoi else all_aois

    for aoi in aois:
        date_dirs = find_date_dirs(root, args.input_root, aoi)
        if not date_dirs:
            continue
        groups = group_by_period(date_dirs, args.cadence)
        spc.log(PREFIX, f"[{aoi}] {len(date_dirs)} fecha(s) -> {len(groups)} periodo(s) {args.cadence}")
        for period, contributing in sorted(groups.items()):
            if args.dry_run:
                spc.log(PREFIX, f"  DRY-RUN {aoi}/{period}: {len(contributing)} fecha(s) -> "
                                 f"{sorted(contributing.keys())}")
                continue
            process_period(cfg, aoi, args.cadence, period, contributing, root)


if __name__ == "__main__":
    main()
