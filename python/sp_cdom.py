#!/usr/bin/env python3
"""
sp_cdom.py -- post-procesamiento OPCIONAL para estimar aCDOM(443) a partir de los
Rrs_*/rhos_* que ACOLITE ya exporto (set de productos 'reflectance' en
config/products.json).

Por que existe este script y no un parametro de ACOLITE: ACOLITE no tiene NINGUN
algoritmo de CDOM incorporado -- verificado contra el codigo fuente completo del
clon en ~/.aquacdom/acolite (grep de 'cdom' solo encuentra nombres de variable XML
en lectores de metadata y configs de Hydrolight, nada de calculo). El candidato
mas cercano dentro de ACOLITE es la absorcion total QAA (qaa_v6_a_443, en el set
'clarity'), que no es lo mismo que un algoritmo de CDOM dedicado.

config/parameter_labels_user.txt tampoco sirve para esto: solo lo lee
acolite/acolite/acolite_map.py:371 para escalas de color al graficar, no calcula
nada (verificado).

Por que un script aparte y no un parche a acolite/acolite/acolite_l2w.py: un
parche se pierde en cada actualizacion (git pull) de ACOLITE. Este script lee los
GeoTIFF/NetCDF L2W ya generados y anade una capa nueva sin tocar el paquete.

ESTADO: framework sin algoritmo. Falta que Raymond identifique el paper de aCDOM
a usar -- requisito: parametrizado para bandas MSI (Sentinel-2) y/o OLI
(Landsat 8/9), no solo SeaWiFS/MODIS, e idealmente validado en aguas opticamente
similares al Caribe. Una vez elegido, implementar la formula en compute_acdom()
abajo -- el resto de la mecanica de E/S ya esta lista.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as spc  # noqa: E402

PREFIX = "sp_cdom"


def compute_acdom(rrs_or_rhos_bands: dict) -> "any":
    """
    PENDIENTE: implementar aqui la formula del paper elegido.

    rrs_or_rhos_bands: dict {wavelength_nm: numpy.ndarray}, ej. {443: arr443, 490: arr490, ...}
    con las bandas que el algoritmo elegido necesite (deben estar en el set
    'reflectance' de config/products.json -- si el paper usa una banda que ACOLITE
    no exporta ahi, hay que anadirla a ese JSON primero).

    Debe devolver un numpy.ndarray de aCDOM(443) en m^-1, mismo shape que los inputs.
    """
    raise NotImplementedError(
        "sp_cdom.py: falta el algoritmo de aCDOM. Ver docstring del modulo -- "
        "se necesita el paper (parametrizado para MSI/OLI) antes de implementar esto."
    )


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--aoi", default=None)
    p.add_argument("--sensor", default="S2,L89")
    p.add_argument("--dry-run", action="store_true",
                   help="Lista que L2W con banda 'reflectance' existen y podrian procesarse.")
    return p.parse_args()


def main():
    args = parse_args()
    cfg = spc.load_config()
    existing = spc.read_inventory(cfg)

    candidates = [
        r for r in existing
        if r["acolite_status"] == "done"
        and "reflectance" in (r.get("product_set") or "").split(",")
    ]

    if not candidates:
        spc.log(PREFIX, "No hay salidas L2W con el set 'reflectance' todavia -- "
                         "nada sobre lo que calcular aCDOM. Corre sp_acolite.py "
                         "--products reflectance primero.")
        return

    spc.log(PREFIX, f"{len(candidates)} escena(s) con reflectancias disponibles.")
    if args.dry_run:
        for r in candidates:
            spc.log(PREFIX, f"  {r['scene_id']} -> {r['l2w_path']}")
        return

    spc.log(PREFIX, "Algoritmo de aCDOM aun no definido -- ver docstring de compute_acdom(). "
                     "Nada ejecutado.")


if __name__ == "__main__":
    main()
