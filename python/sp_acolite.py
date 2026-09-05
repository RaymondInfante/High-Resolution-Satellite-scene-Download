#!/usr/bin/env python3
"""
sp_acolite.py -- Codigo 3: procesamiento por lotes con ACOLITE sobre lo que ya
esta descargado (download_status=done en el inventario), con los productos L2W
configurables via config/products.json (o --products en la linea de comandos).

Uso:
    sp_acolite.py [--products wq,sargassum,clarity] [--aoi ...] [--sensor S2,L89]
                  [--res 20] [--reprocess] [--keep-l1] [--workers 3] [--dry-run]

La unidad de trabajo es (AOI, sensor, fecha de adquisicion), no la escena: si un
AOI cae sobre varias tiles/escenas de la misma pasada se procesan juntas con
merge_tiles=True (acolite/sentinel2/multi_tile_extent.py,
acolite/landsat/multi_tile_extent.py ya soportan esto de forma nativa),
produciendo un solo L2W por AOI por fecha.

ACOLITE se invoca por subprocess (launch_acolite.py --cli --nogfx --settings ...),
no en el mismo proceso -- ac.settings es estado global mutable de ACOLITE, y un
crash/OOM en un job no debe tumbar el resto del lote. Mismo patron que
Water2Coast/python/sn3_gpt_process.py usa para invocar gpt de SNAP.

process_job(cfg, row) es reutilizable desde sp_download.py --stream: procesa una
sola fila del inventario justo despues de que se descargo, y borra su L1 (salvo
que este en la lista de fechas protegidas de pipeline.json).

Al 2026-08-29: escrito pero NO ejecutado sobre ninguna escena real -- no hay
todavia ninguna fila download_status=done en el inventario, por instruccion
explicita del usuario de no descargar nada aun. Falta validar con datos reales:
tiempo por escena, tamano de salida (en particular el set 'reflectance', ver
advertencia en config/products.json), y que el enmascaramiento se vea sano en QGIS.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as spc  # noqa: E402

PREFIX = "sp_acolite"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--products", default="wq", help="Sets separados por coma, ver config/products.json")
    p.add_argument("--aoi", default=None)
    p.add_argument("--sensor", default="S2,L89")
    p.add_argument("--res", type=int, default=None, help="Override de s2_target_res (default: pipeline.json)")
    p.add_argument("--reprocess", action="store_true", help="Reprocesa aunque acolite_status=done")
    p.add_argument("--keep-l1", action="store_true", help="No borra el L1 tras procesar, aunque pipeline.json diga que si")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--dry-run", action="store_true", help="Solo agrupa y muestra los jobs, no invoca ACOLITE")
    return p.parse_args()


def _sensor_of(row: dict) -> str:
    return row["sensor"]  # "S2" o "L89", ya normalizado en el inventario


def _acq_date(row: dict) -> str:
    return (row.get("datetime") or "")[:10]


def group_jobs(rows: list[dict]) -> dict[tuple, list[dict]]:
    """Agrupa filas done-no-procesadas por (aoi, sensor, fecha de adquisicion)."""
    groups = defaultdict(list)
    for r in rows:
        key = (r["aoi"], _sensor_of(r), _acq_date(r))
        groups[key].append(r)
    return groups


def build_settings(cfg: dict, aoi_name: str, sensor: str, tile_paths: list[str],
                    out_dir: Path, product_sets: list[str], res_override: int | None) -> dict:
    roi = spc.resolve_aoi(cfg, aoi_name)
    sensor_key = "S2" if sensor == "S2" else "L89"
    params = []
    for ps in product_sets:
        params += spc.resolve_products(cfg, ps, sensor_key)
    # dedupe conservando orden
    seen = set()
    params = [p for p in params if not (p in seen or seen.add(p))]

    settings = {
        "inputfile": tile_paths,
        "output": str(out_dir),
        "limit": roi,
        "l2w_parameters": params,
        "l2w_export_geotiff": False,  # exportamos nosotros mismos, ver export_l2w_geotiffs() --
                                       # l2_flags hace tronar el driver netCDF de GDAL en esta
                                       # instalacion (choque de version libgdal.36 vs .37,
                                       # investigado 2026-08-31); el resto de las bandas SI
                                       # exportan bien via el mismo driver.
        "l1r_delete_netcdf": True,
        "l2r_delete_netcdf": True,
        "atmospheric_correction_method": cfg["pipeline"]["acolite_atmospheric_correction_method"],
    }
    if len(tile_paths) > 1:
        settings["merge_tiles"] = True

    if sensor == "S2":
        settings["s2_target_res"] = res_override or cfg["pipeline"]["s2_target_res"]
        if cfg["pipeline"].get("acolite_s2_ancillary_offline", True):
            # atmosfera real desde el propio .SAFE (AUX_ECMWFT), sin llamada de red --
            # ver acolite/sentinel2/auxiliary.py, verificado en el plan.
            settings["ancillary_data"] = False
            settings["s2_auxiliary_include"] = True
            settings["s2_auxiliary_default"] = True

    return settings


def run_acolite_subprocess(cfg: dict, settings: dict, out_dir: Path = None, prefix=PREFIX) -> bool:
    acolite_dir = cfg["pipeline"]["acolite_dir"]
    py = cfg["pipeline"]["acolite_python"]

    fd, settings_path = tempfile.mkstemp(suffix=".json", prefix="sp_acolite_settings_")
    try:
        with open(fd, "w") as f:
            json.dump(settings, f, indent=2)

        # launch_acolite.py --settings espera un archivo de texto clave=valor, no JSON;
        # convertimos aqui porque acolite.acolite.settings.parse SI acepta un dict via
        # la API de Python -- usamos esa via para evitar el problema de formato.
        cmd = [
            py, "-c",
            "import sys; sys.path.insert(0, %r); import acolite as ac; "
            "import json; s = json.load(open(%r)); ac.acolite.acolite_run(s)"
            % (acolite_dir, settings_path),
        ]
        spc.log(prefix, f"    invocando ACOLITE: inputfile={settings['inputfile']}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if out_dir is not None:
            (out_dir / "acolite_stderr.log").write_text(result.stdout + "\n" + result.stderr)
        if result.returncode != 0:
            spc.log(prefix, f"    ACOLITE fallo (rc={result.returncode}): "
                             f"{result.stderr.strip()[-4000:]}")
            if out_dir is not None:
                spc.log(prefix, f"    traceback completo en {out_dir / 'acolite_stderr.log'}")
            return False
        return True
    finally:
        Path(settings_path).unlink(missing_ok=True)


## bandas que se excluyen de la exportacion GeoTIFF -- l2_flags hace tronar el
## driver netCDF de GDAL en esta instalacion (choque libgdal.36 vs .37,
## investigado 2026-08-31); el resto de las bandas SI exportan bien via el
## mismo driver, asi que se pide todo excepto esta.
GEOTIFF_SKIP_DATASETS = {"l2_flags"}


def export_l2w_geotiffs(cfg: dict, nc_path: Path, prefix=PREFIX) -> bool:
    """Exporta el L2W a GeoTIFF nosotros mismos (en vez de dejarselo a
    l2w_export_geotiff=True dentro de acolite_run), para poder excluir
    GEOTIFF_SKIP_DATASETS. Mismo patron de subproceso que run_acolite_subprocess."""
    acolite_dir = cfg["pipeline"]["acolite_dir"]
    py = cfg["pipeline"]["acolite_python"]
    skip_repr = repr(GEOTIFF_SKIP_DATASETS)
    code = (
        "import sys; sys.path.insert(0, %r); import acolite as ac\n"
        "gem = ac.gem.gem(%r)\n"
        "wanted = [ds for ds in gem.datasets if ds not in %s]\n"
        "gem.close()\n"
        "ac.output.nc_to_geotiff(%r, datasets=wanted)\n"
    ) % (acolite_dir, str(nc_path), skip_repr, str(nc_path))
    result = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        spc.log(prefix, f"    export GeoTIFF fallo (rc={result.returncode}): "
                         f"{result.stderr.strip()[-2000:]}")
        return False
    return True


def find_output_geotiffs(out_dir: Path) -> list[Path]:
    """Solo los .tif reales -- el .nc NO cuenta como 'salida' (era un bug: se
    contaba junto a los .tif, inflando el tamano reportado sin que existiera
    ningun GeoTIFF real). Descarta tambien los sidecars '._*' de macOS."""
    return sorted(f for f in out_dir.glob("*_L2W_*.tif") if not f.name.startswith("._"))


def process_group(cfg: dict, aoi_name: str, sensor: str, rows: list[dict],
                   product_sets: list[str], res_override: int | None,
                   keep_l1: bool, dry_run: bool) -> list[dict]:
    root = spc.require_storage(cfg, prefix=PREFIX)
    tile_paths = [r["l1_path"] for r in rows if r["l1_path"]]
    if not tile_paths:
        spc.log(PREFIX, f"  [{aoi_name}/{sensor}] sin l1_path en las filas -- omitido.")
        return rows

    date_tag = _acq_date(rows[0]) or "unknown_date"
    out_dir = root / "L2W" / sensor / aoi_name / date_tag

    if dry_run:
        spc.log(PREFIX, f"  DRY-RUN [{aoi_name}/{sensor}/{date_tag}]: "
                         f"{len(tile_paths)} tile(s) -> {out_dir}, productos={product_sets}")
        return rows

    out_dir.mkdir(parents=True, exist_ok=True)
    settings = build_settings(cfg, aoi_name, sensor, tile_paths, out_dir, product_sets, res_override)
    ok = run_acolite_subprocess(cfg, settings, out_dir=out_dir)

    if ok:
        # ojo: descartar los sidecars '._*' que macOS crea en discos externos --
        # sortean antes que el archivo real y no son NetCDF valido.
        nc_files = sorted(f for f in out_dir.glob("*_L2W.nc") if not f.name.startswith("._"))
        if not nc_files:
            spc.log(PREFIX, f"  [{aoi_name}/{sensor}/{date_tag}] ACOLITE dijo exito pero no "
                             f"encuentro ningun *_L2W.nc en {out_dir} -- tratando como fallo.")
            ok = False
        else:
            for nc in nc_files:
                n_bands = export_l2w_geotiffs_direct(nc, out_dir, prefix=PREFIX)
                if n_bands == 0:
                    ok = False

    outputs = find_output_geotiffs(out_dir) if ok else []
    total_mb = sum(f.stat().st_size for f in outputs) / (1024 * 1024) if outputs else 0.0

    for r in rows:
        r["acolite_status"] = "done" if ok else "failed"
        r["product_set"] = ",".join(product_sets)
        r["l2w_path"] = str(out_dir) if ok else r.get("l2w_path", "")
        r["l2w_size_mb"] = f"{total_mb:.1f}" if ok else r.get("l2w_size_mb", "")
        if not ok:
            r["error"] = "ACOLITE fallo -- ver logs"

    should_delete = ok and not keep_l1 and not cfg["pipeline"]["keep_l1_after_processing"]
    if should_delete:
        for r in rows:
            l1 = Path(r["l1_path"]) if r["l1_path"] else None
            if l1 and l1.exists():
                import shutil
                if l1.is_dir():
                    shutil.rmtree(l1, ignore_errors=True)
                else:
                    l1.unlink(missing_ok=True)
                r["l1_deleted"] = "True"
        spc.log(PREFIX, f"  [{aoi_name}/{sensor}/{date_tag}] L1 borrado tras procesar "
                         f"(keep_l1_after_processing=false).")

    spc.log(PREFIX, f"  [{aoi_name}/{sensor}/{date_tag}] {'OK' if ok else 'FALLO'} "
                     f"-> {out_dir} ({total_mb:.1f} MB)")
    return rows


def process_job(cfg: dict, row: dict, product_sets: list[str] | None = None) -> dict:
    """Entrypoint de una sola fila, para el modo --stream de sp_download.py."""
    product_sets = product_sets or ["wq"]
    updated_rows = process_group(cfg, row["aoi"], row["sensor"], [row],
                                 product_sets, None, keep_l1=False, dry_run=False)
    return updated_rows[0]


def main():
    args = parse_args()
    cfg = spc.load_config()
    product_sets = [s.strip() for s in args.products.split(",") if s.strip()]

    existing = spc.read_inventory(cfg)
    if not existing:
        spc.log(PREFIX, "Inventario vacio -- nada descargado todavia, nada que procesar.")
        return

    all_aois = [k for k in cfg["aois"] if not k.startswith("_")]
    aois = [a.strip() for a in args.aoi.split(",")] if args.aoi else all_aois
    sensors = [s.strip() for s in args.sensor.split(",") if s.strip()]

    candidates = [
        r for r in existing
        if r["download_status"] == "done"
        and r["aoi"] in aois
        and r["sensor"] in sensors
        and (args.reprocess or r["acolite_status"] != "done")
    ]

    if not candidates:
        spc.log(PREFIX, "Nada pendiente de procesar para estos filtros "
                         "(usa --reprocess para forzar).")
        return

    groups = group_jobs(candidates)
    spc.log(PREFIX, f"{len(candidates)} escenas agrupadas en {len(groups)} job(s) "
                     f"(AOI x sensor x fecha). Productos: {', '.join(product_sets)}"
                     + (" | DRY-RUN" if args.dry_run else ""))

    all_updated = []
    for (aoi_name, sensor, _date), rows in sorted(groups.items()):
        updated = process_group(cfg, aoi_name, sensor, rows, product_sets,
                                args.res, args.keep_l1, args.dry_run)
        all_updated.extend(updated)
        if not args.dry_run:
            merged = spc.upsert_rows(existing, all_updated)
            spc.write_inventory(cfg, merged, prefix=PREFIX)

    if args.dry_run:
        spc.log(PREFIX, f"DRY-RUN: {len(groups)} job(s) listos, nada ejecutado.")


if __name__ == "__main__":
    main()


def read_l2w_bands_direct(nc_path: Path, skip: set = GEOTIFF_SKIP_DATASETS):
    """Lee bandas de un L2W.nc directo via netCDF4 (sin pasar por el driver
    NetCDF de GDAL, que esta roto en esta instalacion -- choque libgdal.36 vs
    .37, ver GEOTIFF_SKIP_DATASETS). Reconstruye la georreferenciacion (CRS +
    transform) desde los atributos globales xrange/yrange/pixel_size y el
    crs_wkt de la variable projection_key, exactamente los mismos datos que
    ACOLITE ya escribe para su propio nc_to_geotiff -- solo que los leemos con
    netCDF4 (funciona) en vez de gdal.Open('NETCDF:...') (roto).

    Devuelve: dict {dataset_name: numpy_array}, dict con 'transform'/'crs'/'shape'.
    """
    import netCDF4
    import numpy as np
    from affine import Affine

    nc = netCDF4.Dataset(str(nc_path))
    try:
        pkey = getattr(nc, "projection_key", None)
        xrange = getattr(nc, "xrange", None)
        pixel_size = getattr(nc, "pixel_size", None)
        crs_wkt = None
        if pkey and pkey in nc.variables:
            crs_wkt = nc.variables[pkey].getncattr("crs_wkt")

        y = nc.variables["y"][:].data if "y" in nc.variables else None
        x = nc.variables["x"][:].data if "x" in nc.variables else None

        if xrange is not None and pixel_size is not None:
            transform = Affine(pixel_size[0], 0, xrange[0], 0, pixel_size[1], getattr(nc, "yrange")[0])
        elif x is not None and y is not None:
            px = x[1] - x[0]; py = y[1] - y[0]
            transform = Affine(px, 0, x[0] - px / 2, 0, py, y[0] - py / 2)
        else:
            transform = None

        bands = {}
        for name, var in nc.variables.items():
            if name in skip or name in ("x", "y", "lon", "lat") or (pkey and name == pkey):
                continue
            if var.ndim != 2:
                continue
            arr = var[:].astype("float64")
            if hasattr(arr, "filled"):
                arr = arr.filled(np.nan)
            fill = getattr(var, "_FillValue", None)
            if fill is not None:
                arr = np.where(arr == fill, np.nan, arr)
            bands[name] = arr

        meta = {"transform": transform, "crs_wkt": crs_wkt,
                "shape": (len(y), len(x)) if y is not None and x is not None else None}
        return bands, meta
    finally:
        nc.close()


def export_l2w_geotiffs_direct(nc_path: Path, out_dir: Path, prefix=PREFIX) -> int:
    """Alternativa a export_l2w_geotiffs() que NO usa GDAL para leer el .nc
    (evita el driver roto por completo) -- lee con netCDF4, escribe con
    rasterio (que si puede escribir GTiff sin tocar el plugin de NetCDF).
    Devuelve el numero de bandas escritas."""
    import rasterio
    from rasterio.crs import CRS

    bands, meta = read_l2w_bands_direct(nc_path)
    if meta["transform"] is None or not bands:
        spc.log(prefix, f"    sin georreferenciacion o sin bandas legibles en {nc_path.name}")
        return 0

    crs = CRS.from_wkt(meta["crs_wkt"]) if meta["crs_wkt"] else None
    stem = str(nc_path).replace(".nc", "")
    n = 0
    for name, arr in bands.items():
        profile = dict(
            driver="GTiff", height=arr.shape[0], width=arr.shape[1],
            count=1, dtype="float32", crs=crs, transform=meta["transform"],
            nodata=float("nan"),
        )
        with rasterio.open(f"{stem}_{name}.tif", "w", **profile) as dst:
            dst.write(arr.astype("float32"), 1)
        n += 1
    return n
