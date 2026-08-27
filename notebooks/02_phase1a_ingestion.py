# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 1a — Shapefiles to Bronze Delta
# MAGIC
# MAGIC Notebook 02. Reads three ESRI shapefiles from a Unity Catalog volume and writes
# MAGIC them to Delta tables with geometry preserved as WKB.
# MAGIC
# MAGIC This notebook is idempotent. Re-running it overwrites the bronze tables with the
# MAGIC same content.
# MAGIC
# MAGIC **Prerequisite:** the shapefiles must already be uploaded to
# MAGIC `/Volumes/workspace/bushfire/raw/`, each in its own subfolder, with all five
# MAGIC sibling files present (`.shp`, `.shx`, `.dbf`, `.prj`, `.cpg`).

# COMMAND ----------

# MAGIC %md
# MAGIC ## Why GeoPandas is here at all
# MAGIC
# MAGIC Databricks SQL cannot read shapefiles. H3 and ST functions operate on geometry
# MAGIC that is already in a table, so something has to parse `.shp` and `.dbf` into rows
# MAGIC first. That is GeoPandas' entire job in this project.
# MAGIC
# MAGIC Once geometry is a WKB column in Delta, GeoPandas is finished and every later
# MAGIC notebook is pure SQL.

# COMMAND ----------

# MAGIC %pip install geopandas pyogrio
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC The `restartPython()` call is required. Without it, the import below fails even
# MAGIC though the install succeeded.
# MAGIC
# MAGIC It also wipes every variable defined earlier in the session, which is why all the
# MAGIC setup lives in the single cell that follows rather than being spread around.

# COMMAND ----------

import logging
import os
import sys

import geopandas as gpd
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("bushfire.ingest")

CATALOG = "workspace"
SCHEMA = "bushfire"
VOLUME = f"/Volumes/{CATALOG}/{SCHEMA}/raw"

LAYERS = {
    "lga": {
        "path": f"{VOLUME}/vmadmin/AD_LGA_AREA_POLYGON.shp",
        "table": f"{CATALOG}.{SCHEMA}.bronze_lga",
        "expect_geom": "Polygon",
        "expect_rows": 137,
    },
    "power": {
        "path": f"{VOLUME}/vmfeat/POWER_LINE.shp",
        "table": f"{CATALOG}.{SCHEMA}.bronze_power_line",
        "expect_geom": "LineString",
        "expect_rows": 396_455,
    },
    "fire": {
        "path": f"{VOLUME}/fire/FIRE_HISTORY_SCAR.shp",
        "table": f"{CATALOG}.{SCHEMA}.bronze_fire_scar",
        "expect_geom": "Polygon",
        "expect_rows": 109_219,
    },
}

EXPECTED_CRS = "EPSG:7844"  # GDA2020 geographic, as ordered from DataShare

log.info("Config loaded. Volume=%s", VOLUME)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Helper functions
# MAGIC
# MAGIC Three of them. Read and profile, validate, write. Nothing clever.

# COMMAND ----------


def profile_layer(key: str, max_rows: int | None = None) -> gpd.GeoDataFrame:
    """Read a shapefile and log everything worth knowing about it.

    Set max_rows to peek at the schema without loading the whole file. Note that
    shapefiles are spatially ordered, so a sample is fine for discovering column
    names but useless for judging distributions.
    """
    cfg = LAYERS[key]
    log.info("Reading %s", cfg["path"])

    gdf = gpd.read_file(cfg["path"], engine="pyogrio", max_features=max_rows)

    log.info("Rows: %s", f"{len(gdf):,}")
    log.info("CRS:  %s", gdf.crs)
    log.info("Geometry types: %s", gdf.geom_type.value_counts().to_dict())
    log.info("Columns (%d):", len(gdf.columns))
    for c in gdf.columns:
        nulls = gdf[c].isna().sum()
        log.info("  %-15s %-12s nulls=%s", c, str(gdf[c].dtype), f"{nulls:,}")

    invalid = (~gdf.geometry.is_valid).sum()
    empty = gdf.geometry.is_empty.sum()
    log.info("Invalid geometries: %s | Empty: %s", invalid, empty)

    return gdf


def validate(gdf: gpd.GeoDataFrame, key: str) -> list[str]:
    """Return a list of problems. An empty list means the layer is good to load."""
    cfg = LAYERS[key]
    problems = []

    if len(gdf) == 0:
        problems.append("zero rows")

    if gdf.crs is None:
        problems.append("no CRS set — is the .prj file present?")
    elif str(gdf.crs) != EXPECTED_CRS:
        problems.append(f"CRS is {gdf.crs}, expected {EXPECTED_CRS}")

    types = set(gdf.geom_type.dropna().unique())
    expected = {cfg["expect_geom"], "Multi" + cfg["expect_geom"]}
    if not types & expected:
        problems.append(f"geometry types {types}, expected one of {expected}")

    if gdf.geometry.is_empty.any():
        problems.append(f"{gdf.geometry.is_empty.sum()} empty geometries")

    return problems


def to_delta(gdf: gpd.GeoDataFrame, key: str, mode: str = "overwrite") -> int:
    """Write a GeoDataFrame to Delta, geometry as a WKB binary column.

    WKB rather than WKT because it is more compact and because Databricks H3 and ST
    functions accept it directly.
    """
    cfg = LAYERS[key]

    df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    df["geom_wkb"] = gdf.geometry.to_wkb()
    df["src_crs"] = str(gdf.crs)

    # Mixed-type object columns break createDataFrame. Cast them to string.
    for c in df.columns:
        if df[c].dtype == "object" and c != "geom_wkb":
            df[c] = df[c].astype(str)

    log.info("Converting %s rows to Spark...", f"{len(df):,}")
    sdf = spark.createDataFrame(df)

    log.info("Writing to %s", cfg["table"])
    (
        sdf.write.mode(mode)
        .option("overwriteSchema", "true")
        .saveAsTable(cfg["table"])
    )

    written = spark.table(cfg["table"]).count()
    log.info("Wrote %s rows to %s", f"{written:,}", cfg["table"])

    if mode == "overwrite":
        assert written == len(df), f"Row count mismatch: {written} vs {len(df)}"

    return written


log.info("Helpers defined.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Confirm the files are where we think they are
# MAGIC
# MAGIC Cheap check that catches the most common failure — uploading only the `.shp` and
# MAGIC leaving its siblings behind.

# COMMAND ----------

for key, cfg in LAYERS.items():
    folder = os.path.dirname(cfg["path"])
    log.info("--- %s: %s", key, folder)
    if not os.path.isdir(folder):
        log.error("    FOLDER MISSING")
        continue
    for f in sorted(os.listdir(folder)):
        size_mb = os.path.getsize(os.path.join(folder, f)) / 1e6
        log.info("    %-40s %8.1f MB", f, size_mb)
    assert os.path.exists(cfg["path"]), f"Shapefile not found: {cfg['path']}"

log.info("All three shapefiles present.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Load
# MAGIC
# MAGIC Smallest first. If the pattern breaks, it breaks on the 24 MB file rather than
# MAGIC the 248 MB one.

# COMMAND ----------

gdf_lga = profile_layer("lga")

for issue in validate(gdf_lga, "lga"):
    log.warning("VALIDATION: %s", issue)

to_delta(gdf_lga, "lga")

# COMMAND ----------

gdf_power = profile_layer("power")

for issue in validate(gdf_power, "power"):
    log.warning("VALIDATION: %s", issue)

to_delta(gdf_power, "power")

# COMMAND ----------

gdf_fire = profile_layer("fire")

for issue in validate(gdf_fire, "fire"):
    log.warning("VALIDATION: %s", issue)

to_delta(gdf_fire, "fire")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Profiling helper
# MAGIC
# MAGIC This is how the data quality findings in notebook 01 were discovered. Prints
# MAGIC every low-cardinality text column with its value distribution, which is the
# MAGIC fastest way to find the columns that carry meaning.
# MAGIC
# MAGIC Run it against any layer when you need to remember what is in there.

# COMMAND ----------


def show_categoricals(gdf: gpd.GeoDataFrame, max_distinct: int = 30) -> None:
    """Print value counts for every text column with few enough distinct values."""
    for c in gdf.columns:
        if gdf[c].dtype == "object" and c != "geometry":
            n = gdf[c].nunique()
            if n <= max_distinct:
                print(f"--- {c} ({n} distinct)")
                print(gdf[c].value_counts().to_dict())
                print()


show_categoricals(gdf_power)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Baseline checks
# MAGIC
# MAGIC Run these after every load and keep the output. If something looks wrong on day
# MAGIC four, this is what you compare against.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT 'bronze_lga' AS layer, COUNT(*) AS rows
# MAGIC FROM workspace.bushfire.bronze_lga
# MAGIC UNION ALL
# MAGIC SELECT 'bronze_power_line', COUNT(*) FROM workspace.bushfire.bronze_power_line
# MAGIC UNION ALL
# MAGIC SELECT 'bronze_fire_scar', COUNT(*) FROM workspace.bushfire.bronze_fire_scar;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Geometry health
# MAGIC
# MAGIC `distinct_crs` must be 1 on every layer, and the same value on all three. Mixed
# MAGIC datums produce joins that are quietly wrong rather than obviously broken.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT 'lga' AS layer, COUNT(*) AS total_rows,
# MAGIC        SUM(CASE WHEN geom_wkb IS NULL THEN 1 ELSE 0 END) AS null_geom,
# MAGIC        COUNT(DISTINCT src_crs) AS distinct_crs, MIN(src_crs) AS crs
# MAGIC FROM workspace.bushfire.bronze_lga
# MAGIC UNION ALL
# MAGIC SELECT 'power', COUNT(*), SUM(CASE WHEN geom_wkb IS NULL THEN 1 ELSE 0 END),
# MAGIC        COUNT(DISTINCT src_crs), MIN(src_crs)
# MAGIC FROM workspace.bushfire.bronze_power_line
# MAGIC UNION ALL
# MAGIC SELECT 'fire', COUNT(*), SUM(CASE WHEN geom_wkb IS NULL THEN 1 ELSE 0 END),
# MAGIC        COUNT(DISTINCT src_crs), MIN(src_crs)
# MAGIC FROM workspace.bushfire.bronze_fire_scar;

# COMMAND ----------

# MAGIC %md
# MAGIC ### H3 volume at resolution 8
# MAGIC
# MAGIC The number that determines whether Phase 1b is affordable. Recorded baseline:
# MAGIC fire 505,444 and power 560,406.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT 'fire' AS layer, SUM(size(h3_coverash3(geom_wkb, 8))) AS cells_r8
# MAGIC FROM workspace.bushfire.bronze_fire_scar
# MAGIC UNION ALL
# MAGIC SELECT 'power', SUM(size(h3_coverash3(geom_wkb, 8)))
# MAGIC FROM workspace.bushfire.bronze_power_line
# MAGIC UNION ALL
# MAGIC SELECT 'lga_vic', SUM(size(h3_coverash3(geom_wkb, 8)))
# MAGIC FROM workspace.bushfire.bronze_lga WHERE STATE = 'VIC';

# COMMAND ----------

# MAGIC %md
# MAGIC ### The check that matters most
# MAGIC
# MAGIC Must return zero on both layers.
# MAGIC
# MAGIC This is what justifies using `h3_coverash3` rather than `h3_polyfillash3`.
# MAGIC Polyfill returns only cells whose centre falls inside the geometry, so anything
# MAGIC smaller than a cell disappears — silently, as a missing row rather than an error.
# MAGIC Tested on the LGA layer at resolution 7, three of the first five councils returned
# MAGIC zero cells.
# MAGIC
# MAGIC A fire scar that vanishes is a wrong answer nobody notices.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT 'fire' AS layer, COUNT(*) AS zero_cell_rows
# MAGIC FROM workspace.bushfire.bronze_fire_scar
# MAGIC WHERE size(h3_coverash3(geom_wkb, 8)) = 0
# MAGIC UNION ALL
# MAGIC SELECT 'power', COUNT(*)
# MAGIC FROM workspace.bushfire.bronze_power_line
# MAGIC WHERE size(h3_coverash3(geom_wkb, 8)) = 0;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done
# MAGIC
# MAGIC Three bronze tables, geometry intact, baselines recorded.
# MAGIC
# MAGIC Bronze is deliberately raw — no filtering, no cleaning, every original column
# MAGIC kept. All the corrections identified in notebook 01 happen in the curated layer,
# MAGIC so the raw data stays available if a decision needs revisiting.
# MAGIC
# MAGIC Next: notebook 03, H3 indexing and the exposure join.
