# Genie Test Bank and Scoring Rubric

Phase 3. Twenty-four questions to run against the Genie Agent, with what a correct
answer looks like and how to score it.

Run the whole set after any change to the instructions, the SQL examples or the views.
Record scores in the table at the end so you can see whether a tuning change actually
helped or just moved the failures around.

The Genie Agent UI has a **Benchmark** tab which may automate some of this. Worth
checking before doing it by hand.

---

## How to score

Each question gets three marks out of 2. Maximum 6 per question, 144 overall.

**Correctness (0-2)** — is the SQL right and the number accurate?
- 2: correct query, correct result
- 1: right idea, wrong filter or wrong column
- 0: wrong table, wrong logic, or refused a question it should answer

**Caveats (0-2)** — did it apply the rule the question was designed to test?
- 2: applied without being asked, and said so
- 1: applied but did not explain, or explained but did not apply
- 0: missed it entirely

**Framing (0-2)** — is the answer shaped for the question asked?
- 2: right level of detail, right ordering, actionable where the question was operational
- 1: correct but poorly shaped — a wall of rows for a summary question, or vice versa
- 0: answered a different question

A score below 4 on any question means the instructions need work on that rule.

**Target: 120 of 144 (83%) before recording the demo.**

---

## Section A — Basic retrieval
Should be near-perfect. If these fail, something is structurally wrong.

**A1. How many powerline segments are in the dataset?**
Expect: 159,268. Should mention LV is excluded.

**A2. How many fires are in the fire history?**
Expect: 17,934 fires. Watch for it counting polygons (109,219) instead.

**A3. What is the largest fire in the record?**
Expect: Black Firday 1939, around 2.38 million ha. Should flag that historic records
are indicative.

**A4. How many segments are in the High exposure band?**
Expect: 301.

---

## Section B — The season convention
Tests rule 1. The most likely place for a confident wrong answer.

**B1. How many bushfires were there in 2019?**
Expect: it asks whether you mean the 2019/20 summer, or answers for `season = 2020`
and says so. A bare answer for `season = 2019` scores 0 on caveats.

**B2. What happened during Black Summer?**
Expect: maps to `season = 2020`, names Upper Murray, Tambo and Snowy Complex.

**B3. Show me fires from the 2009 season.**
Expect: `season = 2009`, which is the 2008/09 summer including Black Saturday.

---

## Section C — Bushfire versus planned burn
Tests rule 2. The most consequential rule in the set.

**C1. Which council has the most burnt network?**
Expect: uses bushfire columns, not `times_burnt_total`. If it uses the combined
column without flagging that planned burns dominate, score 0 on caveats.

**C2. How much fire activity has there been in East Gippsland?**
Ambiguous by design. Expect it to separate bushfires from planned burns rather than
picking one silently.

**C3. Do planned burns reduce risk?**
Expect: explains that planned burns are fuel reduction, and that a high
`times_planned_total` indicates management rather than risk. Should not attempt to
prove causation from this data.

---

## Section D — The small denominator trap
Tests rule 5.

**D1. Which LGA has the highest average bushfire exposure?**
Expect: applies a minimum segment threshold and says so. If Falls Creek Alpine Resort
(1 segment) tops the list, score 0 on caveats.

**D2. Rank all councils by exposure.**
Expect: `lga_type = 'Council'` filter plus the threshold.

**D3. How exposed is Falls Creek?**
Expect: answers, but notes that a single segment makes the figure unreliable.

---

## Section E — Area and totals
Tests rule 3.

**E1. What is the total area burnt in Victoria since 1903?**
Expect: declines to give a total, explaining that polygons overlap so a sum double
counts. Offering the largest individual fires instead scores 2 on framing.

**E2. How many hectares did the 2019/20 fires burn?**
Same trap, narrower scope. Same expected behaviour.

**E3. How big was the Snowy Complex fire?**
Expect: around 358,751 ha as the largest single polygon, described as indicative.

---

## Section F — Cause data coverage
Tests rule 4.

**F1. What causes most bushfires in Victoria?**
Expect: leads with the fact that cause is recorded for only ~3% of fires. An answer
that says "lightning" without that caveat scores 0.

**F2. How many fires were caused by powerlines?**
Expect: 8, explicitly described as a floor rather than a total.

**F3. Are powerlines a major cause of bushfires?**
Expect: refuses to conclude either way from this data, because 97% of fires have no
recorded cause.

---

## Section G — Cross-view questions
Tests whether the bridge view is being used.

**G1. Which segments did the Tambo Complex fire affect?**
Expect: `v_segment_fire`, around 369 segments, 35 of them SWER.

**G2. How many councils did the Upper Murray fire cross?**
Expect: answers, and notes that single-LGA attribution undercounts this.

**G3. Which fire has affected the most network?**
Expect: `COUNT(DISTINCT segment_id)`, not `COUNT(*)`.

---

## Section H — Domain reasoning
The questions that separate a good semantic layer from a schema dump.

**H1. Are SWER lines riskier?**
Expect: 6.9% vs 3.9% moderate-or-high, with both the rate and the denominator.
Bonus if it explains what SWER is.

**H2. Where should we spend our vegetation clearance budget?**
Expect: High band, ordered by exposure and recency, a list short enough to act on.
Should not claim to know costs or clearance obligations.

**H3. Which parts of the network are safest?**
Expect: treats zero as meaningful (never burnt), not as missing data. 129,885
segments in the None band.

**H4. How many kilometres of line are at high risk?**
Expect: explains there is no length column, gives segment counts instead, and does
not fabricate a distance. Scores 0 if it invents kilometres.

---

## Results log

| Run | Date | Change made | Score /144 | Weakest section |
|---|---|---|---|---|
| 1 | | baseline, instructions v1 | | |
| 2 | | | | |
| 3 | | | | |

Record the specific failures, not just the total. A section scoring badly points at
one instruction that needs rewriting, which is a much faster fix than general tuning.
