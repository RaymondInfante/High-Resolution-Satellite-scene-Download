"""
sp_common.py -- utilidades compartidas de la tuberia Satellite_Pipeline
(descarga Sentinel-2/Landsat 8-9 -> procesamiento ACOLITE).

Convenciones tomadas de scripts existentes del repo:
  - logging con prefijo [modulo], igual que Water2Coast/python/sn3_eumetsat_download.py
  - escritura atomica (.tmp + rename) del inventario, mismo patron que ese script usa
    para archivos individuales
  - fallo ruidoso si el volumen de almacenamiento no existe, en vez de mkdir -p
    silencioso de una ruta falsa en el disco interno (ver Water2Coast/python/pace_download.py
    y modis_download.py para el patron de --dry-run / --dest overridable)

No se ha ejecutado ninguna descarga con este modulo todavia -- fue escrito y verificado
solo con --dry-run / pruebas de configuracion, por instruccion explicita del 2026-08-29.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # .../EMC
PIPELINE_DIR = Path(__file__).resolve().parents[1]  # .../EMC/Satellite_Pipeline
CONFIG_DIR = PIPELINE_DIR / "config"

INVENTORY_FIELDS = [
    "scene_id", "sensor", "platform", "level", "aoi", "tile_or_pathrow",
    "datetime", "cloud_cover", "source",
    "download_status", "l1_path", "l1_deleted",
    "acolite_status", "product_set", "l2w_path", "l2w_size_mb",
    "attempts", "error", "updated_at",
]


def log(prefix: str, msg: str) -> None:
    print(f"[{prefix}] {msg}", flush=True)


# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def load_config() -> dict:
    """Carga los tres JSON de config/. Falla con un mensaje claro si falta alguno."""
    cfg = {}
    for name in ("aois", "products", "pipeline"):
        path = CONFIG_DIR / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Falta {path}. Los tres archivos de config/ deben existir antes de correr nada."
            )
        cfg[name] = _load_json(path)
    return cfg


def resolve_aoi(cfg: dict, name: str) -> list[float]:
    aois = cfg["aois"]
    if name not in aois:
        valid = ", ".join(k for k in aois if not k.startswith("_"))
        raise KeyError(f"AOI '{name}' no existe en config/aois.json. Validos: {valid}")
    return aois[name]["limit"]


def resolve_products(cfg: dict, product_set: str, sensor: str) -> list[str]:
    """sensor: 'S2' o 'L89'. Devuelve common + el override de ese sensor, sin duplicados."""
    products = cfg["products"]
    if product_set not in products:
        valid = ", ".join(k for k in products if not k.startswith("_"))
        raise KeyError(f"Set de productos '{product_set}' no existe. Validos: {valid}")
    entry = products[product_set]
    params = list(entry.get("common", [])) + list(entry.get(sensor, []))
    # dedupe conservando orden
    seen = set()
    out = []
    for p in params:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


# --------------------------------------------------------------------------
# Guardas de almacenamiento y credenciales
# --------------------------------------------------------------------------

def require_storage(cfg: dict, prefix: str = "sp_common") -> Path:
    """
    Verifica que el volumen destino este realmente montado antes de escribir nada.
    NUNCA hace mkdir -p de una ruta /Volumes/... falsa -- eso escribiria silenciosamente
    en el disco interno, que ya esta al ~73% de uso (verificado 2026-08-28/29).
    """
    root = Path(cfg["pipeline"]["storage_root"])
    # la parte montable es el primer componente bajo /Volumes/<Nombre>
    parts = root.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        mount_point = Path(parts[0]) / parts[1] / parts[2]
        if not mount_point.exists():
            raise RuntimeError(
                f"{mount_point} no esta montado. Conecta el disco externo antes de "
                f"correr sp_download.py / sp_acolite.py -- no se creara esa ruta en el "
                f"disco interno."
            )
    root.mkdir(parents=True, exist_ok=True)
    log(prefix, f"Almacenamiento OK: {root}")
    return root


def require_credentials(cfg: dict, sensors: list[str], prefix: str = "sp_common") -> None:
    """
    Preflight de credenciales via el propio ac.shared.auth de ACOLITE, para fallar
    antes de gastar tiempo en consultas de red. Imprime exactamente que falta.
    """
    sys.path.insert(0, cfg["pipeline"]["acolite_dir"])
    import acolite as ac  # noqa: E402

    missing = []
    if "S2" in sensors:
        if ac.shared.auth("cdse") is None:
            missing.append(
                "machine cdse login <usuario_CDSE> password <password_CDSE>"
            )
    if "L89" in sensors:
        if ac.shared.auth("earthexplorer_token") is None:
            missing.append(
                "machine earthexplorer_token login <usuario_ERS> password <token_64_caracteres>"
            )
        if ac.shared.auth("earthexplorer") is None:
            missing.append(
                "machine earthexplorer login <usuario_ERS> password <password_ERS>"
            )

    if missing:
        log(prefix, "Faltan credenciales en ~/.netrc. Anade estas lineas:")
        for line in missing:
            log(prefix, "  " + line)
        raise RuntimeError("Credenciales incompletas -- ver mensaje arriba.")

    log(prefix, f"Credenciales OK para: {', '.join(sensors)}")


# --------------------------------------------------------------------------
# Inventario (scenes.csv) -- lectura/escritura atomica
# --------------------------------------------------------------------------

def inventory_path(cfg: dict) -> Path:
    return REPO_ROOT / cfg["pipeline"]["inventory_csv"]


def read_inventory(cfg: dict) -> list[dict]:
    path = inventory_path(cfg)
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_inventory(cfg: dict, rows: list[dict], prefix: str = "sp_common") -> None:
    """Escritura atomica: .tmp + rename, para que una interrupcion nunca deje
    un inventario truncado que parezca completo (mismo patron que
    Water2Coast/python/sn3_eumetsat_download.py usa para archivos individuales)."""
    path = inventory_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=INVENTORY_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in INVENTORY_FIELDS})
        os.replace(tmp_name, path)
    except (KeyboardInterrupt, SystemExit):
        Path(tmp_name).unlink(missing_ok=True)
        raise
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    log(prefix, f"Inventario escrito: {path} ({len(rows)} filas)")


def upsert_rows(existing: list[dict], new_rows: list[dict]) -> list[dict]:
    """Combina por scene_id; new_rows sobreescribe a existing en caso de choque."""
    by_id = {r["scene_id"]: r for r in existing}
    for r in new_rows:
        by_id[r["scene_id"]] = r
    return list(by_id.values())


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_acquisition_datetime(sensor: str, scene_id: str) -> str:
    """Extrae la fecha/hora de adquisicion del nombre de la escena -- este valor
    puebla el campo 'datetime' del inventario, del que dependen sp_update.py
    (para saber desde donde reanudar) y sp_acolite.py (para agrupar por fecha
    antes de mandar a merge_tiles). Nunca debe tumbar el lote si el nombre no
    calza con el patron esperado -- devuelve cadena vacia y quien llama decide.

    S2:  S2A_MSIL1C_20260828T145751_N0512_R039_T19QHV_20260829T010322[.SAFE]
         -> tercer campo es el sensing datetime
    L89: LC09_L1TP_004047_20260725_20260725_02_T1
         -> cuarto campo es la fecha de adquisicion (el quinto es de procesamiento)
    """
    import re

    base = scene_id.replace(".SAFE", "")
    parts = base.split("_")
    try:
        if sensor == "S2" and len(parts) >= 3:
            raw = parts[2]
            if len(raw) >= 15 and raw[8] == "T":
                return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}T{raw[9:11]}:{raw[11:13]}:{raw[13:15]}Z"
        if sensor == "L89" and len(parts) >= 4:
            raw = parts[3]
            if len(raw) == 8:
                return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    except Exception:
        pass
    m = re.search(r"(20\d{6})", base)
    if m:
        raw = m.group(1)
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""


def new_row(**kwargs) -> dict:
    row = {k: "" for k in INVENTORY_FIELDS}
    row.update(kwargs)
    row["updated_at"] = now_iso()
    return row
