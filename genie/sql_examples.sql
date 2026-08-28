-- Trusted SQL examples for the Genie Agent
--
-- Add each of these in the Genie Agent UI under "SQL queries" / "Example queries",
-- pairing the question in the comment with the query beneath it.
--
-- These teach patterns, not just answers. Genie generalises from them, so the guards
-- matter more than the specific filters: the HAVING clause in example 1 is what stops
-- Falls Creek winning every ranking on a single segment, and Genie will carry that
-- pattern to rankings you never wrote an example for.
--
-- Keep this file in sync with what is in the UI.


-- ============================================================================
-- 1. Which local government areas have the most bushfire-exposed network?
-- Teaches: the minimum-denominator guard, council-only filter, band over raw count
-- ============================================================================
SELECT
  lga_name,
  COUNT(*)                                        AS segments,
  COUNT_IF(bushfire_exposure_band = 'High')       AS high_exposure_segments,
  ROUND(100.0 * COUNT_IF(bushfire_exposure_band IN ('Moderate','High'))
        / COUNT(*), 1)                            AS pct_moderate_or_high,
  ROUND(AVG(times_major_bushfire), 3)             AS avg_major_bushfires
FROM workspace.bushfire.v_segment_exposure
WHERE lga_type = 'Council'
GROUP BY lga_name
HAVING COUNT(*) >= 100
ORDER BY pct_moderate_or_high DESC
LIMIT 15;


-- ============================================================================
-- 2. Are SWER lines more exposed to bushfire than other high voltage lines?
-- Teaches: rate not raw count, always give the denominator
-- ============================================================================
SELECT
  CASE WHEN is_swer THEN 'SWER (12.7 kV)' ELSE 'Other HV and transmission' END AS line_type,
  COUNT(*)                                        AS segments,
  ROUND(AVG(times_bushfire_since_1980), 3)        AS avg_bushfires_since_1980,
  ROUND(AVG(times_major_bushfire), 3)             AS avg_major_bushfires,
  ROUND(100.0 * COUNT_IF(bushfire_exposure_band IN ('Moderate','High'))
        / COUNT(*), 1)                            AS pct_moderate_or_high
FROM workspace.bushfire.v_segment_exposure
GROUP BY is_swer;


-- ============================================================================
-- 3. Which segments should we inspect first?
-- Teaches: prioritisation shape - band first, then recency, actionable list length
-- ============================================================================
SELECT
  segment_id,
  lga_name,
  voltage,
  is_swer,
  times_major_bushfire,
  times_bushfire_last_20yr,
  last_bushfire_season,
  largest_fire_name
FROM workspace.bushfire.v_segment_exposure
WHERE bushfire_exposure_band = 'High'
ORDER BY times_major_bushfire DESC, last_bushfire_season DESC
LIMIT 50;


-- ============================================================================
-- 4. How many bushfires were there in the 2019/20 season?
-- Teaches: season is the ending year; separate bushfire from planned burn
-- ============================================================================
SELECT
  COUNT(*)                     AS bushfires,
  ROUND(MAX(max_area_ha))      AS largest_fire_ha,
  MAX_BY(fire_name, max_area_ha) AS largest_fire_name
FROM workspace.bushfire.v_fire_history
WHERE season = 2020          -- season 2020 = July 2019 to June 2020
  AND fire_type = 'Bushfire';


-- ============================================================================
-- 5. What network did the Snowy Complex fire affect?
-- Teaches: using the bridge view, COUNT(DISTINCT segment_id) not COUNT(*)
-- ============================================================================
SELECT
  fire_name,
  season,
  COUNT(DISTINCT segment_id)                              AS segments_affected,
  COUNT(DISTINCT CASE WHEN is_swer THEN segment_id END)   AS swer_segments,
  COUNT(DISTINCT lga_name)                                AS lgas_touched
FROM workspace.bushfire.v_segment_fire
WHERE fire_name ILIKE '%SNOWY%'
  AND fire_type = 'Bushfire'
GROUP BY fire_name, season
ORDER BY segments_affected DESC;


-- ============================================================================
-- 6. Which fires were caused by powerlines?
-- Teaches: the 3% caveat belongs with every cause answer
-- ============================================================================
SELECT
  fire_name,
  season,
  ROUND(max_area_ha) AS max_area_ha,
  ffm_region
FROM workspace.bushfire.v_fire_history
WHERE powerline_caused = TRUE
ORDER BY season DESC;
-- Note when presenting: cause is recorded for only about 3% of fires, so these 8
-- are a floor rather than a statewide total.


-- ============================================================================
-- 7. Which parts of the network have burnt most recently?
-- Teaches: recency as its own question, NULL means never burnt
-- ============================================================================
SELECT
  lga_name,
  COUNT(*)                                          AS segments,
  COUNT_IF(last_bushfire_season >= 2021)            AS burnt_in_last_5_seasons,
  MAX(last_bushfire_season)                         AS most_recent_season
FROM workspace.bushfire.v_segment_exposure
WHERE lga_type = 'Council'
GROUP BY lga_name
HAVING COUNT(*) >= 100
ORDER BY burnt_in_last_5_seasons DESC
LIMIT 15;


-- ============================================================================
-- 8. Compare exposure by voltage class
-- Teaches: grouping by network tier, transmission is a small population
-- ============================================================================
SELECT
  voltage_class,
  COUNT(*)                                        AS segments,
  ROUND(AVG(times_major_bushfire), 3)             AS avg_major_bushfires,
  ROUND(100.0 * COUNT_IF(bushfire_exposure_band IN ('Moderate','High'))
        / COUNT(*), 1)                            AS pct_moderate_or_high
FROM workspace.bushfire.v_segment_exposure
GROUP BY voltage_class
ORDER BY pct_moderate_or_high DESC;


-- ============================================================================
-- 9. What were the largest bushfires on record?
-- Teaches: MAX not SUM on area; polygon_count explains detail not frequency
-- ============================================================================
SELECT
  fire_name,
  season,
  ROUND(max_area_ha) AS max_area_ha,
  polygon_count,
  ffm_region,
  accuracy
FROM workspace.bushfire.v_fire_history
WHERE fire_type = 'Bushfire'
ORDER BY max_area_ha DESC
LIMIT 15;
-- Note when presenting: pre-1980 records are indicative. Region, district and cause
-- are often missing and mapped areas are approximate.


-- ============================================================================
-- 10. How exposed are the alpine resorts?
-- Teaches: alpine resorts are not councils; small populations need caution
-- ============================================================================
SELECT
  lga_name,
  lga_type,
  COUNT(*)                                  AS segments,
  SUM(times_major_bushfire)                 AS total_major_bushfire_exposures,
  MAX(last_bushfire_season)                 AS most_recent_bushfire_season
FROM workspace.bushfire.v_segment_exposure
WHERE lga_type IN ('Alpine resort', 'Island')
GROUP BY lga_name, lga_type
ORDER BY segments DESC;
-- Note when presenting: these are unincorporated areas with very few segments each,
-- so averages are unstable. Report counts rather than rates.


-- ============================================================================
-- 11. How much of the network has never been burnt by a bushfire?
-- Teaches: zero is a meaningful value, not missing data
-- ============================================================================
SELECT
  bushfire_exposure_band,
  COUNT(*)                                          AS segments,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_network
FROM workspace.bushfire.v_segment_exposure
GROUP BY bushfire_exposure_band
ORDER BY segments DESC;


-- ============================================================================
-- 12. What causes fires in Victoria?
-- Teaches: count distinct fires, and lead with the coverage caveat
-- ============================================================================
SELECT
  cause_group,
  COUNT(*)                                          AS fires,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_all_fires
FROM workspace.bushfire.v_fire_history
GROUP BY cause_group
ORDER BY fires DESC;
-- Note when presenting: "Not recorded" is 16,729 of 17,934 fires. Percentages for
-- the named causes should be quoted against the investigated subset, not all fires.


-- ============================================================================
-- 13. Which SWER segments in high fire country have burnt recently?
-- Teaches: combining risk class, exposure band and recency in one filter
-- ============================================================================
SELECT
  segment_id,
  lga_name,
  times_major_bushfire,
  times_bushfire_last_20yr,
  last_bushfire_season,
  years_since_last_bushfire,
  largest_fire_name
FROM workspace.bushfire.v_segment_exposure
WHERE is_swer = TRUE
  AND bushfire_exposure_band IN ('Moderate','High')
  AND last_bushfire_season >= 2016
ORDER BY times_major_bushfire DESC, last_bushfire_season DESC
LIMIT 50;
