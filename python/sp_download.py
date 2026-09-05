#!/usr/bin/env python3
"""
sp_download.py -- Codigo 1: descarga Sentinel-2 y/o Landsat 8/9 por rango de fechas.

Uso:
    sp_download.py --start 2026-06-01 --end 2026-07-31 \
                    [--aoi pr_main_west,usvi] [--sensor S2,L89] \
                    [--cloud-cover 50] [--dry-run] [--stream]

--dry-run consulta y reporta conteos/GB estimados SIN descargar nada. Correrlo
siempre primero, igual que --dry-run en Water2Coast/python/pace_download.py y
modis_download.py.

--stream encadena descarga -> ACOLITE -> borrado del L1 por escena (llama a
sp_acolite.process_job), para no acumular L1 crudos en disco durante corridas largas.

Reanudable: si una escena ya tiene download_status=done y su l1_path existe, se
salta. Un fallo por escena queda registrado en su fila (error, attempts) y el
lote continua -- solo KeyboardInterrupt/SystemExit propagan, mismo criterio que
Water2Coast/python/sn3_eumetsat_download.py.

IMPORTANTE: al 2026-08-29 este script NO ha sido usado para descargar ninguna
imagen real. Solo se ha probado con --dry-run / consultas de conteo, por
instruccion explicita del usuario ("crea el codigo sin descargar ninguna
imagen"). Antes de una corrida real: confirmar SSD montado y credenciales.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as spc  # noqa: E402

PREFIX = "sp_download"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--aoi", default=None,
                   help="Nombres de AOI separados por coma (ver config/aois.json). "
                        "Por defecto: todos.")
    p.add_argument("--sensor", default="S2,L89",
                   help="S2, L89, o ambos separados por coma. Default: S2,L89 "
                        "(pero L89 solo corre si esta enabled en pipeline.json).")
    p.add_argument("--cloud-cover", type=float, default=None,
                   help="Override del filtro de nubes por defecto en pipeline.json.")
    p.add_argument("--dry-run", action="store_true",
                   help="Solo consulta y reporta conteos/GB estimados. No descarga nada.")
    p.add_argument("--stream", action="store_true",
                   help="Encadena ACOLITE + borrado de L1 por escena tras descargarla.")
    return p.parse_args()


def _requested_sensors(args, cfg) -> list[str]:
    requested = [s.strip() for s in args.sensor.split(",") if s.strip()]
    active = []
    for s in requested:
        sensor_cfg = cfg["pipeline"]["sensors"].get(s)
        if sensor_cfg is None:
            spc.log(PREFIX, f"Sensor desconocido '{s}', ignorado.")
            continue
        if not sensor_cfg.get("enabled", False):
            spc.log(PREFIX, f"Sensor '{s}' esta deshabilitado en pipeline.json (enabled=false) -- "
                             f"omitido. Ver Satellite_Pipeline/README.md.")
            continue
        active.append(s)
    return active


def _requested_aois(args, cfg) -> list[str]:
    all_aois = [k for k in cfg["aois"] if not k.startswith("_")]
    if args.aoi is None:
        return all_aois
    requested = [a.strip() for a in args.aoi.split(",") if a.strip()]
    for a in requested:
        if a not in all_aois:
            raise KeyError(f"AOI '{a}' no existe en config/aois.json. Validos: {', '.join(all_aois)}")
    return requested


def query_sentinel2(ac, roi, start, end, cloud_cover):
    urls, scenes = ac.api.cdse.query(
        collection="SENTINEL-2", product="S2MSI1C",
        start_date=start, end_date=end, roi=roi,
        cloud_cover=cloud_cover, max_results=1000, verbosity=0,
    )
    return urls, scenes


def query_landsat(ac, roi, start, end, cloud_cover):
    ents, ids, dss = ac.api.earthexplorer.query(
        collection=2, level=1, landsat_type="ot",
        start_date=start, end_date=end, roi=roi,
        cloud_cover=cloud_cover, verbosity=0,
    )
    return ents, ids, dss


def download_one_s2(ac, root: Path, scene_id: str, url: str, max_attempts: int, prefix=PREFIX):
    """Descarga una escena S2 via CDSE. ac.api.cdse.download ya salta lo que exista en
    disco (override=False) y hace su propio extract_zip + remove_zip. Reintenta hasta
    max_attempts veces; solo KeyboardInterrupt/SystemExit propagan."""
    dest_dir = root / "S2" / "L1C"
    dest_dir.mkdir(parents=True, exist_ok=True)
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            local = ac.api.cdse.download([url], scenes=[scene_id], output=str(dest_dir),
                                         netrc_machine="cdse", extract_zip=True,
                                         remove_zip=True, verbosity=0)
            if local:
                return local[0], None
            last_err = "download() devolvio vacio"
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            spc.log(prefix, f"    intento {attempt}/{max_attempts} fallo para {scene_id}: {last_err}")
    return None, last_err


def download_one_landsat(ac, root: Path, scene_id: str, entity_id: str, dataset: str,
                          max_attempts: int, prefix=PREFIX):
    """Descarga una escena Landsat via EarthExplorer M2M. Mismo criterio de reintento."""
    dest_dir = root / "L89" / "L1"
    dest_dir.mkdir(parents=True, exist_ok=True)
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            local = ac.api.earthexplorer.download([entity_id], [dataset], [scene_id],
                                                  output=str(dest_dir), extract_tar=True,
                                                  remove_tar=True, verbosity=0)
            if local:
                return local[0], None
            last_err = "download() devolvio vacio"
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            spc.log(prefix, f"    intento {attempt}/{max_attempts} fallo para {scene_id}: {last_err}")
    return None, last_err


def main():
    args = parse_args()
    cfg = spc.load_config()
    sensors = _requested_sensors(args, cfg)
    aois = _requested_aois(args, cfg)
    cloud_cover = args.cloud_cover if args.cloud_cover is not None else cfg["pipeline"]["default_cloud_cover"]

    if not sensors:
        spc.log(PREFIX, "Ningun sensor activo para esta corrida. Nada que hacer.")
        return

    spc.log(PREFIX, f"Rango: {args.start} -> {args.end} | AOIs: {', '.join(aois)} | "
                     f"Sensores: {', '.join(sensors)} | nubes<{cloud_cover}%"
                     + (" | DRY-RUN" if args.dry_run else "")
                     + (" | STREAM" if args.stream else ""))

    spc.require_credentials(cfg, sensors, prefix=PREFIX)
    if not args.dry_run:
        spc.require_storage(cfg, prefix=PREFIX)

    sys.path.insert(0, cfg["pipeline"]["acolite_dir"])
    import acolite as ac  # noqa: E402

    existing = spc.read_inventory(cfg)
    known_ids = {r["scene_id"] for r in existing}
    seen_this_run = set()  # dedupe entre AOIs superpuestos

    # jobs pendientes de esta corrida: (scene_id, sensor, aoi, extra) donde extra trae
    # lo necesario para descargar (url para S2; entity_id/dataset para Landsat)
    jobs = []
    total_found = 0
    for aoi_name in aois:
        roi = spc.resolve_aoi(cfg, aoi_name)
        for sensor in sensors:
            if sensor == "S2":
                urls, scenes = query_sentinel2(ac, roi, args.start, args.end, cloud_cover)
                found = list(zip(scenes, urls))
                spc.log(PREFIX, f"  [{aoi_name}/S2] {len(found)} escenas encontradas")
                total_found += len(found)
                for scene_id, url in found:
                    if scene_id in known_ids or scene_id in seen_this_run:
                        continue
                    seen_this_run.add(scene_id)
                    jobs.append(("S2", aoi_name, scene_id, {"url": url}))
            elif sensor == "L89":
                ents, ids, dss = query_landsat(ac, roi, args.start, args.end, cloud_cover)
                spc.log(PREFIX, f"  [{aoi_name}/L89] {len(ids)} escenas encontradas")
                total_found += len(ids)
                for scene_id, entity_id, dataset in zip(ids, ents, dss):
                    if scene_id in known_ids or scene_id in seen_this_run:
                        continue
                    seen_this_run.add(scene_id)
                    jobs.append(("L89", aoi_name, scene_id, {"entity_id": entity_id, "dataset": dataset}))

    if args.dry_run:
        est_gb = len(jobs) * 0.9
        spc.log(PREFIX, f"DRY-RUN: {total_found} escenas encontradas en total, {len(jobs)} nuevas "
                         f"respecto al inventario actual (no se registro ni descargo nada). "
                         f"Estimado a ~0.9 GB/escena: ~{est_gb:.0f} GB si se descargaran todas.")
        return

    if not jobs:
        spc.log(PREFIX, "Nada nuevo que descargar -- inventario ya al dia para este rango/AOI/sensor.")
        return

    root = spc.require_storage(cfg, prefix=PREFIX)
    max_attempts = cfg["pipeline"]["max_download_attempts"]

    new_rows = []
    n_ok = n_fail = 0
    for sensor, aoi_name, scene_id, extra in jobs:
        if sensor == "S2":
            l1_path, err = download_one_s2(ac, root, scene_id, extra["url"], max_attempts)
            platform, level, source = scene_id[:3], "L1C", "CDSE"
        else:
            l1_path, err = download_one_landsat(ac, root, scene_id, extra["entity_id"],
                                                extra["dataset"], max_attempts)
            platform, level, source = scene_id[:4], "L1", "EarthExplorer"

        acq_dt = spc.parse_acquisition_datetime(sensor, scene_id)
        if not acq_dt:
            spc.log(PREFIX, f"  ADVERTENCIA: no se pudo parsear la fecha de adquisicion de "
                             f"{scene_id} -- quedara vacia en el inventario (afecta sp_update.py "
                             f"y el agrupamiento de sp_acolite.py para esta escena).")

        if l1_path:
            n_ok += 1
            row = spc.new_row(scene_id=scene_id, sensor=sensor, platform=platform, level=level,
                              aoi=aoi_name, source=source, download_status="done",
                              l1_path=l1_path, l1_deleted="False", attempts=str(max_attempts),
                              datetime=acq_dt)
            spc.log(PREFIX, f"  OK  {scene_id} -> {l1_path}")
        else:
            n_fail += 1
            row = spc.new_row(scene_id=scene_id, sensor=sensor, platform=platform, level=level,
                              aoi=aoi_name, source=source, download_status="failed",
                              attempts=str(max_attempts), error=err or "desconocido",
                              datetime=acq_dt)
            spc.log(PREFIX, f"  FAIL {scene_id}: {err}")
        new_rows.append(row)

        if args.stream and l1_path:
            try:
                import sp_acolite
                sp_acolite.process_job(cfg, row)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                spc.log(PREFIX, f"  --stream: ACOLITE fallo para {scene_id}: {type(e).__name__}: {e}")

        # checkpoint incremental -- si el proceso muere a mitad de un lote largo,
        # no se pierde lo ya descargado
        merged = spc.upsert_rows(existing, new_rows)
        spc.write_inventory(cfg, merged, prefix=PREFIX)

    spc.log(PREFIX, f"Terminado: {n_ok} descargadas, {n_fail} fallidas de {len(jobs)} nuevas.")


if __name__ == "__main__":
    main()
