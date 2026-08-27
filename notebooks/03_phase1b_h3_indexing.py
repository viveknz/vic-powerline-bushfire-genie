# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # Phase 1b — H3 Indexing and the Exposure Join
# MAGIC
# MAGIC Notebook 03, revision 2. Turns three geometry layers into one flat table with no
# MAGIC geometry column, where every spatial fact has become an ordinary number.
# MAGIC
# MAGIC That table is what Genie reads. Genie never touches geometry, never generates
# MAGIC spatial SQL, and never reasons about coordinate systems. It answers questions with
# MAGIC plain aggregation, which it does reliably.
# MAGIC
# MAGIC **Input:** `bronze_lga`, `bronze_power_line`, `bronze_fire_scar`
# MAGIC **Output:** `gold_segment_exposure`

# COMMAND ----------

# MAGIC %md
# MAGIC ## What changed in revision 2
# MAGIC
# MAGIC Three corrections, all found by checking the source before trusting it.
# MAGIC
# MAGIC **1. Count fires, not polygons.** `FIREKEY` has 7,649 distinct values across
# MAGIC 109,219 rows — roughly 14 polygons per fire. One fire is mapped as many separate
# MAGIC fragments: burnt patches, unburnt islands, multi-day mapping.
# MAGIC
# MAGIC Counting rows would give a segment inside one large fire a score of 14 instead of
# MAGIC 1. Every "times burnt" number in the app would be inflated, and nothing would look
# MAGIC wrong. Fixed by counting distinct `FIREKEY`.
# MAGIC
# MAGIC **2. Real keys instead of surrogates.** `UFI` is unique across all 396,455 power
# MAGIC line rows, so segments can be traced back to Vicmap.
# MAGIC
# MAGIC **3. The 'None' string problem.** Ingestion cast object columns with
# MAGIC `astype(str)`, which turned Python nulls into the literal string `'None'`. So
# MAGIC `CAUSE IS NULL` matches nothing and `COALESCE(CAUSE, '')` does not help. Every
# MAGIC string column needs `NULLIF(col, 'None')` before use.
# MAGIC
# MAGIC This one is worth remembering. It is invisible until a count comes back wrong.
# MAGIC
# MAGIC Also added: `AREA_HA` (fire size) and `NAME` (fire name). `GEOMETRY_C` was dropped
# MAGIC after inspection — it holds dates, half of them null, some corrupted.

# COMMAND ----------

# MAGIC %md
# MAGIC ## How the join works
# MAGIC
# MAGIC ```
# MAGIC power line  --h3_coverash3-->  cells --+
# MAGIC                                        +--> join on cell --> (segment, fire) pairs
# MAGIC fire scar   --h3_coverash3-->  cells --+
# MAGIC ```
# MAGIC
# MAGIC Two geometries sharing an H3 cell are within roughly 460 m at resolution 8. For a
# MAGIC risk screening tool that matches the accuracy the source data actually has.
# MAGIC
# MAGIC `h3_coverash3` rather than `h3_polyfillash3` because polyfill returns only cells
# MAGIC whose centre is inside the geometry — anything smaller than a cell disappears
# MAGIC silently. Lines have no interior at all, so polyfill would be near useless on the
# MAGIC power layer.

# COMMAND ----------

CATALOG = "workspace"
SCHEMA = "bushfire"
H3_RES = 8

spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"USE SCHEMA {SCHEMA}")

print(f"Working in {CATALOG}.{SCHEMA} at H3 resolution {H3_RES}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — Prep tables
# MAGIC
# MAGIC ### 1a. Segments
# MAGIC
# MAGIC **LV excluded.** 237,187 of 396,455 rows, 60% of the layer, largely urban and
# MAGIC short-span. Bushfire risk lives in HV and SWER through forested country. To
# MAGIC include it later, delete the WHERE clause — bronze still holds everything.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.prep_segment AS
# MAGIC SELECT
# MAGIC   UFI                                        AS segment_id,
# MAGIC   VOLTAGE                                    AS voltage,
# MAGIC   FEATSUBTYP                                 AS feature_subtype,
# MAGIC   CASE
# MAGIC     WHEN FEATSUBTYP = 'power transmission'    THEN 'Transmission'
# MAGIC     WHEN FEATSUBTYP = 'power distribution hv' THEN 'HV Distribution'
# MAGIC     WHEN FEATSUBTYP = 'power distribution lv' THEN 'LV Distribution'
# MAGIC     ELSE 'Unknown'
# MAGIC   END                                        AS voltage_class,
# MAGIC   CASE WHEN VOLTAGE = '12.7 KV' THEN TRUE ELSE FALSE END AS is_swer,
# MAGIC   AUTH_ORG_C                                 AS distributor_code,
# MAGIC   geom_wkb
# MAGIC FROM workspace.bushfire.bronze_power_line
# MAGIC WHERE FEATSUBTYP <> 'power distribution lv';

# COMMAND ----------

# MAGIC %md
# MAGIC ### 1b. Fires
# MAGIC
# MAGIC Three jobs in this cell.
# MAGIC
# MAGIC **`fire_key`** groups polygons belonging to the same fire. Where `FIREKEY` is
# MAGIC missing, the polygon id stands in so the fire still counts once rather than
# MAGIC vanishing from a `COUNT(DISTINCT)`.
# MAGIC
# MAGIC **`cause_group`** collapses the duplicate labels. The source has the same cause
# MAGIC written several ways — hyphenated, comma-separated, one in all caps. Left alone,
# MAGIC Genie splits counts across the variants and reports "Power Lines: 17" when the
# MAGIC true figure is 28.
# MAGIC
# MAGIC **`NULLIF(col, 'None')`** everywhere, for the string-null problem described above.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.prep_fire AS
# MAGIC WITH cleaned AS (
# MAGIC   SELECT
# MAGIC     monotonically_increasing_id()               AS fire_poly_id,
# MAGIC     NULLIF(TRIM(FIREKEY), 'None')               AS firekey_raw,
# MAGIC     CAST(SEASON AS INT)                         AS season,
# MAGIC     START_DATE                                  AS start_date,
# MAGIC     NULLIF(TRIM(FIRETYPE), 'None')              AS fire_type,
# MAGIC     NULLIF(TRIM(NAME), 'None')                  AS fire_name,
# MAGIC     NULLIF(TRIM(FIRE_NO), 'None')               AS fire_number,
# MAGIC     CAST(AREA_HA AS DOUBLE)                     AS area_ha,
# MAGIC     NULLIF(TRIM(TREAT_TYPE), 'None')            AS treatment_type,
# MAGIC     NULLIF(TRIM(FFM_DISTRI), 'None')            AS ffm_district,
# MAGIC     NULLIF(TRIM(FFM_REGION), 'None')            AS ffm_region,
# MAGIC     NULLIF(TRIM(ACCURACY), 'None')              AS accuracy,
# MAGIC     NULLIF(TRIM(CAUSE), 'None')                 AS cause_raw,
# MAGIC     REGEXP_REPLACE(
# MAGIC       REGEXP_REPLACE(
# MAGIC         UPPER(TRIM(COALESCE(NULLIF(TRIM(CAUSE), 'None'), ''))),
# MAGIC         '[-,()]', ' '),
# MAGIC       '\\s+', ' ')                              AS cause_norm,
# MAGIC     geom_wkb
# MAGIC   FROM workspace.bushfire.bronze_fire_scar
# MAGIC )
# MAGIC SELECT
# MAGIC   fire_poly_id,
# MAGIC   COALESCE(
# MAGIC   firekey_raw,
# MAGIC   CONCAT('fno_', CAST(season AS STRING), '_', fire_number),
# MAGIC   CONCAT('poly_', CAST(fire_poly_id AS STRING))
# MAGIC   ) AS fire_key,
# MAGIC   firekey_raw,
# MAGIC   season, start_date, fire_type, fire_name, fire_number, area_ha,
# MAGIC   treatment_type, ffm_district, ffm_region, accuracy, cause_raw,
# MAGIC   CASE
# MAGIC     WHEN cause_norm = ''                              THEN 'Not recorded'
# MAGIC     WHEN cause_norm LIKE '%POWER%'                    THEN 'Powerline'
# MAGIC     WHEN cause_norm LIKE '%LIGHTNING%'                THEN 'Lightning'
# MAGIC     WHEN cause_norm LIKE '%PLANNED BURN%'
# MAGIC       OR cause_norm LIKE '%DEPARTMENTAL PRESCRIBED%'  THEN 'Planned burn'
# MAGIC     WHEN cause_norm LIKE '%BURNING OFF%'
# MAGIC       OR cause_norm LIKE '%WINDROW%'
# MAGIC       OR cause_norm LIKE '%STUBBLE%'                  THEN 'Burning off'
# MAGIC     WHEN cause_norm LIKE '%DELIBERATE%'               THEN 'Deliberate'
# MAGIC     WHEN cause_norm LIKE '%CAMPFIRE%'
# MAGIC       OR cause_norm LIKE '%BARBEQUE%'                 THEN 'Campfire'
# MAGIC     WHEN cause_norm LIKE '%EXHAUST%'
# MAGIC       OR cause_norm LIKE '%VEHICLE%'
# MAGIC       OR cause_norm LIKE '%MACHINE%'
# MAGIC       OR cause_norm LIKE '%CHAINSAW%'                 THEN 'Equipment'
# MAGIC     WHEN cause_norm = 'UNKNOWN'                       THEN 'Unknown'
# MAGIC     ELSE 'Other'
# MAGIC   END AS cause_group,
# MAGIC   CASE WHEN cause_norm LIKE '%POWER%' THEN TRUE ELSE FALSE END AS powerline_caused,
# MAGIC   geom_wkb
# MAGIC FROM cleaned;

# COMMAND ----------

# MAGIC %md
# MAGIC **Check the cleanup worked.** Powerline should total 28 polygons — four source
# MAGIC labels collapsing into one. If it says 17, the `'None'` handling did not take.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT cause_group, COUNT(*) AS polygons, COUNT(DISTINCT fire_key) AS fires
# MAGIC FROM workspace.bushfire.prep_fire
# MAGIC GROUP BY cause_group
# MAGIC ORDER BY polygons DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC **Confirm the polygon-to-fire ratio.** Expect roughly 109,219 polygons against
# MAGIC 7,649 fire keys. This ratio is what justifies counting distinct fires.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   COUNT(*)                      AS polygons,
# MAGIC   COUNT(DISTINCT fire_key)      AS distinct_fires,
# MAGIC   COUNT_IF(firekey_raw IS NULL) AS polygons_without_firekey,
# MAGIC   ROUND(COUNT(*) / COUNT(DISTINCT fire_key), 1) AS polygons_per_fire
# MAGIC FROM workspace.bushfire.prep_fire;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   COUNT(*) AS null_firekey_polys,
# MAGIC   COUNT(DISTINCT fire_number) AS distinct_fire_no,
# MAGIC   COUNT_IF(fire_number IS NULL) AS also_null_fire_no,
# MAGIC   COUNT(DISTINCT CONCAT(COALESCE(fire_name,'?'), '|', CAST(season AS STRING))) AS distinct_name_season
# MAGIC FROM workspace.bushfire.prep_fire
# MAGIC WHERE firekey_raw IS NULL;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT fire_type, COUNT(*) AS polys, COUNT_IF(firekey_raw IS NULL) AS no_key
# MAGIC FROM workspace.bushfire.prep_fire
# MAGIC GROUP BY fire_type;

# COMMAND ----------

# MAGIC %md
# MAGIC ### 1c. LGA boundaries
# MAGIC
# MAGIC **Filter to VIC.** 44 of 137 rows are NSW or SA — councils touching Victoria along
# MAGIC the Murray and the SA border. Without this a Victorian line near the border could
# MAGIC be attributed to Albury.
# MAGIC
# MAGIC **Flag non-councils.** Six alpine resorts and two island groups sit alongside the
# MAGIC 79 councils. Kept, because alpine resorts are high fire risk country with network
# MAGIC infrastructure. The flag lets Genie include or exclude them on request.
# MAGIC
# MAGIC Multi-polygon councils need no special handling — attribution happens at cell
# MAGIC level and groups by name, so the fragments merge naturally.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.prep_lga AS
# MAGIC SELECT
# MAGIC   NAME AS lga_name,
# MAGIC   CASE
# MAGIC     WHEN NAME LIKE '%ALPINE RESORT%' THEN 'Alpine resort'
# MAGIC     WHEN NAME LIKE '%ISLAND%'        THEN 'Island'
# MAGIC     ELSE 'Council'
# MAGIC   END AS lga_type,
# MAGIC   geom_wkb
# MAGIC FROM workspace.bushfire.bronze_lga
# MAGIC WHERE STATE = 'VIC';

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — Explode to H3 cells
# MAGIC
# MAGIC `h3_coverash3` returns an array of cells per geometry. `explode` turns that into
# MAGIC one row per cell, which is what makes the join an ordinary equality join.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.cells_segment AS
# MAGIC SELECT segment_id, explode(h3_coverash3(geom_wkb, 8)) AS h3_cell
# MAGIC FROM workspace.bushfire.prep_segment;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.cells_fire AS
# MAGIC SELECT fire_poly_id, explode(h3_coverash3(geom_wkb, 8)) AS h3_cell
# MAGIC FROM workspace.bushfire.prep_fire;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.cells_lga AS
# MAGIC SELECT lga_name, lga_type, explode(h3_coverash3(geom_wkb, 8)) AS h3_cell
# MAGIC FROM workspace.bushfire.prep_lga;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT 'cells_segment' AS t, COUNT(*) AS rows FROM workspace.bushfire.cells_segment
# MAGIC UNION ALL SELECT 'cells_fire', COUNT(*) FROM workspace.bushfire.cells_fire
# MAGIC UNION ALL SELECT 'cells_lga',  COUNT(*) FROM workspace.bushfire.cells_lga;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — The exposure join
# MAGIC
# MAGIC Segments matched to fire polygons through shared cells. `DISTINCT` because a long
# MAGIC segment and a large scar may share many cells and we want one row per pair.
# MAGIC
# MAGIC This is the expensive step. Everything after it is cheap.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.segment_fire_pairs AS
# MAGIC SELECT DISTINCT s.segment_id, f.fire_poly_id
# MAGIC FROM workspace.bushfire.cells_segment s
# MAGIC JOIN workspace.bushfire.cells_fire f
# MAGIC   ON s.h3_cell = f.h3_cell;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   COUNT(*)                     AS pair_rows,
# MAGIC   COUNT(DISTINCT segment_id)   AS segments_with_fire,
# MAGIC   COUNT(DISTINCT fire_poly_id) AS fire_polygons_touching_network
# MAGIC FROM workspace.bushfire.segment_fire_pairs;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 — LGA attribution
# MAGIC
# MAGIC A segment can cross more than one council. It is attributed to whichever it shares
# MAGIC the most cells with — a reasonable proxy for "mostly in".
# MAGIC
# MAGIC `lga_name` is the tiebreaker so results are deterministic across runs. A metric
# MAGIC that shifts when you re-run the pipeline is impossible to trust.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.segment_lga AS
# MAGIC WITH overlap AS (
# MAGIC   SELECT s.segment_id, l.lga_name, l.lga_type, COUNT(*) AS shared_cells
# MAGIC   FROM workspace.bushfire.cells_segment s
# MAGIC   JOIN workspace.bushfire.cells_lga l ON s.h3_cell = l.h3_cell
# MAGIC   GROUP BY s.segment_id, l.lga_name, l.lga_type
# MAGIC ),
# MAGIC ranked AS (
# MAGIC   SELECT *, ROW_NUMBER() OVER (
# MAGIC     PARTITION BY segment_id ORDER BY shared_cells DESC, lga_name ASC
# MAGIC   ) AS rn
# MAGIC   FROM overlap
# MAGIC )
# MAGIC SELECT segment_id, lga_name, lga_type, shared_cells
# MAGIC FROM ranked WHERE rn = 1;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5 — Aggregate to segment level
# MAGIC
# MAGIC Where the count columns get built. Three decisions matter here.
# MAGIC
# MAGIC ### Distinct fires, not polygons
# MAGIC
# MAGIC `COUNT(DISTINCT fire_key)` throughout. Counting rows would inflate every figure by
# MAGIC roughly 14x for segments inside large, heavily fragmented fires.
# MAGIC
# MAGIC ### Bushfire and planned burn counted separately
# MAGIC
# MAGIC Planned burns substantially outnumber bushfires. A single `times_burnt` metric
# MAGIC would mostly measure DEECA's fuel reduction program, not fire risk — and it would
# MAGIC look entirely plausible while being wrong.
# MAGIC
# MAGIC ### Three time windows
# MAGIC
# MAGIC A fire in 1903 says little about current risk. Three bushfires since 2006 says a
# MAGIC lot. The windows let Genie treat "burnt repeatedly" and "burnt repeatedly
# MAGIC recently" as different questions.
# MAGIC
# MAGIC **Assumption to verify** in check 6: `SEASON` is treated as a calendar year.
# MAGIC Victorian fire seasons run July to June, so season 2020 may mean 2019/20.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.segment_fire_stats AS
# MAGIC SELECT
# MAGIC   p.segment_id,
# MAGIC
# MAGIC   COUNT(DISTINCT f.fire_key) AS times_burnt_total,
# MAGIC   COUNT(DISTINCT CASE WHEN f.fire_type = 'Bushfire' THEN f.fire_key END) AS times_bushfire_total,
# MAGIC   COUNT(DISTINCT CASE WHEN f.fire_type = 'Burn'     THEN f.fire_key END) AS times_planned_total,
# MAGIC
# MAGIC   COUNT(DISTINCT CASE WHEN f.season >= 1980 THEN f.fire_key END) AS times_burnt_since_1980,
# MAGIC   COUNT(DISTINCT CASE WHEN f.season >= 1980 AND f.fire_type = 'Bushfire'
# MAGIC                       THEN f.fire_key END) AS times_bushfire_since_1980,
# MAGIC
# MAGIC   COUNT(DISTINCT CASE WHEN f.season >= 2006 THEN f.fire_key END) AS times_burnt_last_20yr,
# MAGIC   COUNT(DISTINCT CASE WHEN f.season >= 2006 AND f.fire_type = 'Bushfire'
# MAGIC                       THEN f.fire_key END) AS times_bushfire_last_20yr,
# MAGIC   COUNT(DISTINCT CASE WHEN f.fire_type = 'Bushfire' AND f.area_ha >= 1000
# MAGIC                       THEN f.fire_key END) AS times_major_bushfire,
# MAGIC
# MAGIC   MAX(f.season) AS last_burn_season,
# MAGIC   MAX(CASE WHEN f.fire_type = 'Bushfire' THEN f.season END) AS last_bushfire_season,
# MAGIC
# MAGIC   MAX(f.area_ha)                 AS largest_fire_area_ha,
# MAGIC   MAX_BY(f.fire_name, f.area_ha) AS largest_fire_name,
# MAGIC   MAX_BY(f.fire_name, f.season)  AS most_recent_fire_name,
# MAGIC
# MAGIC   COUNT(DISTINCT CASE WHEN f.powerline_caused THEN f.fire_key END) AS powerline_caused_fires,
# MAGIC   MAX(CASE WHEN f.powerline_caused THEN f.season END) AS last_powerline_caused_season
# MAGIC
# MAGIC FROM workspace.bushfire.segment_fire_pairs p
# MAGIC JOIN workspace.bushfire.prep_fire f ON p.fire_poly_id = f.fire_poly_id
# MAGIC GROUP BY p.segment_id;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 6 — The gold table
# MAGIC
# MAGIC One row per segment. No geometry. This is what Genie reads.
# MAGIC
# MAGIC `LEFT JOIN` on the stats, because a segment with no fire history is a valid and
# MAGIC meaningful result — that stretch of network has never burnt. `COALESCE` turns
# MAGIC those into zeros rather than nulls, so `AVG(times_burnt_total)` is correct instead
# MAGIC of silently excluding every safe segment.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.gold_segment_exposure AS
# MAGIC SELECT
# MAGIC   s.segment_id,
# MAGIC   s.voltage,
# MAGIC   s.voltage_class,
# MAGIC   s.is_swer,
# MAGIC   s.distributor_code,
# MAGIC
# MAGIC   COALESCE(l.lga_name, 'Unmatched') AS lga_name,
# MAGIC   COALESCE(l.lga_type, 'Unknown')   AS lga_type,
# MAGIC
# MAGIC   COALESCE(fs.times_burnt_total,         0) AS times_burnt_total,
# MAGIC   COALESCE(fs.times_bushfire_total,      0) AS times_bushfire_total,
# MAGIC   COALESCE(fs.times_planned_total,       0) AS times_planned_total,
# MAGIC   COALESCE(fs.times_burnt_since_1980,    0) AS times_burnt_since_1980,
# MAGIC   COALESCE(fs.times_bushfire_since_1980, 0) AS times_bushfire_since_1980,
# MAGIC   COALESCE(fs.times_burnt_last_20yr,     0) AS times_burnt_last_20yr,
# MAGIC   COALESCE(fs.times_bushfire_last_20yr,  0) AS times_bushfire_last_20yr,
# MAGIC   COALESCE(fs.times_major_bushfire,      0) AS times_major_bushfire,
# MAGIC
# MAGIC   fs.last_burn_season,
# MAGIC   fs.last_bushfire_season,
# MAGIC   CASE WHEN fs.last_bushfire_season IS NOT NULL
# MAGIC        THEN 2026 - fs.last_bushfire_season END AS years_since_last_bushfire,
# MAGIC
# MAGIC   fs.largest_fire_area_ha,
# MAGIC   fs.largest_fire_name,
# MAGIC   fs.most_recent_fire_name,
# MAGIC
# MAGIC   COALESCE(fs.powerline_caused_fires, 0) AS powerline_caused_fires,
# MAGIC   fs.last_powerline_caused_season,
# MAGIC
# MAGIC   CASE WHEN COALESCE(fs.times_major_bushfire, 0) = 0 THEN 'None'
# MAGIC        WHEN fs.times_major_bushfire = 1              THEN 'Low'
# MAGIC        WHEN fs.times_major_bushfire <= 3             THEN 'Moderate'
# MAGIC        ELSE 'High'
# MAGIC   END AS bushfire_exposure_band
# MAGIC
# MAGIC FROM workspace.bushfire.prep_segment s
# MAGIC LEFT JOIN workspace.bushfire.segment_lga l         ON s.segment_id = l.segment_id
# MAGIC LEFT JOIN workspace.bushfire.segment_fire_stats fs ON s.segment_id = fs.segment_id;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 7 — Validation
# MAGIC
# MAGIC Six checks. Run them every time the pipeline changes.

# COMMAND ----------

# MAGIC %md
# MAGIC **Check 1 — no segments lost or duplicated.** Gold must match prep exactly. A
# MAGIC LEFT JOIN that accidentally fans out shows up here.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   (SELECT COUNT(*) FROM workspace.bushfire.prep_segment)          AS prep_rows,
# MAGIC   (SELECT COUNT(*) FROM workspace.bushfire.gold_segment_exposure) AS gold_rows,
# MAGIC   (SELECT COUNT(*) FROM workspace.bushfire.prep_segment)
# MAGIC     = (SELECT COUNT(*) FROM workspace.bushfire.gold_segment_exposure) AS match;

# COMMAND ----------

# MAGIC %md
# MAGIC **Check 2 — one row per segment.** Must return zero.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT COUNT(*) AS duplicate_segments FROM (
# MAGIC   SELECT segment_id FROM workspace.bushfire.gold_segment_exposure
# MAGIC   GROUP BY segment_id HAVING COUNT(*) > 1
# MAGIC );

# COMMAND ----------

# MAGIC %md
# MAGIC **Check 3 — counts internally consistent.** Bushfire plus planned cannot exceed
# MAGIC total, and narrower windows cannot exceed wider ones. Must return zero.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT COUNT(*) AS inconsistent_rows
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC WHERE times_bushfire_total + times_planned_total > times_burnt_total
# MAGIC    OR times_burnt_since_1980   > times_burnt_total
# MAGIC    OR times_burnt_last_20yr    > times_burnt_since_1980
# MAGIC    OR times_bushfire_last_20yr > times_bushfire_since_1980;

# COMMAND ----------

# MAGIC %md
# MAGIC **Check 4 — LGA attribution coverage.** Some unmatched segments are expected near
# MAGIC borders and coastlines. A large share would mean gaps in the LGA cell layer.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   lga_name = 'Unmatched' AS unmatched,
# MAGIC   COUNT(*) AS segments,
# MAGIC   ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC GROUP BY 1;

# COMMAND ----------

# MAGIC %md
# MAGIC **Check 5 — does the distribution look sane?** No single right answer, but if
# MAGIC everything lands in one band something is wrong.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT bushfire_exposure_band, COUNT(*) AS segments,
# MAGIC        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC GROUP BY bushfire_exposure_band
# MAGIC ORDER BY segments DESC;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT lga_name, voltage_class, COUNT(*) AS segments
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC WHERE bushfire_exposure_band = 'High'
# MAGIC GROUP BY lga_name, voltage_class
# MAGIC ORDER BY segments DESC
# MAGIC LIMIT 15;

# COMMAND ----------

# MAGIC %md
# MAGIC **Check 6 — the SEASON assumption.** Compare `START_DATE` year against `SEASON`.
# MAGIC If they differ consistently by one, seasons are labelled by their ending year and
# MAGIC the 1980 and 2006 boundaries need shifting.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT g.lga_name, g.segment_id, g.times_bushfire_since_1980,
# MAGIC        g.largest_fire_area_ha, g.most_recent_fire_name, g.last_bushfire_season
# MAGIC FROM workspace.bushfire.gold_segment_exposure g
# MAGIC WHERE g.lga_name IN ('FRANKSTON','BRIMBANK')
# MAGIC   AND g.bushfire_exposure_band = 'High'
# MAGIC LIMIT 10;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT fire_key, fire_name, COUNT(*) AS polys,
# MAGIC        ROUND(MAX(area_ha),1) AS max_area, ROUND(SUM(area_ha),1) AS sum_area
# MAGIC FROM workspace.bushfire.prep_fire
# MAGIC WHERE season = 2020 AND fire_type = 'Bushfire'
# MAGIC GROUP BY fire_key, fire_name
# MAGIC ORDER BY sum_area DESC
# MAGIC LIMIT 10;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT season, YEAR(start_date) AS start_year, COUNT(*) AS n
# MAGIC FROM workspace.bushfire.prep_fire
# MAGIC WHERE start_date IS NOT NULL AND season >= 2018
# MAGIC GROUP BY season, YEAR(start_date)
# MAGIC ORDER BY season DESC, n DESC
# MAGIC LIMIT 20;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Sample answers
# MAGIC
# MAGIC The kind of question the app has to handle. If these return sensible results the
# MAGIC data is ready for Genie.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT lga_name,
# MAGIC        COUNT(*) AS segments,
# MAGIC        ROUND(AVG(times_bushfire_since_1980), 2) AS avg_bushfires,
# MAGIC        COUNT_IF(bushfire_exposure_band = 'High') AS high_exposure_segments
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC WHERE voltage_class = 'HV Distribution'
# MAGIC GROUP BY lga_name
# MAGIC ORDER BY avg_bushfires DESC
# MAGIC LIMIT 15;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT is_swer,
# MAGIC        COUNT(*) AS segments,
# MAGIC        ROUND(AVG(times_bushfire_since_1980), 3) AS avg_bushfires,
# MAGIC        ROUND(100.0 * COUNT_IF(bushfire_exposure_band IN ('Moderate','High'))
# MAGIC              / COUNT(*), 1) AS pct_moderate_or_high
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC GROUP BY is_swer;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT lga_name, voltage, powerline_caused_fires, last_powerline_caused_season,
# MAGIC        most_recent_fire_name
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC WHERE powerline_caused_fires > 0
# MAGIC ORDER BY powerline_caused_fires DESC, last_powerline_caused_season DESC
# MAGIC LIMIT 20;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done
# MAGIC
# MAGIC `gold_segment_exposure` — one row per line segment, no geometry, every spatial
# MAGIC fact reduced to a number Genie can aggregate.
# MAGIC
# MAGIC Next: notebook 04, the semantic layer. Column comments, metric definitions and
# MAGIC Genie instructions. That is where 20 of the 40 contest points live, and it matters
# MAGIC more than anything in this notebook.