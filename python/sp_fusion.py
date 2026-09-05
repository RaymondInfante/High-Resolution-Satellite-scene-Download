#!/usr/bin/env python3
"""
sp_fusion.py -- combina, por (AOI, fecha), los L2W ya producidos de S2 y
Landsat en un solo set de GeoTIFF a 10 m ("Super Imagen"), con una capa
adicional source_sensor.tif que indica que sensor gano cada pixel.

Regla de combinacion: mejor-pixel por cobertura (proxy de nubes). Como
l2_flags no se exporta a GeoTIFF en esta instalacion (ver sp_acolite.py,
GEOTIFF_SKIP_DATASETS -- choque de version de GDAL), se usa la fraccion de
pixeles validos (no-NaN) de la escena completa como proxy de "menos nube":
la escena mas completa de la ventana gana en caso de que ambos sensores
tengan dato valido en un pixel dado.

Landsat se remuestrea de 30 a 10 m por interpolacion bilineal para alinear
con la grilla nativa de S2 -- esto NO anade detalle real, solo permite la
combinacion pixel a pixel (documentado explicitamente para que nadie lo
confunda con resolucion nativa de 10 m).

Si solo hay un sensor disponible para una fecha (caso de hoy: agosto 2026
solo tiene S2 descargado, Landsat pendiente), la fusion es un caso
degenerado -- pasa esos valores tal cual, con source_sensor=1 en todo el
AOI. El codigo es el mismo; simplemente no hay competencia de pixeles
todavia.

Salida en storage_root/Fused/<aoi>/<date>/<band>.tif + source_sensor.tif,
misma convencion de carpeta-por-fecha que sp_acolite.py usa en L2W/, para
que sp_composite.py pueda apuntar a cualquiera de los dos sin cambios.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as spc  # noqa: E402

PREFIX = "sp_fusion"

BAND_RE = re.compile(r"_L2W_(.+)\.tif$")


def band_from_filename(path: Path) -> str | None:
    m = BAND_RE.search(path.name)
    return m.group(1) if m else None


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--aoi", default=None, help="Nombres de AOI separados por coma. Default: todos los que haya en disco.")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def find_date_dirs(root: Path, sensor: str, aoi: str) -> dict[str, Path]:
    base = root / "L2W" / sensor / aoi
    if not base.exists():
        return {}
    return {d.name: d for d in base.iterdir() if d.is_dir()}


def fuse_band(s2_tif: Path | None, l89_tif: Path | None, ref_profile: dict = None, prefix=PREFIX):
    """Devuelve (array_fusionado, source_array, profile) para una banda dada.
    source: 0=sin dato, 1=S2, 2=Landsat. Si solo hay un sensor, caso degenerado.

    ref_profile: grilla de referencia COMUN para toda la fecha (no solo esta
    banda) -- necesaria porque distintos parametros de ACOLITE tienen nombres
    distintos entre sensores (TSS_Jiang2021 en Landsat vs TSS_Jiang2023 en S2),
    asi que una banda puede existir SOLO en Landsat aunque S2 si tenga datos
    para esa fecha. Sin esto, esa banda quedaba en la grilla nativa de Landsat
    (30 m) mientras el resto de las bandas de la misma fecha quedaban en la de
    S2 (10 m) -- tamanos de array distintos, tronaba al combinar con
    source_sensor (bug encontrado 2026-09-01)."""
    import numpy as np
    import rasterio
    from rasterio.warp import reproject, Resampling

    if s2_tif is None and l89_tif is None:
        return None, None, None

    # S2 define la grilla de referencia (10 m nativo) si no se paso una explicita
    if s2_tif is not None:
        with rasterio.open(s2_tif) as src:
            if ref_profile is None:
                ref_profile = src.profile
            s2_native = src.read(1).astype("float64")
            nodata = src.nodata
            if nodata is not None:
                s2_native = np.where(s2_native == nodata, np.nan, s2_native)
        if (s2_native.shape == (ref_profile["height"], ref_profile["width"])
                and src.transform == ref_profile["transform"]):
            s2_arr = s2_native
        else:
            s2_arr = np.full((ref_profile["height"], ref_profile["width"]), np.nan)
            reproject(
                source=s2_native, destination=s2_arr,
                src_transform=src.transform, src_crs=src.crs,
                dst_transform=ref_profile["transform"], dst_crs=ref_profile["crs"],
                resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan,
            )
    else:
        if ref_profile is None:
            with rasterio.open(l89_tif) as src:
                ref_profile = src.profile
        s2_arr = None

    l89_arr = None
    if l89_tif is not None:
        with rasterio.open(l89_tif) as src:
            l89_native = src.read(1).astype("float64")
            l89_nodata = src.nodata
            if l89_nodata is not None:
                l89_native = np.where(l89_native == l89_nodata, np.nan, l89_native)
            if src.shape == (ref_profile["height"], ref_profile["width"]) and src.transform == ref_profile["transform"]:
                l89_arr = l89_native
            else:
                # remuestrea Landsat (30 m) a la grilla de referencia (10 m) --
                # bilineal, NO anade detalle real, solo alinea el pixel (ver docstring).
                l89_arr = np.full((ref_profile["height"], ref_profile["width"]), np.nan)
                reproject(
                    source=l89_native, destination=l89_arr,
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=ref_profile["transform"], dst_crs=ref_profile["crs"],
                    resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan,
                )

    shape = (ref_profile["height"], ref_profile["width"])
    fused = np.full(shape, np.nan)
    source = np.zeros(shape, dtype="uint8")

    if s2_arr is not None and l89_arr is not None:
        s2_frac = float(np.mean(~np.isnan(s2_arr)))
        l89_frac = float(np.mean(~np.isnan(l89_arr)))
        # mejor-pixel por cobertura: la escena mas completa gana donde ambas tengan dato
        if s2_frac >= l89_frac:
            first, first_id, second, second_id = s2_arr, 1, l89_arr, 2
        else:
            first, first_id, second, second_id = l89_arr, 2, s2_arr, 1
        mask1 = ~np.isnan(first)
        fused[mask1] = first[mask1]; source[mask1] = first_id
        mask2 = np.isnan(fused) & ~np.isnan(second)
        fused[mask2] = second[mask2]; source[mask2] = second_id
    elif s2_arr is not None:
        mask = ~np.isnan(s2_arr)
        fused[mask] = s2_arr[mask]; source[mask] = 1
    elif l89_arr is not None:
        mask = ~np.isnan(l89_arr)
        fused[mask] = l89_arr[mask]; source[mask] = 2

    return fused, source, ref_profile


def process_date(cfg: dict, aoi: str, date: str, s2_dir: Path | None, l89_dir: Path | None,
                  out_root: Path, prefix=PREFIX) -> dict:
    import numpy as np
    import rasterio

    def _real_tifs(d):
        return [] if d is None else [f for f in d.glob("*.tif") if not f.name.startswith("._")]
    s2_bands = {band_from_filename(f): f for f in _real_tifs(s2_dir) if band_from_filename(f)}
    l89_bands = {band_from_filename(f): f for f in _real_tifs(l89_dir) if band_from_filename(f)}
    all_bands = sorted(set(s2_bands) | set(l89_bands))

    if not all_bands:
        spc.log(prefix, f"  [{aoi}/{date}] sin bandas GeoTIFF en ninguno de los dos sensores -- omitido.")
        return {}

    out_dir = out_root / "Fused" / aoi / date
    out_dir.mkdir(parents=True, exist_ok=True)

    # Grilla de referencia UNICA para toda la fecha (no por-banda) -- prefiere
    # cualquier banda de S2 si existe (10 m nativo), si no la primera de Landsat.
    # Necesaria porque algunos parametros solo existen en un sensor para esta
    # fecha (ej. TSS_Jiang2021 solo en Landsat) y sin una referencia compartida
    # esa banda quedaba en su propia grilla nativa, de tamano distinto al resto
    # (bug encontrado 2026-09-01: ValueError de shapes al combinar source_sensor).
    with rasterio.open(next(iter(s2_bands.values())) if s2_bands else next(iter(l89_bands.values()))) as ref_src:
        date_ref_profile = ref_src.profile

    # source_sensor.tif se acumula como la UNION de cobertura a traves de TODAS
    # las bandas -- no basta con tomar la primera banda alfabetica: distintos
    # parametros de ACOLITE tienen distinto enmascaramiento (ej. l2w_mask_negative_rhow
    # deja SPM_Nechad2016_654 en ~0% mientras fai/ndvi llegan a 68%+), asi que una
    # sola banda arbitraria puede subrepresentar por completo la cobertura real
    # (bug encontrado 2026-09-01: source_sensor.tif salia 100% "sin dato" pese a
    # que chl_oc3 si tenia pixeles validos).
    combined_source = None
    coverage_by_band = {}
    for band in all_bands:
        fused, source, profile = fuse_band(s2_bands.get(band), l89_bands.get(band), date_ref_profile, prefix)
        if fused is None:
            continue
        out_profile = profile.copy()
        out_profile.update(dtype="float32", nodata=float("nan"), count=1)
        with rasterio.open(out_dir / f"{band}.tif", "w", **out_profile) as dst:
            dst.write(fused.astype("float32"), 1)
        coverage_by_band[band] = float(np.mean(~np.isnan(fused)))

        if combined_source is None:
            combined_source = source.copy()
            ref_profile = profile
        else:
            fill_mask = (combined_source == 0) & (source != 0)
            combined_source[fill_mask] = source[fill_mask]

    if combined_source is not None:
        src_profile = ref_profile.copy()
        src_profile.update(dtype="uint8", nodata=0, count=1)
        with rasterio.open(out_dir / "source_sensor.tif", "w", **src_profile) as dst:
            dst.write(combined_source, 1)

    meta = {
        "aoi": aoi, "date": date,
        "sensors_present": [s for s, d in (("S2", s2_dir), ("L89", l89_dir)) if d is not None],
        "bands": coverage_by_band,
    }
    (out_dir / "fusion_meta.json").write_text(json.dumps(meta, indent=2))
    spc.log(prefix, f"  [{aoi}/{date}] fusionado -> {out_dir} "
                     f"(sensores: {','.join(meta['sensors_present'])}, {len(coverage_by_band)} bandas)")
    return meta


def main():
    args = parse_args()
    cfg = spc.load_config()
    root = spc.require_storage(cfg, prefix=PREFIX)

    all_aois = [k for k in cfg["aois"] if not k.startswith("_")]
    aois = [a.strip() for a in args.aoi.split(",")] if args.aoi else all_aois

    for aoi in aois:
        s2_dates = find_date_dirs(root, "S2", aoi)
        l89_dates = find_date_dirs(root, "L89", aoi)
        all_dates = sorted(set(s2_dates) | set(l89_dates))
        if not all_dates:
            continue
        spc.log(PREFIX, f"[{aoi}] {len(all_dates)} fecha(s) con L2W disponible "
                         f"(S2: {len(s2_dates)}, L89: {len(l89_dates)})")
        if args.dry_run:
            for d in all_dates:
                sensors = [s for s, dd in (("S2", s2_dates), ("L89", l89_dates)) if d in dd]
                spc.log(PREFIX, f"  DRY-RUN {aoi}/{d}: sensores disponibles = {sensors}")
            continue
        for d in all_dates:
            process_date(cfg, aoi, d, s2_dates.get(d), l89_dates.get(d), root)


if __name__ == "__main__":
    main()
