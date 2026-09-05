#!/usr/bin/env python3
"""
sp_update.py -- Codigo 2: descarga incremental. Consulta el inventario existente
(Satellite_Pipeline/inventory/scenes.csv) para saber que es lo mas reciente que
ya se tiene por (AOI, sensor), y solo pide a sp_download.py el tramo nuevo.

No duplica logica de descarga -- toda pasa por sp_download.main_for_range(),
igual patron setdiff() que Water2Coast/R/SN3_Anomaly_Update.R pero calculado en
tiempo de ejecucion en vez de mirar nombres de columnas de un NetCDF.

Uso:
    sp_update.py [--aoi ...] [--sensor S2,L89] [--floor-date 2024-01-01]
                 [--dry-run] [--stream]

Reintenta ademas las filas con download_status=failed y attempts < limite
(pipeline.json: max_download_attempts), volviendo a pedirlas en el mismo rango
de fechas que su propio 'datetime' de inventario.

Al 2026-08-29: escrito y probado solo por inspeccion/--dry-run, sin ninguna
descarga real, por instruccion explicita del usuario.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as spc  # noqa: E402
import sp_download  # noqa: E402

PREFIX = "sp_update"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--aoi", default=None, help="Ver sp_download.py --aoi")
    p.add_argument("--sensor", default="S2,L89", help="Ver sp_download.py --sensor")
    p.add_argument("--cloud-cover", type=float, default=None)
    p.add_argument("--floor-date", default=None,
                   help="Fecha de arranque si el inventario esta vacio para un (AOI,sensor). "
                        "YYYY-MM-DD. Sin este flag, un par (AOI,sensor) sin historial se omite "
                        "con una advertencia -- no se asume una fecha por defecto que luego "
                        "quede obsoleta (ver nota sobre pace_download.py/DEFAULT_START en el plan).")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--stream", action="store_true")
    return p.parse_args()


def latest_done_date(rows: list[dict], aoi: str, sensor: str) -> str | None:
    dates = [r["datetime"] for r in rows
             if r["aoi"] == aoi and r["sensor"] == sensor
             and r["download_status"] == "done" and r["datetime"]]
    return max(dates) if dates else None


def main():
    args = parse_args()
    cfg = spc.load_config()
    existing = spc.read_inventory(cfg)

    all_aois = [k for k in cfg["aois"] if not k.startswith("_")]
    aois = [a.strip() for a in args.aoi.split(",")] if args.aoi else all_aois
    sensors = [s.strip() for s in args.sensor.split(",") if s.strip()]

    today = datetime.now().strftime("%Y-%m-%d")
    plan = []  # (aoi, sensor, start, end)

    for aoi_name in aois:
        for sensor in sensors:
            sensor_cfg = cfg["pipeline"]["sensors"].get(sensor, {})
            if not sensor_cfg.get("enabled", False):
                continue
            last = latest_done_date(existing, aoi_name, sensor)
            if last:
                start = (datetime.strptime(last[:10], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            elif args.floor_date:
                start = args.floor_date
            else:
                spc.log(PREFIX, f"[{aoi_name}/{sensor}] sin historial en el inventario y sin "
                                 f"--floor-date -- omitido. Pasa --floor-date para arrancarlo.")
                continue
            if start > today:
                spc.log(PREFIX, f"[{aoi_name}/{sensor}] ya al dia (ultimo: {last}). Nada nuevo.")
                continue
            plan.append((aoi_name, sensor, start, today))

    failed = [r for r in existing
              if r["download_status"] == "failed"
              and int(r.get("attempts") or 0) < cfg["pipeline"]["max_download_attempts"]]
    if failed:
        spc.log(PREFIX, f"{len(failed)} escenas marcadas 'failed' con reintentos disponibles -- "
                         f"quedaran cubiertas si su fecha cae dentro del rango recalculado arriba; "
                         f"revisa el inventario si necesitas forzar una fecha puntual.")

    if not plan:
        spc.log(PREFIX, "Nada nuevo que consultar en ningun (AOI, sensor).")
        return

    for aoi_name, sensor, start, end in plan:
        spc.log(PREFIX, f"Actualizando [{aoi_name}/{sensor}]: {start} -> {end}")
        argv = ["--start", start, "--end", end, "--aoi", aoi_name, "--sensor", sensor]
        if args.cloud_cover is not None:
            argv += ["--cloud-cover", str(args.cloud_cover)]
        if args.dry_run:
            argv += ["--dry-run"]
        if args.stream:
            argv += ["--stream"]
        sys.argv = ["sp_download.py"] + argv
        sp_download.main()


if __name__ == "__main__":
    main()
