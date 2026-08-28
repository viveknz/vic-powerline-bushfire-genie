# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 2 — The Semantic Layer
# MAGIC
# MAGIC Notebook 04. Builds the three views Genie reads, with a comment on every column.
# MAGIC
# MAGIC This is the highest-value phase in the project. The contest awards 20 of its 40
# MAGIC points for Genie being genuinely central and answering well, and Genie's answer
# MAGIC quality is mostly a function of what it has been told the data means.
# MAGIC
# MAGIC A column called `times_major_bushfire` means nothing on its own. "Count of
# MAGIC distinct bushfires of 1,000 hectares or more that came within roughly 460 m of
# MAGIC this segment" means everything.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Why views rather than the gold table
# MAGIC
# MAGIC Three reasons.
# MAGIC
# MAGIC **Hide what should not be queried.** `area_ha` is per-polygon and polygons
# MAGIC overlap. The Snowy Complex fire sums to 892,445 ha across 71 polygons when the
# MAGIC actual fire was around 400,000 ha. If Genie can reach that column, someone will
# MAGIC eventually ask for total hectares burnt and get an answer that is wrong by 2x and
# MAGIC looks entirely credible.
# MAGIC
# MAGIC **Hide what is meaningless.** `distributor_code` holds six numeric codes with no
# MAGIC lookup table. Exposing it invites questions nobody can answer.
# MAGIC
# MAGIC **Fewer columns, better answers.** Genie picks between the columns it can see.
# MAGIC Every irrelevant one is a chance to pick wrong.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Three views
# MAGIC
# MAGIC | View | Grain | Answers |
# MAGIC |---|---|---|
# MAGIC | `v_segment_exposure` | One row per line segment | "Which segments are most exposed" |
# MAGIC | `v_fire_history` | One row per fire | "How many bushfires in 2020" |
# MAGIC | `v_segment_fire` | One row per segment-fire pair | "Which segments did Black Saturday affect" |
# MAGIC
# MAGIC The bridge view is what lets Genie answer questions that span both — naming a
# MAGIC specific fire and asking about the network it touched. Without it the two sides
# MAGIC are unconnected and Genie will try to join on fire name, which will not work.

# COMMAND ----------

# MAGIC %md
# MAGIC ## View 1 — Segment exposure
# MAGIC
# MAGIC The primary table. One row per overhead line segment, 159,268 rows.
# MAGIC
# MAGIC Column comments are written for a reader who knows nothing about the pipeline.
# MAGIC Where a number has a caveat, the caveat is in the comment rather than left for
# MAGIC the instructions to catch.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE VIEW workspace.bushfire.v_segment_exposure (
# MAGIC   segment_id COMMENT
# MAGIC     'Unique Vicmap feature identifier (UFI) for this overhead powerline segment. Traceable back to the published Vicmap Infrastructure dataset.',
# MAGIC   voltage COMMENT
# MAGIC     'Operating voltage as published by Vicmap. Values include 22 KV, 12.7 KV, 11 KV, 6.6 KV, 66 KV, 132 KV, 220 KV, 275 KV, 330 KV, 500 KV. 12.7 KV indicates a SWER line.',
# MAGIC   voltage_class COMMENT
# MAGIC     'Network tier: Transmission or HV Distribution. Low voltage (LV) is deliberately excluded from this dataset because it is largely urban, short-span and often underground, so it carries little bushfire relevance.',
# MAGIC   is_swer COMMENT
# MAGIC     'TRUE where voltage is 12.7 KV, indicating Single Wire Earth Return. SWER lines run long spans through remote bushland on minimal infrastructure and are a distinct bushfire risk class. 26,646 segments.',
# MAGIC   lga_name COMMENT
# MAGIC     'Local government area the segment mostly falls within, by shared H3 cell count. Includes 79 Victorian councils plus 6 alpine resorts and 2 island groups. Value "Unmatched" means no LGA overlap was found (113 segments, typically offshore or on a border).',
# MAGIC   lga_type COMMENT
# MAGIC     'Whether the lga_name is a Council, an Alpine resort, or an Island. Alpine resorts and islands are unincorporated areas, not councils. Exclude them when a question asks specifically about councils.',
# MAGIC   times_burnt_total COMMENT
# MAGIC     'Distinct fires of any type that came within roughly 460 m of this segment, across the whole record from 1903. Includes planned burns, which substantially outnumber bushfires. Rarely the right column on its own.',
# MAGIC   times_bushfire_total COMMENT
# MAGIC     'Distinct bushfires (unplanned fires) near this segment across the whole record from 1903. Excludes planned burns.',
# MAGIC   times_planned_total COMMENT
# MAGIC     'Distinct planned burns near this segment across the whole record. Planned burns are DEECA fuel reduction activity, not fire risk events. High counts indicate active fuel management, which reduces risk rather than indicating it.',
# MAGIC   times_burnt_since_1980 COMMENT
# MAGIC     'Distinct fires of any type near this segment since the 1979/80 season.',
# MAGIC   times_bushfire_since_1980 COMMENT
# MAGIC     'Distinct bushfires near this segment since the 1979/80 season. Counts fires of all sizes, so a 5 ha grassfire counts the same as a 300,000 ha campaign fire. Use times_major_bushfire when fire scale matters.',
# MAGIC   times_burnt_last_20yr COMMENT
# MAGIC     'Distinct fires of any type near this segment since the 2005/06 season.',
# MAGIC   times_bushfire_last_20yr COMMENT
# MAGIC     'Distinct bushfires near this segment since the 2005/06 season. The best measure of recent fire activity.',
# MAGIC   times_major_bushfire COMMENT
# MAGIC     'Distinct bushfires of 1,000 hectares or more near this segment. This is the scale-aware exposure measure and it drives bushfire_exposure_band. Prefer this over times_bushfire_since_1980 when ranking or prioritising, because it separates campaign fires from small grassfires.',
# MAGIC   bushfire_exposure_band COMMENT
# MAGIC     'Risk banding derived from times_major_bushfire: None (0 major bushfires), Low (1), Moderate (2 to 3), High (4 or more). 301 segments are High. This is the primary field for prioritisation questions.',
# MAGIC   last_burn_season COMMENT
# MAGIC     'Most recent fire season in which any fire, including planned burns, occurred near this segment. See the season convention note in the instructions.',
# MAGIC   last_bushfire_season COMMENT
# MAGIC     'Most recent fire season in which a bushfire occurred near this segment. NULL where no bushfire is recorded. Season is the ending year of a July to June period, so 2020 means the 2019/20 summer.',
# MAGIC   years_since_last_bushfire COMMENT
# MAGIC     'Years between 2026 and last_bushfire_season. NULL where no bushfire is recorded, which means never burnt rather than burnt long ago.',
# MAGIC   largest_fire_area_ha COMMENT
# MAGIC     'Area in hectares of the single largest fire polygon near this segment. Indicative of scale only. Never sum this column across segments or fires, because fire polygons overlap and the total will roughly double count.',
# MAGIC   largest_fire_name COMMENT
# MAGIC     'Name of the largest fire near this segment. Names suffixed (NSW) are cross-border fires that burned into Victoria.',
# MAGIC   most_recent_fire_name COMMENT
# MAGIC     'Name of the most recent fire near this segment, of any type including planned burns.',
# MAGIC   powerline_caused_fires COMMENT
# MAGIC     'Distinct fires near this segment whose recorded cause was a powerline or power transmission, including one recorded as "Tree on Power Line". Only 8 such fires exist statewide, because cause is investigated and recorded for only about 3 percent of fires. A zero means no recorded powerline cause, not an absence of risk.',
# MAGIC   last_powerline_caused_season COMMENT
# MAGIC     'Most recent season with a powerline-caused fire near this segment. NULL where none recorded.'
# MAGIC )
# MAGIC COMMENT
# MAGIC   'One row per Victorian overhead powerline segment (transmission and HV distribution), with bushfire exposure derived from DEECA fire history 1903 to 2026. Exposure is computed by H3 spatial indexing at resolution 8, so "near" means sharing a hexagonal cell of roughly 460 m across, not exact intersection. Low voltage lines are excluded. Source data: Vicmap Infrastructure and DEECA Fire History Scar, State Government of Victoria, CC BY 4.0.'
# MAGIC AS
# MAGIC SELECT
# MAGIC   segment_id, voltage, voltage_class, is_swer,
# MAGIC   lga_name, lga_type,
# MAGIC   times_burnt_total, times_bushfire_total, times_planned_total,
# MAGIC   times_burnt_since_1980, times_bushfire_since_1980,
# MAGIC   times_burnt_last_20yr, times_bushfire_last_20yr,
# MAGIC   times_major_bushfire, bushfire_exposure_band,
# MAGIC   last_burn_season, last_bushfire_season, years_since_last_bushfire,
# MAGIC   largest_fire_area_ha, largest_fire_name, most_recent_fire_name,
# MAGIC   powerline_caused_fires, last_powerline_caused_season
# MAGIC FROM workspace.bushfire.gold_segment_exposure;

# COMMAND ----------

# MAGIC %md
# MAGIC ## View 2 — Fire history
# MAGIC
# MAGIC One row per fire rather than per polygon. 17,934 rows.
# MAGIC
# MAGIC The source maps a single fire as many polygons — 6.1 on average, and 868 for the
# MAGIC 2019/20 Upper Murray fire. Aggregating to `fire_key` first is what stops every
# MAGIC count in the app being inflated.
# MAGIC
# MAGIC `area_ha` is exposed as a per-fire maximum only, never a sum.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE VIEW workspace.bushfire.v_fire_history (
# MAGIC   fire_key COMMENT
# MAGIC     'Identifier for a single fire event. Derived from the source FIREKEY, falling back to fire number plus season, then to polygon id. One fire is mapped as many polygons in the source, so always count distinct fires rather than rows.',
# MAGIC   fire_name COMMENT
# MAGIC     'Name of the fire, typically a locality or feature. Names suffixed (NSW) are cross-border fires that burned into Victoria from New South Wales.',
# MAGIC   season COMMENT
# MAGIC     'Fire season, expressed as the ending year of a July to June period. Season 2020 covers July 2019 to June 2020, which is the 2019/20 summer. A user asking about "the 2019 fires" almost always means season 2020. Range 1903 to 2026.',
# MAGIC   fire_type COMMENT
# MAGIC     'Bushfire (unplanned) or Burn (planned burn conducted by DEECA for fuel reduction). Planned burns substantially outnumber bushfires. Never combine the two when discussing fire risk.',
# MAGIC   cause_group COMMENT
# MAGIC     'Normalised cause: Powerline, Lightning, Planned burn, Burning off, Deliberate, Campfire, Equipment, Other, Unknown, or "Not recorded". Cause is investigated and recorded for only about 3 percent of fires, so 16,729 of 17,934 fires are "Not recorded". Any answer about causes must state that it covers only the small investigated subset.',
# MAGIC   powerline_caused COMMENT
# MAGIC     'TRUE where the recorded cause was a powerline or power transmission. Only 8 fires statewide. This is a floor, not a total, because most fires have no recorded cause.',
# MAGIC   max_area_ha COMMENT
# MAGIC     'Area in hectares of the largest single polygon mapped for this fire. Indicative of fire scale. This is not the total burnt area, because polygons within a fire overlap. Never sum this column across fires.',
# MAGIC   polygon_count COMMENT
# MAGIC     'Number of separate polygons the source uses to map this fire. High values indicate a large or complex fire mapped in detail, not multiple fires.',
# MAGIC   ffm_region COMMENT
# MAGIC     'DEECA Forest Fire Management region: Loddon Mallee, Barwon South West, Gippsland, Hume, Port Phillip, or Grampians. This is a fire management geography, not a local government one.',
# MAGIC   ffm_district COMMENT
# MAGIC     'DEECA Forest Fire Management district, a finer division within ffm_region.',
# MAGIC   first_start_date COMMENT
# MAGIC     'Earliest recorded start date across this fire polygons. Some historic records have unreliable dates.',
# MAGIC   accuracy COMMENT
# MAGIC     'Positional accuracy of the mapped boundary: High (25 m or less), Medium (26 to 100 m), Low (greater than 100 m), or Unknown. Roughly a quarter of records are Unknown. Useful for qualifying confidence in an answer.'
# MAGIC )
# MAGIC COMMENT
# MAGIC   'One row per fire event in the Victorian fire history record, 1903 to 2026, aggregated from 109,219 source polygons to 17,934 distinct fires. Includes both bushfires and DEECA planned burns. Source: DEECA Fire History Scar, State Government of Victoria, CC BY 4.0.'
# MAGIC AS
# MAGIC SELECT
# MAGIC   fire_key,
# MAGIC   MAX_BY(fire_name, area_ha)          AS fire_name,
# MAGIC   MAX(season)                         AS season,
# MAGIC   MAX_BY(fire_type, area_ha)          AS fire_type,
# MAGIC   MAX_BY(cause_group, area_ha)        AS cause_group,
# MAGIC   MAX(powerline_caused)               AS powerline_caused,
# MAGIC   MAX(area_ha)                        AS max_area_ha,
# MAGIC   COUNT(*)                            AS polygon_count,
# MAGIC   MAX_BY(ffm_region, area_ha)         AS ffm_region,
# MAGIC   MAX_BY(ffm_district, area_ha)       AS ffm_district,
# MAGIC   MIN(start_date)                     AS first_start_date,
# MAGIC   MAX_BY(accuracy, area_ha)           AS accuracy
# MAGIC FROM workspace.bushfire.prep_fire
# MAGIC GROUP BY fire_key;

# COMMAND ----------

# MAGIC %md
# MAGIC ## View 3 — The bridge
# MAGIC
# MAGIC One row per segment-fire pair. This is what lets Genie answer questions that name
# MAGIC a specific fire and ask about the network, or start from a segment and ask which
# MAGIC fires touched it.
# MAGIC
# MAGIC Without it, the other two views are unconnected islands and Genie will attempt to
# MAGIC join on fire name, which will not work.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE VIEW workspace.bushfire.v_segment_fire (
# MAGIC   segment_id COMMENT
# MAGIC     'Powerline segment identifier. Joins to v_segment_exposure.segment_id.',
# MAGIC   fire_key COMMENT
# MAGIC     'Fire identifier. Joins to v_fire_history.fire_key.',
# MAGIC   fire_name COMMENT
# MAGIC     'Name of the fire, repeated here for convenience so simple questions need no join.',
# MAGIC   season COMMENT
# MAGIC     'Fire season, the ending year of a July to June period. Season 2020 is the 2019/20 summer.',
# MAGIC   fire_type COMMENT
# MAGIC     'Bushfire or Burn (planned burn).',
# MAGIC   max_area_ha COMMENT
# MAGIC     'Largest polygon area in hectares for this fire. Indicative of scale. Never sum.',
# MAGIC   voltage COMMENT
# MAGIC     'Operating voltage of the affected segment.',
# MAGIC   voltage_class COMMENT
# MAGIC     'Transmission or HV Distribution.',
# MAGIC   is_swer COMMENT
# MAGIC     'TRUE where the segment is a 12.7 KV SWER line.',
# MAGIC   lga_name COMMENT
# MAGIC     'Local government area of the affected segment.'
# MAGIC )
# MAGIC COMMENT
# MAGIC   'One row for each combination of powerline segment and fire that came within roughly 460 m of it. Use this to answer questions that name a specific fire, or that need fire detail alongside segment detail. Counting rows here counts segment-fire pairs, not segments and not fires: use COUNT(DISTINCT segment_id) or COUNT(DISTINCT fire_key) as appropriate.'
# MAGIC AS
# MAGIC SELECT DISTINCT
# MAGIC   p.segment_id,
# MAGIC   f.fire_key,
# MAGIC   f.fire_name,
# MAGIC   f.season,
# MAGIC   f.fire_type,
# MAGIC   f.max_area_ha,
# MAGIC   s.voltage,
# MAGIC   s.voltage_class,
# MAGIC   s.is_swer,
# MAGIC   s.lga_name
# MAGIC FROM workspace.bushfire.segment_fire_pairs p
# MAGIC JOIN (
# MAGIC   SELECT fire_poly_id, fire_key,
# MAGIC          MAX(area_ha)  OVER (PARTITION BY fire_key) AS max_area_ha,
# MAGIC          FIRST_VALUE(fire_name) OVER (PARTITION BY fire_key ORDER BY area_ha DESC) AS fire_name,
# MAGIC          season, fire_type
# MAGIC   FROM workspace.bushfire.prep_fire
# MAGIC ) f ON p.fire_poly_id = f.fire_poly_id
# MAGIC JOIN workspace.bushfire.gold_segment_exposure s ON p.segment_id = s.segment_id;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify the views
# MAGIC
# MAGIC Row counts first.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT 'v_segment_exposure' AS view, COUNT(*) AS rows FROM workspace.bushfire.v_segment_exposure
# MAGIC UNION ALL SELECT 'v_fire_history', COUNT(*) FROM workspace.bushfire.v_fire_history
# MAGIC UNION ALL SELECT 'v_segment_fire', COUNT(*) FROM workspace.bushfire.v_segment_fire;

# COMMAND ----------

# MAGIC %md
# MAGIC Then confirm the comments actually landed. Genie reads these, so an empty comment
# MAGIC column here means the semantic layer is not doing its job.

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE TABLE EXTENDED workspace.bushfire.v_segment_exposure;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Smoke tests
# MAGIC
# MAGIC Three questions that exercise each view, to confirm the data behaves before Genie
# MAGIC sees it.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- The SWER finding, which is the headline result of the project
# MAGIC SELECT
# MAGIC   CASE WHEN is_swer THEN 'SWER (12.7 KV)' ELSE 'Other HV and Transmission' END AS line_type,
# MAGIC   COUNT(*) AS segments,
# MAGIC   ROUND(AVG(times_bushfire_since_1980), 3) AS avg_bushfires,
# MAGIC   ROUND(100.0 * COUNT_IF(bushfire_exposure_band IN ('Moderate','High')) / COUNT(*), 1) AS pct_moderate_or_high
# MAGIC FROM workspace.bushfire.v_segment_exposure
# MAGIC GROUP BY is_swer;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Fire-level: the largest bushfires on record
# MAGIC SELECT fire_name, season, ROUND(max_area_ha) AS max_area_ha, polygon_count, ffm_region
# MAGIC FROM workspace.bushfire.v_fire_history
# MAGIC WHERE fire_type = 'Bushfire'
# MAGIC ORDER BY max_area_ha DESC
# MAGIC LIMIT 10;

# COMMAND ----------

# MAGIC %sql
# MAGIC -- The bridge: which network did the 2019/20 season touch
# MAGIC SELECT
# MAGIC   fire_name,
# MAGIC   COUNT(DISTINCT segment_id) AS segments_affected,
# MAGIC   COUNT(DISTINCT lga_name)   AS lgas_affected,
# MAGIC   COUNT(DISTINCT CASE WHEN is_swer THEN segment_id END) AS swer_segments
# MAGIC FROM workspace.bushfire.v_segment_fire
# MAGIC WHERE season = 2020 AND fire_type = 'Bushfire'
# MAGIC GROUP BY fire_name
# MAGIC ORDER BY segments_affected DESC
# MAGIC LIMIT 10;

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC
# MAGIC The views exist and are documented. Two things remain in Phase 2, and both happen
# MAGIC in the Genie Agent UI rather than here:
# MAGIC
# MAGIC 1. **General instructions** — the domain rules Genie applies to every question.
# MAGIC 2. **Trusted SQL examples** — question and query pairs it learns patterns from.
# MAGIC
# MAGIC Both are kept in the repository under `genie/` so they are versioned rather than
# MAGIC living only in a web form. See `genie/instructions.md` and
# MAGIC `genie/sql_examples.sql`.
