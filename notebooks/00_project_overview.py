# Databricks notebook source
# MAGIC %md
# MAGIC # Powerline Bushfire Exposure Console
# MAGIC
# MAGIC **Databricks Community Contest — Genie-Powered App Challenge**
# MAGIC **Track A — Real-World Problem Solver**
# MAGIC
# MAGIC Notebook 00 of the series. This one holds no code. It is the document you
# MAGIC read first if you come back to this project in three months and need to
# MAGIC remember why any of it exists.

# COMMAND ----------

# MAGIC %md
# MAGIC ## The problem
# MAGIC
# MAGIC Overhead powerlines and bushfire have a two-way relationship. Lines run through
# MAGIC country that burns, and lines start fires when vegetation contacts them.
# MAGIC Victoria regulates this through electric line clearance obligations, and every
# MAGIC distribution business runs a vegetation management program against it.
# MAGIC
# MAGIC The people doing that work have a budgeting problem. There is more network than
# MAGIC there is inspection and clearance money, so someone has to decide which spans get
# MAGIC attention this year. That decision needs a view of which parts of the network sit
# MAGIC in country with a real fire history.
# MAGIC
# MAGIC The data to answer that exists and is public. It is just never joined up.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Why this needs Genie rather than a dashboard
# MAGIC
# MAGIC The contest awards half its points for Genie being genuinely central, and the
# MAGIC stated test is whether the app collapses if you remove it. This use case passes
# MAGIC that test honestly, for one reason: the questions are not knowable in advance.
# MAGIC
# MAGIC A dashboard can show fire frequency by council. It cannot answer:
# MAGIC
# MAGIC - Which 22 kV segments in Gippsland run through country burnt more than three
# MAGIC   times since 1980, excluding planned burns?
# MAGIC - Where have powerline-caused ignitions occurred near SWER lines?
# MAGIC - Compare bushfire exposure between alpine resorts and their neighbouring councils.
# MAGIC
# MAGIC Each of those is a different combination of filters, groupings and thresholds.
# MAGIC Pre-building them all is impossible. That is the space natural language is
# MAGIC actually good at, and it is why the semantic layer matters more than the app.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Data sources
# MAGIC
# MAGIC All three are open Victorian government data from DEECA DataShare, supplied as
# MAGIC ESRI shapefiles in GDA2020 geographic projection (EPSG:7844), licensed
# MAGIC Creative Commons Attribution 4.0 International.
# MAGIC
# MAGIC | Layer | Rows | Geometry | What it gives us |
# MAGIC |---|---|---|---|
# MAGIC | `FIRE_HISTORY_SCAR` | 109,219 | Polygon | Where fire has been, 1903 to 2026 |
# MAGIC | `POWER_LINE` (Vicmap) | 396,455 | LineString | Where the network is, with voltage |
# MAGIC | `AD_LGA_AREA_POLYGON` (Vicmap) | 137 | Polygon | Council boundaries for reporting |
# MAGIC
# MAGIC No AusNet data, no commercial data, no personal data. Everything here can be
# MAGIC downloaded by anyone.
# MAGIC
# MAGIC **Attribution required in the app and the write-up:**
# MAGIC State Government of Victoria, Department of Energy, Environment and Climate
# MAGIC Action (DEECA), CC BY 4.0.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Why H3 instead of spatial joins
# MAGIC
# MAGIC The core computation is "which fire scars intersect which line segments". Done as
# MAGIC polygon-to-line intersection across 109k polygons and 396k lines, that is an
# MAGIC expensive operation and a fragile one for Genie to generate SQL against.
# MAGIC
# MAGIC H3 turns it into an integer join. Both layers get indexed to hexagonal cells, and
# MAGIC the intersection becomes a match on cell ID. Photon accelerates this natively.
# MAGIC
# MAGIC More importantly, it means **Genie never sees a geometry column**. The spatial
# MAGIC work happens once, up front, in Phase 1b. What Genie queries is a flat table where
# MAGIC every spatial fact has already become an ordinary number: `times_burnt`,
# MAGIC `last_burn_year`, `lga_name`. Every question then becomes plain aggregation, which
# MAGIC Genie handles reliably.
# MAGIC
# MAGIC That is the single most important design decision in the project.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Resolution 8, and cover rather than polyfill
# MAGIC
# MAGIC Two sub-decisions, both made against measurements rather than guesses.
# MAGIC
# MAGIC **`h3_coverash3`, not `h3_polyfillash3`.** Polyfill returns only cells whose centre
# MAGIC falls inside the polygon. Tested against the LGA layer at resolution 7, three of
# MAGIC the first five councils returned zero cells — they were smaller than a single cell.
# MAGIC Applied to fire scars, small burns would silently vanish from every answer. Cover
# MAGIC returns every cell that touches the geometry, so nothing can disappear.
# MAGIC
# MAGIC **Resolution 8 (~460 m edge).** Resolution 9 is roughly seven times the row count
# MAGIC for precision the source data does not have. The fire history `ACCURACY` column
# MAGIC records values down to "greater than 100m", and Vicmap states powerline locations
# MAGIC are unverified. Indexing at 175 m would be false confidence.
# MAGIC
# MAGIC Measured cell counts at resolution 8:
# MAGIC
# MAGIC | Layer | Cells |
# MAGIC |---|---|
# MAGIC | Fire scars | 505,444 |
# MAGIC | Power lines | 560,406 |
# MAGIC | LGA (VIC only) | 278,436 |
# MAGIC
# MAGIC Comfortable volumes. Zero rows produced zero cells, which was the check that
# MAGIC confirmed the cover decision.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Deliverables
# MAGIC
# MAGIC Four things, not one. The app alone is not a submission.
# MAGIC
# MAGIC 1. A deployed Databricks App on Free Edition with a Genie Agent attached
# MAGIC 2. A demo video
# MAGIC 3. A write-up published to Databricks Community Articles
# MAGIC 4. The Google Form submission
# MAGIC
# MAGIC **Deadline: 31 August 2026, 11:30 PM PDT** (about 4:30 PM 1 September, Melbourne).
# MAGIC
# MAGIC Scoring is 20 points for Genie being central, 10 for track execution, 10 for app
# MAGIC experience. Half the marks ride on the semantic layer, which is Phase 2.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Notebook series
# MAGIC
# MAGIC | Notebook | Purpose |
# MAGIC |---|---|
# MAGIC | `00_project_overview` | This document |
# MAGIC | `01_build_plan_and_progress` | Phase plan and current status |
# MAGIC | `02_phase1a_ingestion` | Shapefiles to bronze Delta tables |
# MAGIC | `03_phase1b_h3_indexing` | H3 indexing and the exposure join |
# MAGIC | `04_phase2_semantic_layer` | Curated views and column comments |
# MAGIC | `05_phase3_genie_testing` | Question bank and regression checks |
# MAGIC
# MAGIC Notebooks 03 onward do not exist yet.
