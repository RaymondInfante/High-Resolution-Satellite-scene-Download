<<<<<<< HEAD
# Satellite_Pipeline

Descarga y procesamiento por lotes de Sentinel-2 y Landsat 8/9, con ACOLITE, para
Puerto Rico + Culebra + Vieques + Mona + Desecheo + veril sur + USVI.

Ver el plan completo en `~/.claude/plans/hola-claude-ahora-mismo-delegated-nebula.md`
para el contexto, las cifras de costo/AWS, y el orden de ejecucion recomendado.

## Estado (2026-08-29)

Los cuatro codigos estan escritos y probados con `--dry-run` / consultas de
prueba **sin descargar ninguna imagen real**, por instruccion explicita del
usuario. Antes de una corrida que si descargue:

1. Montar `/Volumes/Ray_SSD` (verificado que NO esta montado al escribir esto).
2. Anadir las credenciales de CDSE a `~/.netrc` (ver abajo -- confirmado
   faltante via `sp_download.py --dry-run`, que fallo con el mensaje exacto).
3. Revisar `config/aois.json` -- las 6 cajas AOI son un punto de partida, no
   coordenadas finales.

## Codigos

| Script | Que hace |
|---|---|
| `python/sp_common.py` | Config, guardas de storage/credenciales, inventario (E/S atomica) |
| `python/sp_download.py` | Codigo 1: descarga por rango de fechas |
| `python/sp_update.py` | Codigo 2: descarga incremental desde el inventario |
| `python/sp_acolite.py` | Codigo 3: procesamiento por lotes con ACOLITE, productos configurables |
| `python/sp_cdom.py` | Post-proceso opcional de aCDOM -- framework listo, algoritmo pendiente |

Todos corren con `/opt/anaconda3/bin/python3` (el unico interprete con
GDAL+ACOLITE funcionando, verificado). Ejemplos:

```bash
cd Satellite_Pipeline/python

# SIEMPRE primero, para cualquier rango nuevo:
/opt/anaconda3/bin/python3 sp_download.py --start 2026-06-01 --end 2026-07-31 --dry-run

# Descarga real + ACOLITE encadenado, streaming (borra L1 tras procesar):
/opt/anaconda3/bin/python3 sp_download.py --start 2026-06-01 --end 2026-07-31 \
    --aoi pr_main_east --sensor S2 --stream

# Incremental, cuando ya haya historial en el inventario:
/opt/anaconda3/bin/python3 sp_update.py --sensor S2,L89 --stream

# Reprocesar con otro set de productos sin volver a descargar:
/opt/anaconda3/bin/python3 sp_acolite.py --products sargassum --reprocess
```

## Credenciales -- `~/.netrc`

ACOLITE lee credenciales de `~/.netrc` (via `acolite/shared/auth.py`, prioridad
mas alta sobre variables de entorno y sobre `config/credentials.txt`). Formato:

```
machine cdse login <usuario_CDSE> password <password_CDSE>
machine earthexplorer_token login <usuario_ERS> password <token_de_64_caracteres>
machine earthexplorer login <usuario_ERS> password <password_ERS>
```

Estado verificado 2026-08-29:
- **EarthExplorer: listo.** `machine earthexplorer_token` y `machine earthexplorer`
  ya estan en `~/.netrc` (usuario `RaymondInfante310`), y el login M2M contra USGS
  se probo en vivo con exito -- una consulta real sobre `pr_main_east`
  (jun-jul 2026) devolvio 26 escenas Landsat 8/9 reales.
- **CDSE: falta.** No hay entrada `machine cdse` todavia. Sin ella, `sp_download.py`
  falla en el preflight de credenciales antes de gastar tiempo en la consulta
  (comportamiento intencional). Anadir la linea de arriba con el usuario/password
  de dataspace.copernicus.eu para activar Sentinel-2.

Un backup del `.netrc` previo a estos cambios quedo en `~/.netrc.backup_pre_acolite`.

## Landsat -- activado 2026-08-31

`config/pipeline.json` -> `sensors.L89.enabled = true`. La consulta M2M ya se
probo en vivo (26 escenas reales sobre `pr_main_east`), pero el flujo completo
descarga -> ACOLITE -> L2W todavia no se ha corrido con una escena real --
esa es la proxima prueba pendiente (ver el plan, paso 4-5).

## LUTs de ACOLITE

`~/.aquacdom/acolite/data/LUT` tenia originalmente solo `S2A/S2B/S2C_MSI`
(1.6 GB). Sentinel-2 procesa 100% offline. Para Landsat, precarga iniciada
2026-08-31 (no requiere credenciales de descarga de escenas, solo red):

```bash
/opt/anaconda3/bin/python3 ~/.aquacdom/acolite/launch_acolite.py \
    --retrieve_luts --sensor L8_OLI,L9_OLI
```

Estado: `L8_OLI` **listo** (confirmado en disco, `data/LUT/ACOLITE-LUT-202110/L8_OLI/`).
`L9_OLI` en progreso la primera vez que se corrio (proceso lento, se dejo de
fondo). Verificar con `ls data/LUT/ACOLITE-LUT-202110/` antes de la primera
corrida real con L9.

## Inventario

`inventory/scenes.csv` es la fuente de verdad de que escenas existen y en que
estado estan (`download_status`, `acolite_status`). Se commitea al repo (sigue
la convencion de `Emisario/checkpoints/`, tambien versionado). `inventory/logs/`
esta en `.gitignore`.
=======
# High-Resolution-Satellite-scene-Download
>>>>>>> fecca6c97b045ad9d32bfd5aa8989389240b8607
