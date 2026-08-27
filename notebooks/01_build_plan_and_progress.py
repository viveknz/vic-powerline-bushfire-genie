# Databricks notebook source
# MAGIC %md
# MAGIC # Build Plan and Progress
# MAGIC
# MAGIC Notebook 01. The running record of what is done, what is next, and what was
# MAGIC decided along the way. Update the status table as you go.
# MAGIC
# MAGIC Last updated: 27 August 2026

# COMMAND ----------

# MAGIC %md
# MAGIC ## Status
# MAGIC
# MAGIC | Phase | What it covers | Status |
# MAGIC |---|---|---|
# MAGIC | 0 | Verify Free Edition capabilities | Complete |
# MAGIC | 1a | Shapefiles into bronze Delta | Complete |
# MAGIC | 1b | H3 indexing and exposure join | Next |
# MAGIC | 2 | Semantic layer and Genie Agent config | Not started |
# MAGIC | 3 | Genie question bank and testing | Not started |
# MAGIC | 4 | App backend, Genie Conversations API | Not started |
# MAGIC | 5 | App UI | Not started |
# MAGIC | 6 | Deploy | Not started |
# MAGIC | 7 | Video, article, form | Not started |
# MAGIC
# MAGIC Phases 2 and 3 carry 20 of the 40 available points. Budget accordingly — they
# MAGIC matter more than the app looking good.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Phase 0 — Recon (complete)
# MAGIC
# MAGIC The point was to find out what Free Edition allows before building anything on
# MAGIC assumptions. Everything passed, so no fallback path was forced.
# MAGIC
# MAGIC | Check | Result |
# MAGIC |---|---|
# MAGIC | Unity Catalog, table creation | Pass |
# MAGIC | Serverless SQL warehouse | Pass |
# MAGIC | `h3_longlatash3`, `h3_coverash3`, `h3_polyfillash3` | Pass |
# MAGIC | ST functions (`st_geomfromtext`, `st_astext`) | Pass |
# MAGIC | Python library installs on serverless | Pass |
# MAGIC | Genie — appears in nav as **Genie Agents** | Pass |
# MAGIC | Databricks Apps — via workspace switcher, not left nav | Pass |
# MAGIC | Genie Agent attachable as an app resource | Pass |
# MAGIC
# MAGIC Two notes worth keeping:
# MAGIC
# MAGIC - Apps is a separate workspace context in the top-right switcher, alongside
# MAGIC   Lakehouse and Genie One. It is not in the left navigation, which is confusing
# MAGIC   the first time.
# MAGIC - `st_geomfromtext` returns `GEOMETRY(0)` when no SRID is supplied. Pass 4326
# MAGIC   explicitly if you ever compute distance or area, or the units will be nonsense.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Phase 1a — Ingestion (complete)
# MAGIC
# MAGIC Three shapefiles read with GeoPandas, geometry stored as WKB, written to bronze
# MAGIC Delta tables. Full code is in notebook 02.
# MAGIC
# MAGIC | Table | Rows | CRS | Geometry |
# MAGIC |---|---|---|---|
# MAGIC | `bronze_lga` | 137 | EPSG:7844 | Polygon |
# MAGIC | `bronze_power_line` | 396,455 | EPSG:7844 | LineString |
# MAGIC | `bronze_fire_scar` | 109,219 | EPSG:7844 | Polygon |
# MAGIC
# MAGIC No nulls in geometry on any layer. One CRS across all three, so no datum
# MAGIC reconciliation needed.
# MAGIC
# MAGIC GeoPandas is only needed to read the shapefiles. Once geometry is a WKB column,
# MAGIC everything downstream is SQL.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Data quality findings
# MAGIC
# MAGIC All of these were found by profiling before writing pipeline code. Each one is a
# MAGIC wrong answer avoided. They form the specification for Phase 2.

# COMMAND ----------

# MAGIC %md
# MAGIC ### LGA layer
# MAGIC
# MAGIC **1. Cross-border councils.** 44 of 137 rows are not Victorian — 25 NSW, 19 SA.
# MAGIC These are councils that touch Victoria along the Murray and the SA border. They
# MAGIC have null `LGA_CODE`, `OFFICIALNM` and `ABSLGACODE`.
# MAGIC
# MAGIC *Risk if ignored:* a Victorian line segment near the border could be attributed to
# MAGIC Albury or Renmark. Genie would report it confidently and it would be wrong.
# MAGIC *Fix:* filter `STATE = 'VIC'` in the curated view.
# MAGIC
# MAGIC **2. Multi-polygon councils.** 93 VIC rows but only 87 unique names. Bass Coast
# MAGIC and French-Elizabeth-Sandstone Islands have 3 polygons each, Murrindindi and
# MAGIC Queenscliffe have 2. Island and coastal fragmentation.
# MAGIC
# MAGIC *Risk if ignored:* Bass Coast triple-counts in any grouping.
# MAGIC *Fix:* aggregate at H3 cell level rather than joining polygon to polygon, which
# MAGIC sidesteps the problem entirely.
# MAGIC
# MAGIC **3. Non-council entities.** 87 names = 79 councils + 6 alpine resorts (Falls
# MAGIC Creek, Lake Mountain, Mount Baw Baw, Mount Buller, Mount Hotham, Mount Stirling)
# MAGIC + 2 island groups (Gabo Island, French-Elizabeth-Sandstone Islands).
# MAGIC
# MAGIC *Decision:* keep them. Alpine resorts sit in high fire risk country and have
# MAGIC network infrastructure — they are among the most interesting rows in the dataset.
# MAGIC Add an `lga_type` flag so Genie can include or exclude them on request.
# MAGIC
# MAGIC **4. Lineage columns.** 10 of 16 columns are Vicmap internals — `UFI`, `PFI`,
# MAGIC `NFEAT_ID`, `FQID`, `TASK_ID`, `SUPER_PFI`, `CRDATE_PFI`, `CRDATE_UFI`,
# MAGIC `LABEL_USE_`. Meaningless to a user.
# MAGIC
# MAGIC *Fix:* strip from the curated view. Genie accuracy drops when it must choose
# MAGIC between sixteen columns where five carry meaning.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Power line layer
# MAGIC
# MAGIC **5. LV dominates.** `FEATSUBTYP` splits as:
# MAGIC
# MAGIC | Value | Rows |
# MAGIC |---|---|
# MAGIC | power distribution lv | 237,187 |
# MAGIC | power distribution hv | 158,853 |
# MAGIC | power transmission | 415 |
# MAGIC
# MAGIC LV is 60% of the layer and is largely urban, short-span, often underground.
# MAGIC Bushfire risk lives in HV and SWER through forested country.
# MAGIC
# MAGIC *Decision:* keep LV in bronze, filter it out of the curated view.
# MAGIC
# MAGIC **6. Voltage detail is good.** `VOLTAGE` has 13 distinct values:
# MAGIC
# MAGIC | Voltage | Rows | Note |
# MAGIC |---|---|---|
# MAGIC | LV | 237,187 | |
# MAGIC | 22 KV | 121,068 | Standard HV distribution |
# MAGIC | 12.7 KV | 26,646 | **SWER** — single wire earth return |
# MAGIC | 11 KV | 8,610 | |
# MAGIC | 6.6 KV | 1,491 | Legacy |
# MAGIC | 66 KV | 1,005 | Sub-transmission |
# MAGIC | 220 / 500 / 330 / 275 / 132 / 33 / 19.1 KV | < 400 each | |
# MAGIC
# MAGIC SWER at 12.7 kV is worth calling out separately in the semantic layer. Long spans
# MAGIC through remote bushland on minimal infrastructure — a distinctive risk class that a
# MAGIC judge with utility background will recognise.
# MAGIC
# MAGIC **7. Distributor codes.** `AUTH_ORG_C` has 6 numeric codes and no accompanying
# MAGIC names. Needs a lookup, or exclude it.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Fire history layer
# MAGIC
# MAGIC **8. Planned burns dominate, and must be separated.** `FIRETYPE` distinguishes
# MAGIC `Burn` (planned) from `Bushfire`. Planned burns are the large majority.
# MAGIC
# MAGIC *Risk if ignored:* a single `times_burnt` metric would mostly measure DEECA's fuel
# MAGIC reduction program, not bushfire risk. This is the most consequential finding in
# MAGIC the list — conflating them is a genuine analytical error, not a cosmetic one.
# MAGIC *Fix:* produce `times_bushfire` and `times_planned_burn` as separate columns.
# MAGIC
# MAGIC **9. CAUSE is 97% null.** 106,083 of 109,219 rows have no cause recorded. Cause is
# MAGIC only captured for investigated fires.
# MAGIC
# MAGIC *Fix:* state this in the Genie instructions, so it qualifies rather than
# MAGIC generalising from a 3% sample.
# MAGIC
# MAGIC **10. CAUSE categories are dirty.** The same category appears under multiple
# MAGIC labels — hyphenated, comma-separated, and in one case all caps:
# MAGIC
# MAGIC - `Planned Burn` / `Planned Burning` / `Burning Off (Departmental Prescribed)`
# MAGIC - `Deliberate Lighting (Malicious)` / `Deliberate-Lighting-Malicious`
# MAGIC - `Campfire, Barbeque` / `Campfire-Barbeque`
# MAGIC - `Burning Off, Stubble, Grass, Scrub` / `Burning-Off-Stubble-Grass-Scrub` /
# MAGIC   `BURNING OFF, STUBBLE, GRASS, SCRUB`
# MAGIC
# MAGIC *Fix:* a normalised `cause_group` column with roughly eight clean categories.
# MAGIC
# MAGIC **11. Powerline-caused fires exist and matter.** Across four label variants:
# MAGIC
# MAGIC | Label | Count |
# MAGIC |---|---|
# MAGIC | Power Lines | 17 |
# MAGIC | Power Transmission | 5 |
# MAGIC | Power-Transmission | 5 |
# MAGIC | Tree on Power Line | 1 |
# MAGIC | **Total** | **28** |
# MAGIC
# MAGIC Small, but it closes the loop between the two datasets — lines are both exposed to
# MAGIC fire and a cause of it. `Tree on Power Line` is a vegetation clearance failure,
# MAGIC which is exactly what line clearance regulation exists to prevent.
# MAGIC
# MAGIC *Fix:* a boolean `powerline_caused` flag.
# MAGIC
# MAGIC **12. `CFA_FRV_DI` mixes formats.** `'5'` and `'05'`, `'6'` and `'06'`, plus
# MAGIC `'Goulburn'` sitting among numeric codes. Normalise or exclude.
# MAGIC
# MAGIC **13. Useful columns to keep.** `SEASON` (1903–2026), `START_DATE`, `FFM_DISTRICT`,
# MAGIC `FFM_REGION`, `TREAT_TYPE`, `COVER`, `ACCURACY`.
# MAGIC
# MAGIC `ACCURACY` is worth exposing to Genie so it can qualify answers by confidence:
# MAGIC High (25 m or less), Medium (26–100 m), Low (above 100 m), Unknown.

# COMMAND ----------

# MAGIC %md
# MAGIC ## A methodology note
# MAGIC
# MAGIC A sample read with `max_features=5000` returned proportions that were badly
# MAGIC misleading — Loddon Mallee appeared to hold 75% of Victoria's fire history.
# MAGIC
# MAGIC Shapefiles are spatially ordered. Taking the first N features gives you one corner
# MAGIC of the state, not a random sample.
# MAGIC
# MAGIC Sampling is fine for discovering schema. It is not fine for distributions. Every
# MAGIC proportion in this notebook comes from the full load.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Phase 1b — next
# MAGIC
# MAGIC 1. Index each bronze layer to H3 cells at resolution 8 using `h3_coverash3`
# MAGIC 2. Explode to one row per cell, producing three cell-keyed tables
# MAGIC 3. Join power line cells to fire scar cells to get segment-level fire exposure
# MAGIC 4. Join power line cells to LGA cells to attribute each segment to a council
# MAGIC 5. Aggregate to one row per line segment with the exposure metrics
# MAGIC 6. Validate — no segment lost, no segment in two LGAs, counts reconcile
# MAGIC
# MAGIC Output is a single flat table with no geometry column. That table is what Genie
# MAGIC reads in Phase 2.
# MAGIC
# MAGIC Estimated 3 hours.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Open questions
# MAGIC
# MAGIC - `AUTH_ORG_C` codes — find a lookup or drop the column?
# MAGIC - Should `times_burnt` count all history since 1903, or offer a windowed version
# MAGIC   (since 1980, last 20 years) as separate columns?
# MAGIC - Does the app need a map, or is Genie's own chart rendering sufficient for the
# MAGIC   10 app-experience points?
