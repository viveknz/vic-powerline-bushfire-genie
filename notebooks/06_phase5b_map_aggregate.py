# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 5b — Map Aggregate
# MAGIC
# MAGIC Notebook 06. Builds a small pre-aggregated table so the app can draw Victoria
# MAGIC without shipping 290,760 hexagons to a browser.
# MAGIC
# MAGIC No new spatial work. H3 cells are hierarchical, so rolling resolution 8 up to
# MAGIC resolution 5 is one function call.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Why resolution 5
# MAGIC
# MAGIC | Resolution | Cell width | Cells statewide | Browser |
# MAGIC |---|---|---|---|
# MAGIC | 8 | ~460 m | 290,760 | far too many |
# MAGIC | 6 | ~3.2 km | ~15,000 | slow |
# MAGIC | 5 | ~8.5 km | ~1,000 | instant |
# MAGIC
# MAGIC At resolution 5 the whole state fits comfortably and the pattern is still legible.
# MAGIC `h3_toparent` walks the hierarchy upward and `h3_h3tostring` converts the bigint
# MAGIC cell id into the hex string Deck.gl expects.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.gold_map_hex AS
# MAGIC SELECT
# MAGIC   h3_h3tostring(h3_toparent(cs.h3_cell, 5))          AS hex_id,
# MAGIC   COUNT(DISTINCT cs.segment_id)                      AS segments,
# MAGIC   COUNT(DISTINCT CASE WHEN g.is_swer THEN cs.segment_id END) AS swer_segments,
# MAGIC   ROUND(AVG(g.times_major_bushfire), 3)              AS avg_major_bushfires,
# MAGIC   ROUND(AVG(g.pct_extent_major_bushfire), 1)         AS avg_pct_extent_burnt,
# MAGIC   COUNT(DISTINCT CASE WHEN g.bushfire_exposure_band = 'High'
# MAGIC                       THEN cs.segment_id END)        AS high_exposure_segments,
# MAGIC   MAX(g.lga_name)                                    AS example_lga
# MAGIC FROM workspace.bushfire.cells_segment cs
# MAGIC JOIN workspace.bushfire.gold_segment_exposure g
# MAGIC   ON cs.segment_id = g.segment_id
# MAGIC GROUP BY 1;

# COMMAND ----------

# MAGIC %md
# MAGIC ## A finer table for result maps
# MAGIC
# MAGIC When Genie returns specific segments, the app zooms to them. Resolution 7
# MAGIC (~1.2 km) is right for that: detailed enough to see the route, small enough that
# MAGIC a few hundred segments render instantly.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.bushfire.gold_segment_hex AS
# MAGIC SELECT DISTINCT
# MAGIC   cs.segment_id,
# MAGIC   h3_h3tostring(h3_toparent(cs.h3_cell, 7)) AS hex_id
# MAGIC FROM workspace.bushfire.cells_segment cs;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT 'gold_map_hex' AS t, COUNT(*) AS rows FROM workspace.bushfire.gold_map_hex
# MAGIC UNION ALL
# MAGIC SELECT 'gold_segment_hex', COUNT(*) FROM workspace.bushfire.gold_segment_hex;

# COMMAND ----------

# MAGIC %md
# MAGIC Sanity check. The hottest cells should be East Gippsland, the Alps and the
# MAGIC Otways, not metropolitan Melbourne.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT hex_id, example_lga, segments, avg_pct_extent_burnt, high_exposure_segments
# MAGIC FROM workspace.bushfire.gold_map_hex
# MAGIC WHERE segments >= 20
# MAGIC ORDER BY avg_pct_extent_burnt DESC
# MAGIC LIMIT 15;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Grants
# MAGIC
# MAGIC The app service principal needs to read both new tables. Replace the id if yours
# MAGIC differs — it is the App ID on the app overview page.

# COMMAND ----------

# MAGIC %sql
# MAGIC GRANT SELECT ON TABLE workspace.bushfire.gold_map_hex
# MAGIC   TO `49b204c8-8e62-4620-84b6-69954f0ddeee`;

# COMMAND ----------

# MAGIC %sql
# MAGIC GRANT SELECT ON TABLE workspace.bushfire.gold_segment_hex
# MAGIC   TO `49b204c8-8e62-4620-84b6-69954f0ddeee`;
