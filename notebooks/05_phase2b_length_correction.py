# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 2b — Correcting for Segment Length
# MAGIC
# MAGIC Notebook 05. Adds an extent measure and a density measure to the gold table, then
# MAGIC rebuilds the Genie-facing view.
# MAGIC
# MAGIC **Run this after notebook 03 and before further Genie testing.**

# COMMAND ----------

# MAGIC %md
# MAGIC ## The problem
# MAGIC
# MAGIC Genie was asked whether planned burns reduce risk, and cited a segment in East
# MAGIC Gippsland with 111 planned burns and 11 major bushfires. That figure is not
# MAGIC plausible for one location, and checking it exposed a flaw in the metrics.
# MAGIC
# MAGIC A Vicmap line segment is a single feature of arbitrary length. Segment 75427440 is
# MAGIC a 330 kV line spanning 255 H3 cells, roughly 100 km. It intersects every fire along
# MAGIC that entire distance. A 200 m HV span nearby can only ever touch a handful.
# MAGIC
# MAGIC So every count column is confounded by length:
# MAGIC
# MAGIC | segment_id | h3_cells | times_burnt_total | voltage |
# MAGIC |---|---|---|---|
# MAGIC | 75427440 | 255 | 96 | 330 KV |
# MAGIC | 75424928 | 255 | 98 | 330 KV |
# MAGIC | 75448756 | 196 | 12 | 220 KV |
# MAGIC | 75365218 | 178 | 5 | 220 KV |
# MAGIC
# MAGIC The confound is real but not total. Yarriambiack has 196 cells and 12 fires;
# MAGIC Colac Otway 178 cells and 5 fires. Location still matters. So the fix is to expose
# MAGIC both a total and a rate, not to replace one with the other.

# COMMAND ----------

# MAGIC %md
# MAGIC ## The measure worth adding
# MAGIC
# MAGIC Rather than dividing counts by length, compute directly what proportion of a
# MAGIC segment's extent has been in major fire ground.
# MAGIC
# MAGIC Of this segment's N H3 cells, how many have ever been covered by a bushfire of
# MAGIC 1,000 hectares or more? That gives `pct_extent_major_bushfire`, which is
# MAGIC comparable across segments of any length and means something physical: the share
# MAGIC of this line that runs through country which has burnt badly.
# MAGIC
# MAGIC A 100 km transmission line crossing one fire scar scores low. A 500 m span sitting
# MAGIC entirely inside repeated fire ground scores 100%. That is the correct ordering for
# MAGIC a vegetation manager, and the opposite of what raw counts give.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — Segment extent

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.segment_extent AS
# MAGIC SELECT segment_id, COUNT(*) AS h3_cell_count
# MAGIC FROM workspace.bushfire.cells_segment
# MAGIC GROUP BY segment_id;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Sanity check on the spread. Expect a long tail: most segments small, a few huge.
# MAGIC SELECT
# MAGIC   MIN(h3_cell_count)  AS min_cells,
# MAGIC   PERCENTILE(h3_cell_count, 0.5)  AS median_cells,
# MAGIC   PERCENTILE(h3_cell_count, 0.95) AS p95_cells,
# MAGIC   MAX(h3_cell_count)  AS max_cells,
# MAGIC   ROUND(AVG(h3_cell_count), 2) AS mean_cells
# MAGIC FROM workspace.bushfire.segment_extent;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — How much of each segment sits in major fire ground
# MAGIC
# MAGIC Cells belonging to major bushfires (1,000 ha or more), joined against segment
# MAGIC cells. `COUNT(DISTINCT h3_cell)` because several fires may cover the same cell and
# MAGIC we are measuring extent, not frequency.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.segment_burnt_extent AS
# MAGIC WITH major_fire_cells AS (
# MAGIC   SELECT DISTINCT cf.h3_cell
# MAGIC   FROM workspace.bushfire.cells_fire cf
# MAGIC   JOIN workspace.bushfire.prep_fire f ON cf.fire_poly_id = f.fire_poly_id
# MAGIC   WHERE f.fire_type = 'Bushfire' AND f.area_ha >= 1000
# MAGIC ),
# MAGIC any_bushfire_cells AS (
# MAGIC   SELECT DISTINCT cf.h3_cell
# MAGIC   FROM workspace.bushfire.cells_fire cf
# MAGIC   JOIN workspace.bushfire.prep_fire f ON cf.fire_poly_id = f.fire_poly_id
# MAGIC   WHERE f.fire_type = 'Bushfire'
# MAGIC )
# MAGIC SELECT
# MAGIC   cs.segment_id,
# MAGIC   COUNT(DISTINCT CASE WHEN m.h3_cell IS NOT NULL THEN cs.h3_cell END) AS cells_in_major_bushfire,
# MAGIC   COUNT(DISTINCT CASE WHEN a.h3_cell IS NOT NULL THEN cs.h3_cell END) AS cells_in_any_bushfire
# MAGIC FROM workspace.bushfire.cells_segment cs
# MAGIC LEFT JOIN major_fire_cells   m ON cs.h3_cell = m.h3_cell
# MAGIC LEFT JOIN any_bushfire_cells a ON cs.h3_cell = a.h3_cell
# MAGIC GROUP BY cs.segment_id;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — Rebuild the gold table
# MAGIC
# MAGIC Same as before plus four columns: extent, extent class, and the two percentages.
# MAGIC
# MAGIC The exposure band stays keyed to `times_major_bushfire` so existing answers do not
# MAGIC shift. The density measure is offered alongside, and the instructions tell Genie
# MAGIC when to prefer it.

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
# MAGIC   COALESCE(e.h3_cell_count, 0) AS h3_cell_count,
# MAGIC   CASE
# MAGIC     WHEN COALESCE(e.h3_cell_count, 0) <= 2  THEN 'Short'
# MAGIC     WHEN e.h3_cell_count <= 10              THEN 'Medium'
# MAGIC     WHEN e.h3_cell_count <= 50              THEN 'Long'
# MAGIC     ELSE 'Very long'
# MAGIC   END AS extent_class,
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
# MAGIC   ROUND(100.0 * COALESCE(be.cells_in_major_bushfire, 0)
# MAGIC         / NULLIF(e.h3_cell_count, 0), 1) AS pct_extent_major_bushfire,
# MAGIC   ROUND(100.0 * COALESCE(be.cells_in_any_bushfire, 0)
# MAGIC         / NULLIF(e.h3_cell_count, 0), 1) AS pct_extent_any_bushfire,
# MAGIC
# MAGIC   CASE WHEN COALESCE(fs.times_major_bushfire, 0) = 0 THEN 'None'
# MAGIC        WHEN fs.times_major_bushfire = 1              THEN 'Low'
# MAGIC        WHEN fs.times_major_bushfire <= 3             THEN 'Moderate'
# MAGIC        ELSE 'High'
# MAGIC   END AS bushfire_exposure_band,
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
# MAGIC   fs.last_powerline_caused_season
# MAGIC
# MAGIC FROM workspace.bushfire.prep_segment s
# MAGIC LEFT JOIN workspace.bushfire.segment_lga l          ON s.segment_id = l.segment_id
# MAGIC LEFT JOIN workspace.bushfire.segment_fire_stats fs   ON s.segment_id = fs.segment_id
# MAGIC LEFT JOIN workspace.bushfire.segment_extent e        ON s.segment_id = e.segment_id
# MAGIC LEFT JOIN workspace.bushfire.segment_burnt_extent be ON s.segment_id = be.segment_id;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 — Validate

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Row count must still match prep_segment
# MAGIC SELECT
# MAGIC   (SELECT COUNT(*) FROM workspace.bushfire.prep_segment) AS prep_rows,
# MAGIC   (SELECT COUNT(*) FROM workspace.bushfire.gold_segment_exposure) AS gold_rows;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Percentages must sit between 0 and 100, and major cannot exceed any
# MAGIC SELECT COUNT(*) AS invalid_rows
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC WHERE pct_extent_major_bushfire > 100
# MAGIC    OR pct_extent_any_bushfire > 100
# MAGIC    OR pct_extent_major_bushfire > pct_extent_any_bushfire;

# COMMAND ----------

# MAGIC %md
# MAGIC ### The point of the whole exercise
# MAGIC
# MAGIC The same segments, ranked both ways. The transmission lines that dominated the
# MAGIC count-based ranking should fall away once length is accounted for.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Ranked by raw count: long transmission lines win
# MAGIC SELECT segment_id, voltage, h3_cell_count, times_major_bushfire,
# MAGIC        pct_extent_major_bushfire, lga_name
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC ORDER BY times_major_bushfire DESC
# MAGIC LIMIT 10;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Ranked by extent share: segments genuinely sitting in fire ground
# MAGIC SELECT segment_id, voltage, h3_cell_count, times_major_bushfire,
# MAGIC        pct_extent_major_bushfire, lga_name
# MAGIC FROM workspace.bushfire.gold_segment_exposure
# MAGIC WHERE h3_cell_count >= 5          -- exclude very short segments where the pct is unstable
# MAGIC ORDER BY pct_extent_major_bushfire DESC, times_major_bushfire DESC
# MAGIC LIMIT 10;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5 — Rebuild the Genie view
# MAGIC
# MAGIC Adds the four new columns with comments that tell Genie which measure to use when.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE VIEW workspace.bushfire.v_segment_exposure (
# MAGIC   segment_id COMMENT
# MAGIC     'Unique Vicmap feature identifier (UFI) for this overhead powerline segment.',
# MAGIC   voltage COMMENT
# MAGIC     'Operating voltage as published by Vicmap. 22 KV, 12.7 KV, 11 KV, 6.6 KV, 66 KV, 132 KV, 220 KV, 275 KV, 330 KV, 500 KV. 12.7 KV indicates a SWER line.',
# MAGIC   voltage_class COMMENT
# MAGIC     'Network tier: Transmission or HV Distribution. Low voltage is excluded from this dataset because it is largely urban, short-span and often underground.',
# MAGIC   is_swer COMMENT
# MAGIC     'TRUE where voltage is 12.7 KV, indicating Single Wire Earth Return. Long spans through remote bushland on minimal infrastructure, and a distinct risk class. 26,646 segments.',
# MAGIC   lga_name COMMENT
# MAGIC     'Local government area the segment mostly falls within. 79 Victorian councils plus 6 alpine resorts and 2 island groups. "Unmatched" means no overlap found (113 segments).',
# MAGIC   lga_type COMMENT
# MAGIC     'Council, Alpine resort, or Island. Alpine resorts and islands are unincorporated areas. Filter to Council when a question is specifically about councils.',
# MAGIC   h3_cell_count COMMENT
# MAGIC     'Number of H3 cells at resolution 8 that this segment passes through. A proxy for segment extent: one cell is roughly 460 m across, so a segment spanning 255 cells runs about 100 km. Vicmap segments vary enormously in length, so this is essential context for interpreting any count.',
# MAGIC   extent_class COMMENT
# MAGIC     'Segment length band derived from h3_cell_count: Short (2 cells or fewer), Medium (3 to 10), Long (11 to 50), Very long (over 50). Very long segments are almost all transmission lines.',
# MAGIC   times_burnt_total COMMENT
# MAGIC     'Distinct fires of any type near this segment since 1903, including planned burns. Strongly influenced by segment length: a long transmission line intersects everything along its route. Rarely the right column on its own.',
# MAGIC   times_bushfire_total COMMENT
# MAGIC     'Distinct bushfires near this segment since 1903. Excludes planned burns. Influenced by segment length.',
# MAGIC   times_planned_total COMMENT
# MAGIC     'Distinct planned burns near this segment since 1903. DEECA fuel reduction activity, not risk events. Heavily influenced by segment length: the highest values belong to 330 kV lines running 100 km, not to unusually burnt locations.',
# MAGIC   times_burnt_since_1980 COMMENT
# MAGIC     'Distinct fires of any type near this segment since the 1979/80 season.',
# MAGIC   times_bushfire_since_1980 COMMENT
# MAGIC     'Distinct bushfires near this segment since the 1979/80 season. Counts fires of all sizes, so a 5 ha grassfire counts the same as a 300,000 ha campaign fire.',
# MAGIC   times_burnt_last_20yr COMMENT
# MAGIC     'Distinct fires of any type near this segment since the 2005/06 season.',
# MAGIC   times_bushfire_last_20yr COMMENT
# MAGIC     'Distinct bushfires near this segment since the 2005/06 season. The best measure of recent fire activity.',
# MAGIC   times_major_bushfire COMMENT
# MAGIC     'Distinct bushfires of 1,000 hectares or more near this segment. Drives bushfire_exposure_band. Still influenced by segment length, so pair it with pct_extent_major_bushfire when comparing individual segments.',
# MAGIC   pct_extent_major_bushfire COMMENT
# MAGIC     'Percentage of this segment length that has been covered by a major bushfire (1,000 ha or more) at some point in the record. This is the length-normalised exposure measure and it is comparable across segments of any size. Prefer it over raw counts when comparing individual segments or when a question asks how exposed something is. Unstable for very short segments, so filter to h3_cell_count >= 5 when ranking.',
# MAGIC   pct_extent_any_bushfire COMMENT
# MAGIC     'Percentage of this segment length covered by any bushfire, including small ones. Always greater than or equal to pct_extent_major_bushfire.',
# MAGIC   bushfire_exposure_band COMMENT
# MAGIC     'Risk banding from times_major_bushfire: None (0), Low (1), Moderate (2 to 3), High (4 or more). 301 segments are High. Good for filtering, but because it derives from a count it favours long segments. Use pct_extent_major_bushfire to rank within a band.',
# MAGIC   last_burn_season COMMENT
# MAGIC     'Most recent season with any fire, including planned burns, near this segment.',
# MAGIC   last_bushfire_season COMMENT
# MAGIC     'Most recent season with a bushfire near this segment. NULL where none recorded. Season is the ending year of a July to June period, so 2020 is the 2019/20 summer.',
# MAGIC   years_since_last_bushfire COMMENT
# MAGIC     'Years between 2026 and last_bushfire_season. NULL means never burnt, not burnt long ago.',
# MAGIC   largest_fire_area_ha COMMENT
# MAGIC     'Area in hectares of the largest single fire polygon near this segment. Indicative of scale only. Never sum this column, because fire polygons overlap and totals roughly double count.',
# MAGIC   largest_fire_name COMMENT
# MAGIC     'Name of the largest fire near this segment. Names suffixed (NSW) are cross-border fires that burned into Victoria.',
# MAGIC   most_recent_fire_name COMMENT
# MAGIC     'Name of the most recent fire near this segment, of any type.',
# MAGIC   powerline_caused_fires COMMENT
# MAGIC     'Distinct fires near this segment with a recorded powerline cause. Only 8 statewide, because cause is investigated for about 3 percent of fires. Zero means no recorded cause, not absence of risk.',
# MAGIC   last_powerline_caused_season COMMENT
# MAGIC     'Most recent season with a powerline-caused fire near this segment.'
# MAGIC )
# MAGIC COMMENT
# MAGIC   'One row per Victorian overhead powerline segment (transmission and HV distribution), with bushfire exposure from DEECA fire history 1903 to 2026. Exposure is computed by H3 indexing at resolution 8, so "near" means within roughly 460 m, not exact intersection. Segments vary greatly in length, so counts favour long transmission lines: use pct_extent_major_bushfire for fair comparison between segments. Low voltage excluded. Source: Vicmap Infrastructure and DEECA Fire History Scar, State Government of Victoria, CC BY 4.0.'
# MAGIC AS
# MAGIC SELECT
# MAGIC   segment_id, voltage, voltage_class, is_swer,
# MAGIC   lga_name, lga_type,
# MAGIC   h3_cell_count, extent_class,
# MAGIC   times_burnt_total, times_bushfire_total, times_planned_total,
# MAGIC   times_burnt_since_1980, times_bushfire_since_1980,
# MAGIC   times_burnt_last_20yr, times_bushfire_last_20yr,
# MAGIC   times_major_bushfire,
# MAGIC   pct_extent_major_bushfire, pct_extent_any_bushfire,
# MAGIC   bushfire_exposure_band,
# MAGIC   last_burn_season, last_bushfire_season, years_since_last_bushfire,
# MAGIC   largest_fire_area_ha, largest_fire_name, most_recent_fire_name,
# MAGIC   powerline_caused_fires, last_powerline_caused_season
# MAGIC FROM workspace.bushfire.gold_segment_exposure;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Then update the Genie instructions
# MAGIC
# MAGIC Add this as a new numbered rule. The column comments alone will not reliably stop
# MAGIC Genie ranking by raw counts, because counts are the obvious thing to reach for.
# MAGIC
# MAGIC > **Segments vary enormously in length.** A Vicmap segment may be 200 m or 100 km.
# MAGIC > `h3_cell_count` is the extent proxy: one cell is about 460 m. Raw counts such as
# MAGIC > `times_major_bushfire` and `times_planned_total` are therefore biased towards
# MAGIC > long transmission lines, and the highest values in the dataset belong to 330 kV
# MAGIC > lines running across the state rather than to unusually fire-prone locations.
# MAGIC >
# MAGIC > When comparing individual segments, or answering how exposed something is, use
# MAGIC > `pct_extent_major_bushfire` and filter to `h3_cell_count >= 5`. When reporting a
# MAGIC > raw count for a single segment, state its `h3_cell_count` or `extent_class`
# MAGIC > alongside it so the figure can be read in context. Never cite a high count for
# MAGIC > one segment as evidence about a location without noting its length.
