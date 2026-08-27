# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 1b — H3 Indexing and the Exposure Join
# MAGIC
# MAGIC Notebook 03. Turns three geometry layers into one flat table with no geometry
# MAGIC column, where every spatial fact has already become an ordinary number.
# MAGIC
# MAGIC That table is what Genie reads. Genie never touches geometry, never generates
# MAGIC spatial SQL, and never has to reason about coordinate systems. It answers
# MAGIC questions with plain aggregation, which it does reliably.
# MAGIC
# MAGIC **Input:** `bronze_lga`, `bronze_power_line`, `bronze_fire_scar`
# MAGIC **Output:** `gold_segment_exposure`

# COMMAND ----------

# MAGIC %md
# MAGIC ## How the join works
# MAGIC
# MAGIC The real question is "which fire scars intersect which line segments". Done as
# MAGIC polygon-to-line intersection across 109k polygons and 396k lines, that is
# MAGIC expensive and fragile.
# MAGIC
# MAGIC H3 turns it into an integer join:
# MAGIC
# MAGIC ```
# MAGIC power line  ──h3_coverash3──>  cells ─┐
# MAGIC                                        ├──> join on cell ──> (segment, fire) pairs
# MAGIC fire scar   ──h3_coverash3──>  cells ─┘
# MAGIC ```
# MAGIC
# MAGIC Two geometries that share an H3 cell are within roughly 460 m of each other at
# MAGIC resolution 8. For a risk screening tool that is the right tolerance — it is
# MAGIC comparable to the accuracy the source data actually has.
# MAGIC
# MAGIC ### Why cover and not polyfill
# MAGIC
# MAGIC `h3_polyfillash3` returns only cells whose **centre** falls inside the geometry.
# MAGIC Anything smaller than a cell returns nothing. Tested on the LGA layer at
# MAGIC resolution 7, three of the first five councils came back with zero cells.
# MAGIC
# MAGIC `h3_coverash3` returns every cell the geometry touches, so nothing disappears.
# MAGIC That matters more than it sounds: a fire scar that vanishes is not an error, it
# MAGIC is a wrong answer nobody notices.
# MAGIC
# MAGIC Lines have no interior at all, so polyfill on the power layer would be close to
# MAGIC useless.

# COMMAND ----------

CATALOG = "workspace"
SCHEMA = "bushfire"
H3_RES = 8

spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"USE SCHEMA {SCHEMA}")

print(f"Working in {CATALOG}.{SCHEMA} at H3 resolution {H3_RES}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 0 — Check what columns we actually have
# MAGIC
# MAGIC Run this before anything else. The code below references specific column names
# MAGIC and this is where you find out if any are missing or spelled differently.
# MAGIC
# MAGIC Shapefile truncates field names to 10 characters, so `FFM_DISTRICT` arrives as
# MAGIC `FFM_DISTRI`. Check the `*_column_names.txt` files from DataShare if anything
# MAGIC looks odd.

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE workspace.bushfire.bronze_power_line;

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE workspace.bushfire.bronze_fire_scar;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — Prep tables with stable surrogate keys
# MAGIC
# MAGIC Every segment and every fire needs an identifier that survives the joins. Rather
# MAGIC than assume `UFI` exists and is unique on both layers, generate one and
# MAGIC materialise it. Once written, the ids are stable.
# MAGIC
# MAGIC ### Filters applied here
# MAGIC
# MAGIC **Power lines: LV excluded.** LV is 237,187 of 396,455 rows — 60% of the layer —
# MAGIC and it is largely urban, short-span, often underground. Bushfire risk lives in HV
# MAGIC and SWER through forested country. Excluding it halves the H3 work.
# MAGIC
# MAGIC To include LV later, delete one line from the WHERE clause. Bronze still holds
# MAGIC everything.
# MAGIC
# MAGIC **Fire scars: none.** All 109,219 rows, back to 1903. The bushfire and planned
# MAGIC burn split happens at aggregation time, not here.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.prep_segment AS
# MAGIC SELECT
# MAGIC   monotonically_increasing_id()                AS segment_id,
# MAGIC   VOLTAGE                                      AS voltage,
# MAGIC   FEATSUBTYP                                   AS feature_subtype,
# MAGIC   CASE
# MAGIC     WHEN FEATSUBTYP = 'power transmission'     THEN 'Transmission'
# MAGIC     WHEN FEATSUBTYP = 'power distribution hv'  THEN 'HV Distribution'
# MAGIC     WHEN FEATSUBTYP = 'power distribution lv'  THEN 'LV Distribution'
# MAGIC     ELSE 'Unknown'
# MAGIC   END                                          AS voltage_class,
# MAGIC   CASE WHEN VOLTAGE = '12.7 KV' THEN TRUE ELSE FALSE END AS is_swer,
# MAGIC   AUTH_ORG_C                                   AS distributor_code,
# MAGIC   geom_wkb
# MAGIC FROM workspace.bushfire.bronze_power_line
# MAGIC WHERE FEATSUBTYP <> 'power distribution lv';

# COMMAND ----------

# MAGIC %md
# MAGIC ### Fire prep, with the CAUSE cleanup
# MAGIC
# MAGIC Two problems in the source, both found during profiling:
# MAGIC
# MAGIC **Duplicate category labels.** The same cause appears hyphenated, comma-separated
# MAGIC and in one case all caps. Left alone, Genie splits counts across the variants and
# MAGIC reports "Power Lines: 17" when the true figure is 28.
# MAGIC
# MAGIC **97% null.** Cause is only recorded for investigated fires. This is stated in the
# MAGIC Genie instructions so it qualifies rather than generalising from a 3% sample.
# MAGIC
# MAGIC The normalisation below collapses everything to a handful of clean groups.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.prep_fire AS
# MAGIC WITH cleaned AS (
# MAGIC   SELECT
# MAGIC     monotonically_increasing_id() AS fire_id,
# MAGIC     CAST(SEASON AS INT)           AS season,
# MAGIC     START_DATE                    AS start_date,
# MAGIC     FIRETYPE                      AS fire_type,
# MAGIC     TREAT_TYPE                    AS treatment_type,
# MAGIC     FFM_DISTRI                    AS ffm_district,
# MAGIC     FFM_REGION                    AS ffm_region,
# MAGIC     ACCURACY                      AS accuracy,
# MAGIC     -- normalise: uppercase, strip punctuation, collapse whitespace
# MAGIC     REGEXP_REPLACE(
# MAGIC       REGEXP_REPLACE(UPPER(TRIM(COALESCE(CAUSE, ''))), '[-,()]', ' '),
# MAGIC       '\\s+', ' '
# MAGIC     )                             AS cause_norm,
# MAGIC     CAUSE                         AS cause_raw,
# MAGIC     geom_wkb
# MAGIC   FROM workspace.bushfire.bronze_fire_scar
# MAGIC )
# MAGIC SELECT
# MAGIC   *,
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
# MAGIC   CASE WHEN cause_norm LIKE '%POWER%' THEN TRUE ELSE FALSE END AS powerline_caused
# MAGIC FROM cleaned;

# COMMAND ----------

# MAGIC %md
# MAGIC Check the cleanup worked. Powerline should now total 28 across all four original
# MAGIC label variants, not 17.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT cause_group, COUNT(*) AS n
# MAGIC FROM workspace.bushfire.prep_fire
# MAGIC GROUP BY cause_group
# MAGIC ORDER BY n DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC ### LGA prep
# MAGIC
# MAGIC Three corrections, all from the profiling findings:
# MAGIC
# MAGIC **Filter to VIC.** 44 of 137 rows are NSW or SA — councils that touch Victoria
# MAGIC along the Murray and the SA border. A Victorian line near the border could
# MAGIC otherwise be attributed to Albury.
# MAGIC
# MAGIC **Flag non-councils.** Six alpine resorts and two island groups sit alongside the
# MAGIC 79 councils. They are kept, because alpine resorts are high fire risk country with
# MAGIC network infrastructure. The flag lets Genie include or exclude them on request.
# MAGIC
# MAGIC **Multi-polygon councils are not a problem here.** Bass Coast has 3 polygons,
# MAGIC French-Elizabeth-Sandstone Islands 3, Murrindindi and Queenscliffe 2 each. Because
# MAGIC we aggregate at cell level and attribute by name, the fragments merge naturally
# MAGIC and nothing triple-counts.

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
# MAGIC
# MAGIC Expect roughly 560k rows for power (before the LV filter — will be lower),
# MAGIC 505k for fire, 278k for LGA.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.cells_segment AS
# MAGIC SELECT
# MAGIC   segment_id,
# MAGIC   explode(h3_coverash3(geom_wkb, 8)) AS h3_cell
# MAGIC FROM workspace.bushfire.prep_segment;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.cells_fire AS
# MAGIC SELECT
# MAGIC   fire_id,
# MAGIC   explode(h3_coverash3(geom_wkb, 8)) AS h3_cell
# MAGIC FROM workspace.bushfire.prep_fire;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.cells_lga AS
# MAGIC SELECT
# MAGIC   lga_name,
# MAGIC   lga_type,
# MAGIC   explode(h3_coverash3(geom_wkb, 8)) AS h3_cell
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
# MAGIC Segments matched to fires through shared cells. `DISTINCT` because a long segment
# MAGIC and a large fire scar may share many cells, and we want one row per pair.
# MAGIC
# MAGIC This is the expensive step. Everything else is cheap.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.segment_fire_pairs AS
# MAGIC SELECT DISTINCT
# MAGIC   s.segment_id,
# MAGIC   f.fire_id
# MAGIC FROM workspace.bushfire.cells_segment s
# MAGIC JOIN workspace.bushfire.cells_fire f
# MAGIC   ON s.h3_cell = f.h3_cell;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   COUNT(*)                    AS pair_rows,
# MAGIC   COUNT(DISTINCT segment_id)  AS segments_with_fire,
# MAGIC   COUNT(DISTINCT fire_id)     AS fires_touching_network
# MAGIC FROM workspace.bushfire.segment_fire_pairs;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 — LGA attribution
# MAGIC
# MAGIC A segment can cross more than one council. We attribute it to whichever LGA it
# MAGIC shares the most cells with, which is a reasonable proxy for "mostly in".
# MAGIC
# MAGIC The `ROW_NUMBER` picks the winner. `lga_name` is the tiebreaker so the result is
# MAGIC deterministic across runs — important, because a metric that changes when you
# MAGIC re-run the pipeline is impossible to trust.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.segment_lga AS
# MAGIC WITH overlap AS (
# MAGIC   SELECT
# MAGIC     s.segment_id,
# MAGIC     l.lga_name,
# MAGIC     l.lga_type,
# MAGIC     COUNT(*) AS shared_cells
# MAGIC   FROM workspace.bushfire.cells_segment s
# MAGIC   JOIN workspace.bushfire.cells_lga l
# MAGIC     ON s.h3_cell = l.h3_cell
# MAGIC   GROUP BY s.segment_id, l.lga_name, l.lga_type
# MAGIC ),
# MAGIC ranked AS (
# MAGIC   SELECT *,
# MAGIC     ROW_NUMBER() OVER (
# MAGIC       PARTITION BY segment_id
# MAGIC       ORDER BY shared_cells DESC, lga_name ASC
# MAGIC     ) AS rn
# MAGIC   FROM overlap
# MAGIC )
# MAGIC SELECT segment_id, lga_name, lga_type, shared_cells
# MAGIC FROM ranked
# MAGIC WHERE rn = 1;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5 — Aggregate to segment level
# MAGIC
# MAGIC Where the six count columns get built.
# MAGIC
# MAGIC ### Why bushfire and planned burn are counted separately
# MAGIC
# MAGIC This is the most consequential decision in the notebook. Planned burns
# MAGIC substantially outnumber bushfires in the source data. A single `times_burnt`
# MAGIC metric would mostly be measuring DEECA's fuel reduction program, not fire risk —
# MAGIC and it would look plausible while being wrong.
# MAGIC
# MAGIC ### Why three time windows
# MAGIC
# MAGIC A fire in 1903 says little about current risk. Three bushfires since 2006 says a
# MAGIC lot. The windows let Genie answer "burnt repeatedly" and "burnt repeatedly
# MAGIC recently" as genuinely different questions.
# MAGIC
# MAGIC **Assumption to verify:** `SEASON` is treated as a calendar year. Victorian fire
# MAGIC seasons run July to June, so season 2020 may mean 2019/20. Check `START_DATE`
# MAGIC against `SEASON` on a few rows and adjust the window boundaries if needed.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.segment_fire_stats AS
# MAGIC SELECT
# MAGIC   p.segment_id,
# MAGIC
# MAGIC   COUNT(*)                                                       AS times_burnt_total,
# MAGIC   COUNT_IF(f.fire_type = 'Bushfire')                             AS times_bushfire_total,
# MAGIC   COUNT_IF(f.fire_type = 'Burn')                                 AS times_planned_total,
# MAGIC
# MAGIC   COUNT_IF(f.season >= 1980)                                     AS times_burnt_since_1980,
# MAGIC   COUNT_IF(f.season >= 1980 AND f.fire_type = 'Bushfire')        AS times_bushfire_since_1980,
# MAGIC
# MAGIC   COUNT_IF(f.season >= 2006)                                     AS times_burnt_last_20yr,
# MAGIC   COUNT_IF(f.season >= 2006 AND f.fire_type = 'Bushfire')        AS times_bushfire_last_20yr,
# MAGIC
# MAGIC   MAX(f.season)                                                  AS last_burn_season,
# MAGIC   MAX(CASE WHEN f.fire_type = 'Bushfire' THEN f.season END)      AS last_bushfire_season,
# MAGIC
# MAGIC   COUNT_IF(f.powerline_caused)                                   AS powerline_caused_fires,
# MAGIC   MAX(CASE WHEN f.powerline_caused THEN f.season END)            AS last_powerline_caused_season
# MAGIC
# MAGIC FROM workspace.bushfire.segment_fire_pairs p
# MAGIC JOIN workspace.bushfire.prep_fire f
# MAGIC   ON p.fire_id = f.fire_id
# MAGIC GROUP BY p.segment_id;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 6 — The gold table
# MAGIC
# MAGIC One row per segment. No geometry column. This is what Genie reads.
# MAGIC
# MAGIC `LEFT JOIN` on the stats, because a segment with no fire history is a valid and
# MAGIC meaningful result — it means that stretch of network has never burnt. `COALESCE`
# MAGIC turns those into zeros rather than nulls, so `AVG(times_burnt_total)` is correct
# MAGIC rather than silently excluding the safe segments.

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
# MAGIC   COALESCE(l.lga_name, 'Outside Victoria or unmatched') AS lga_name,
# MAGIC   COALESCE(l.lga_type, 'Unknown')                       AS lga_type,
# MAGIC
# MAGIC   COALESCE(fs.times_burnt_total,          0) AS times_burnt_total,
# MAGIC   COALESCE(fs.times_bushfire_total,       0) AS times_bushfire_total,
# MAGIC   COALESCE(fs.times_planned_total,        0) AS times_planned_total,
# MAGIC   COALESCE(fs.times_burnt_since_1980,     0) AS times_burnt_since_1980,
# MAGIC   COALESCE(fs.times_bushfire_since_1980,  0) AS times_bushfire_since_1980,
# MAGIC   COALESCE(fs.times_burnt_last_20yr,      0) AS times_burnt_last_20yr,
# MAGIC   COALESCE(fs.times_bushfire_last_20yr,   0) AS times_bushfire_last_20yr,
# MAGIC
# MAGIC   fs.last_burn_season,
# MAGIC   fs.last_bushfire_season,
# MAGIC   CASE WHEN fs.last_bushfire_season IS NOT NULL
# MAGIC        THEN 2026 - fs.last_bushfire_season END AS years_since_last_bushfire,
# MAGIC
# MAGIC   COALESCE(fs.powerline_caused_fires, 0) AS powerline_caused_fires,
# MAGIC   fs.last_powerline_caused_season,
# MAGIC
# MAGIC   CASE WHEN COALESCE(fs.times_bushfire_since_1980, 0) = 0 THEN 'None'
# MAGIC        WHEN fs.times_bushfire_since_1980 = 1              THEN 'Low'
# MAGIC        WHEN fs.times_bushfire_since_1980 <= 3             THEN 'Moderate'
# MAGIC        ELSE 'High'
# MAGIC   END AS bushfire_exposure_band
# MAGIC
# MAGIC FROM workspace.bushfire.prep_segment s
# MAGIC LEFT JOIN workspace.bushfire.segment_lga l          ON s.segment_id = l.segment_id
# MAGIC LEFT JOIN workspace.bushfire.segment_fire_stats fs  ON s.segment_id = fs.segment_id;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 7 — Validation
# MAGIC
# MAGIC Six checks. Run them every time the pipeline changes.

# COMMAND ----------

# MAGIC %md
# MAGIC **Check 1 — no segments lost.** Gold must match prep exactly. A LEFT JOIN that
# MAGIC accidentally fans out would show up here as more rows than expected.

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
# MAGIC **Check 3 — counts are internally consistent.** Bushfire plus planned should equal
# MAGIC total, and the narrower windows can never exceed the wider ones. Must return zero.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT COUNT(*) AS inconsistent_rows
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC WHERE times_bushfire_total + times_planned_total > times_burnt_total
# MAGIC    OR times_burnt_since_1980 > times_burnt_total
# MAGIC    OR times_burnt_last_20yr  > times_burnt_since_1980
# MAGIC    OR times_bushfire_last_20yr > times_bushfire_since_1980;

# COMMAND ----------

# MAGIC %md
# MAGIC **Check 4 — LGA attribution coverage.** Some unmatched segments are expected near
# MAGIC borders and coastlines. A large proportion would mean the LGA cell layer has gaps.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   lga_name = 'Outside Victoria or unmatched' AS unmatched,
# MAGIC   COUNT(*) AS segments,
# MAGIC   ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC GROUP BY 1;

# COMMAND ----------

# MAGIC %md
# MAGIC **Check 5 — does the distribution look sane?** No single right answer, but if
# MAGIC every segment lands in one band something is wrong.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   bushfire_exposure_band,
# MAGIC   COUNT(*) AS segments,
# MAGIC   ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC GROUP BY bushfire_exposure_band
# MAGIC ORDER BY segments DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC **Check 6 — the SEASON assumption.** Compare the year in `START_DATE` against
# MAGIC `SEASON`. If they differ consistently by one, the season is labelled by its ending
# MAGIC year and the 1980 and 2006 window boundaries need shifting.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   season,
# MAGIC   YEAR(start_date) AS start_year,
# MAGIC   COUNT(*) AS n
# MAGIC FROM workspace.bushfire.prep_fire
# MAGIC WHERE start_date IS NOT NULL AND season >= 2018
# MAGIC GROUP BY season, YEAR(start_date)
# MAGIC ORDER BY season DESC, n DESC
# MAGIC LIMIT 20;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Sample answers
# MAGIC
# MAGIC The kind of question the app has to handle. If these return sensible results, the
# MAGIC data is ready for Genie.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   lga_name,
# MAGIC   COUNT(*)                          AS segments,
# MAGIC   ROUND(AVG(times_bushfire_since_1980), 2) AS avg_bushfires,
# MAGIC   COUNT_IF(bushfire_exposure_band = 'High') AS high_exposure_segments
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC WHERE voltage_class = 'HV Distribution'
# MAGIC GROUP BY lga_name
# MAGIC ORDER BY avg_bushfires DESC
# MAGIC LIMIT 15;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT
# MAGIC   is_swer,
# MAGIC   COUNT(*)                                 AS segments,
# MAGIC   ROUND(AVG(times_bushfire_since_1980), 3) AS avg_bushfires,
# MAGIC   ROUND(100.0 * COUNT_IF(bushfire_exposure_band IN ('Moderate','High'))
# MAGIC         / COUNT(*), 1)                     AS pct_moderate_or_high
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC GROUP BY is_swer;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done
# MAGIC
# MAGIC `gold_segment_exposure` — one row per line segment, no geometry, every spatial
# MAGIC fact reduced to a number Genie can aggregate.
# MAGIC
# MAGIC Next: notebook 04, the semantic layer. Column comments, metric definitions and
# MAGIC Genie instructions. That is where 20 of the 40 contest points live, and it matters
# MAGIC considerably more than anything in this notebook.
