# Decision Log

Every methodological decision, deviation from the research plan, and defect that affected results, in the order they were made. The README states the current protocol; this log records how it got there, what evidence each choice rested on, and what else was considered.

Entries are append-only. A later decision that reverses an earlier one gets a new entry that references the old one; old entries are not edited except to add a "Superseded by" line.

Each entry records:

- **Date:** when it was decided.
- **Decided by:** the researcher, or the coding assistant (Claude) working under the researcher's standing instructions. Where the assistant recommended one option and the researcher chose another, both are recorded.
- **Evidence:** the findings the decision rests on, with numbers.
- **Alternatives:** options considered and why they were not taken.
- **Where:** README section, config key, notebook cell, or commit.

---

## D01. Pin package versions

- **Date:** 2026-10-19
- **Decided by:** Researcher
- **Decision:** Pin every dependency to an exact version in `requirements.txt`.
- **Reason:** Results must be reproducible from the repository alone. Library defaults (for example, scikit-learn solver and binning behavior) change across releases.
- **Where:** README section 10; commits f4030af, 0eca682.

## D02. Label groups for positive-unlabeled data

- **Date:** 2026-10-19
- **Decided by:** Researcher. The three-group framing came from the researcher's reading of the red-team file during the label audit.
- **Decision:** Every authentication record is a known positive, a possible red-team record, or unlabeled. Only known positives count toward recall. Unlabeled records are treated as negative for training only, as a stated noisy-label assumption.
- **Evidence:** The red-team file has four fields (time, user, source computer, destination computer) and lists positive events only. It has no label column and no benign records, so the absence of a key does not certify an event benign. The red-team accounts and computers also carry ordinary activity, so a label cannot be extended by identity.
- **Alternatives:**
  - Treat unlabeled as benign. Rejected: absence from a partial red-team record is not evidence of benign activity.
  - Extend labels to every event by a red-team user or computer. Rejected: it would label real users' ordinary activity as attack.
- **Where:** README sections 4.1 and 4.4; commit 167e68e.

## D03. Possible red-team rule

- **Date:** 2026-10-19
- **Decided by:** Assistant proposed, researcher approved.
- **Decision:** A record is possible red-team if its source computer and source user both appear in the red-team file, it is an NTLM network logon, and it falls on a day with at least one red-team key.
- **Evidence:** Every matched red-team event is NTLM with logon type Network.
- **Alternatives:** Add behavioral conditions such as novelty, login rate or volume. Rejected: those are the signals the models score, so evaluating against a label built from them would be circular. The rule uses only facts from the red-team file.
- **Where:** README section 4.2; `ingestion.possible_redteam`; commit 167e68e.

## D04. Recall denominator of 701 matched keys

- **Date:** 2026-10-19
- **Decided by:** Researcher.
- **Decision:** Recall is measured over the 701 red-team keys with an exact authentication match. The 14 unmatched keys, all from source computer `C17693`, are excluded from the denominator and reported separately. Where an unmatched key has records agreeing on time, user and source but not destination, those records are not substituted as known positives.
- **Reason:** A key with no matching authentication record cannot be recovered by any ranking of authentication records, so counting it would cap recall below 100% for reasons unrelated to the models.
- **Where:** README section 4.3; commit 167e68e.

## D05. Day-51 exception

- **Date:** 2026-10-19
- **Decided by:** Researcher asked for the investigation; the exception was added on the researcher's instruction.
- **Decision:** Row 897415494 (day 51, `U349@DOM1` from `C17693` to `C3601`, NTLM network) is possible red-team, although it fails the labeled-day condition of D03.
- **Evidence:**
  - It exactly repeats red-team key 81 from day 9.
  - `U349@DOM1` never otherwise authenticates from `C17693`.
  - The account's owner was making ordinary Kerberos logons from their usual workstations in the same hour.
- **Alternatives:** Leave it unlabeled, as the rule alone would. Rejected because the evidence points strongly to unrecorded red-team reuse. Making it a known positive was also rejected, because it has no red-team key.
- **Where:** README section 4.2; `labels.possible_exceptions` in `configs/experiment.yaml`; commit de17698.

## D06. Replace the provisional split boundaries

- **Date:** 2026-10-19
- **Decided by:** Assistant ran the coverage audit and recommended the design; researcher chose it.
- **Decision:** Warm-up days 1–5, training 6–12, validation 13–20, test 21–30, alert volume 31–58. Settings are chosen on validation, the final models are refit on days 6–20 with those settings, and test is scored once.
- **Evidence:** The provisional split (training 8–30, validation 31–42, test 43–58) left validation and test with zero known positives. All 701 matched keys fall on days 2–30. The new cuts fall on days without keys, so no attack burst is split across periods. The resulting periods hold:

  | Period | Days | Matched keys |
  |---|---|---:|
  | Training | 6–12 | 291 |
  | Validation | 13–20 | 333 |
  | Test | 21–30 | 52 |
  | Refit (training + validation) | 6–20 | 624 |
- **Alternatives:**
  - Three-way split with no refit. Simpler, but the final model would never learn from the day-13 burst (207 keys).
  - Rolling origin with two folds. More evaluation keys (385), but needs inner tuning splits and adds complexity.
  - Unsupervised ranking on all 676 post-warm-up keys. Avoids the one-burst-per-period problem but drops the supervised models in the plan.
- **Limitation:** Each period is dominated by one burst: day 9 in training, day 13 in validation, day 27 in test. Source `C17693` supplies most keys in every period. The boundaries were set from label coverage alone, before any model was fit.
- **Where:** README section 5.3; `splits` and `refit_days` in the config; commits 407fcfc, 1e9f452.

## D07. Treatment of the DNS break

- **Date:** 2026-10-19
- **Decided by:** Researcher. The assistant had recommended restricting DNS comparisons to days 27 onward; the researcher chose to use all days and flag the era.
- **Decision:** Compute DNS features on all days, give every record a `dns_full_collection` indicator (day 27 onward), and drop "time since any DNS record from the source", which mainly measures whether a host is collected. Test for a collection artifact with an authentication-only model plus one diagnostic feature: whether the source has any earlier DNS record.
- **Evidence:**
  - DNS covers about 4,348 source computers per day on days 1–23. Day 24 has 9,610 records, days 25–26 have none, and from day 27 coverage is 14,875 sources and 0.6–1.6 million records per day.
  - No red-team source computer has any DNS record before day 27.
  - Only 46 test keys fall in the full-collection era.
- **Alternatives:**
  - DNS comparisons only from day 27 (the assistant's recommendation). Avoids the artifact but leaves DNS conclusions resting on 46 keys.
  - Audit the flow and process archives for earlier coverage. Expands the study's scope.
  - Declare the DNS questions infeasible.
- **Risk accepted:** A model can learn "no DNS history means attack" from the collection gap. The artifact-check model and by-era reporting (README section 6) exist to detect this; results in D14.
- **Where:** README section 6; `dns.full_collection_from_day` in the config; commits 18d204d, f56435f.

## D08. Gradient-boosting library

- **Date:** 2026-10-19
- **Decided by:** Researcher, on the assistant's recommendation.
- **Decision:** scikit-learn `HistGradientBoostingClassifier`.
- **Reason:** It handles missing values and categorical inputs natively and adds no dependency. LightGBM and XGBoost both need the `libomp` system library on macOS, which was not installed.
- **Alternatives:** LightGBM (fastest, most common for tabular ranking); XGBoost.
- **Where:** README section 8; commit edd67c5.

## D09. Causal feature construction

- **Date:** 2026-10-20
- **Decided by:** Assistant, implementing README section 6.
- **Decision:**
  - Each feature for an event at time `t` uses only observations strictly before `t`. As-of joins exclude exact timestamp matches, so events in the same second never see each other.
  - Windowed counts are differences of cumulative counts.
  - History from earlier days is carried forward as state.
  - Raw user and computer IDs are never model inputs.
- **Verification:** Counts, windows, distinct-destination counts and time gaps were checked against the raw records for sampled rows. One apparent mismatch in the type-frequency feature turned out to be an error in the check: Polars `mean` skips nulls. The feature itself was correct.
- **Output:** 975,669,982 feature rows for days 6–58.
- **Where:** `src/knightglass/features.py`; notebook 02; commit 65fe6a9.

## D10. Training samples

- **Date:** 2026-10-20
- **Decided by:** Assistant, implementing README section 5.4.
- **Decision:**
  - Keep every known-positive row.
  - Draw 1,000,000 unlabeled rows by a time-stratified reservoir, with daily quotas proportional to each day's unlabeled count, seeded per day.
  - Exclude possible red-team rows.
  - Store inclusion weights equal to the inverse inclusion probability.
- **Samples:**

  | Sample | Days | Known positives | Unlabeled rows | Inclusion probability |
  |---|---|---:|---:|---:|
  | Train | 6–12 | 291 | 1,000,000 | 0.0081 |
  | Refit | 6–20 | 624 | 1,000,000 | about 0.0039 |
- **Where:** `src/knightglass/sampling.py`; manifests in `data/processed/samples/`; commit 810e502.

## D11. Baseline settings grid and selection rule

- **Date:** 2026-10-20
- **Decided by:** Assistant.
- **Decision:**
  - Logistic regression with `C` in {0.01, 1.0}.
  - Gradient boosting with two settings (learning rate 0.1 with 31 leaves, and 0.05 with 63 leaves; 100 iterations each).
  - Each setting is fit both unweighted and with inclusion weights, giving the weighted/unweighted comparison in README section 5.4.
  - For each feature set and model kind, the setting with the highest mean validation recall across the 100/500/1000 budgets is chosen.
- **Rule baselines:**
  - Novelty: a first-ever user-to-destination logon ranks first; ties are broken by distinct destinations in the past hour.
  - Rate: distinct destinations in the past hour.
  - Neither rule is fit.
- **Where:** `models` in the config; `src/knightglass/models.py`; notebook 03.

## D12. Score only days with labeled rows for recall

- **Date:** 2026-10-20
- **Decided by:** Assistant.
- **Decision:** Recall on validation and test is computed by scoring every row of each day that has at least one known-positive or possible row. Days with neither are not scored for recall.
- **Reason:** Each day is ranked independently against its own review budget, so a day without labeled rows cannot change recall. All rows of a scored day are ranked, as README section 5.5 requires. This cut a multi-hour scoring pass to the labeled days only:
  - validation: days 13–16
  - test: days 21–23 and 27–30
- **Not covered:** Alert-volume reporting for days 31–58 still needs those days scored in full. That belongs to Phase 5.
- **Where:** `models.labeled_days`; notebook 03.

## D13. Defect: gradient-boosting divergence, and the voided first test pass

- **Date:** 2026-10-20 to 2026-10-21
- **Decided by:** Assistant diagnosed the defect. The researcher chose to re-score test and log it.
- **Finding:** In the first full run, gradient boosting recovered almost no keys (0–6% on test), below the novelty rule. Diagnosis on the training sample:
  - Training AUC was 0.58, close to chance, against 0.999 for logistic regression on the same rows.
  - Scores were saturated at exactly 0 and 1.
- **Cause:** With 291 positives among 1,000,291 rows, predicted probabilities fall close to 0. The Newton step's hessian then approaches 0 and leaf values explode.
- **Fix 1:** Add `l2_regularization: 1.0` to both gradient-boosting settings. On validation day 13:

  | Combined model, day 13 | Training AUC | Recall at 100 / 500 / 1000 |
  |---|---:|---|
  | Before | 0.58 | 1% / 4% / 10% |
  | After | 1.00 | 7% / 42% / 55% |
- **Second finding:** After fix 1, the refit auth-only model (whose chosen setting was inclusion-weighted) still diverged, with training AUC 0.46 on the refit sample. The raw inclusion weights average 123 on the train sample and 258 on the refit sample, because the refit sample covers more days at the same cap. The same setting trained correctly at the smaller scale.
- **Fix 2:** Normalize weights to mean 1 before fitting. Only relative weights matter, so this leaves the weighting scheme unchanged. On the refit sample, training AUC rose from 0.46 to 0.9999. A unit test checks that multiplying the weights by a constant does not change the fitted model.
- **Test-set handling:** The plan says test is scored once. The first test pass was voided for all model rankers because it measured a code defect, not a modeling choice. Both fixes were chosen on training-sample evidence and validation day 13; no test result informed them. All model rankers were reselected on validation, refit and scored on test again. The rule test scores from the first pass stand, since rules are unaffected.
- **Safeguard added:** Every final model is checked for training AUC on its own fitting sample before its results are accepted.
- **Where:** `models` in the config; `ModelRanker.fit`; `tests/test_models.py`; notebook 03, cell 03-7.

## D14. Phase 3 baseline results

- **Date:** 2026-10-21
- **Decided by:** Assistant ran the pipeline and verification. The researcher has not yet made the DNS decision; it will get its own entry.
- **Verification:**
  - Each final model was refit on days 6–20 and checked for AUC on its own refit sample: logistic 0.998–0.999, gradient boosting 1.000. None diverged (D13).
  - Validation chose the unweighted variant for every feature set and model kind.
- **Chosen settings:**
  - Logistic: `C = 1.0` for auth-only and combined; `C = 0.01` for auth plus collection.
  - Gradient boosting: learning rate 0.1 with 31 leaves for auth-only and combined; learning rate 0.05 with 63 leaves for auth plus collection.
- **Recall of unique keys at daily review budgets of 100 / 500 / 1000:**

  | Ranker | Validation (333 keys) | Test (52 keys) | Test, days 27–30 (46 keys) | Test, days 21–23 (6 keys) |
  |---|---|---|---|---|
  | Novelty rule | .01 / .14 / .15 | .00 / .02 / .02 | .00 / .00 / .00 | .00 / .17 / .17 |
  | Rate rule | .00 / .00 / .00 | .00 / .00 / .00 | .00 / .00 / .00 | .00 / .00 / .00 |
  | Auth-only, logistic | .14 / .38 / .45 | .15 / .33 / .38 | .15 / .35 / .41 | .17 / .17 / .17 |
  | Auth-only, gradient boosting | .07 / .30 / .41 | .19 / .38 / .44 | .20 / .39 / .46 | .17 / .33 / .33 |
  | Combined, logistic | .19 / .39 / .47 | .17 / .35 / .40 | .17 / .37 / .43 | .17 / .17 / .17 |
  | Combined, gradient boosting | .07 / .32 / .45 | .13 / .35 / .56 | .15 / .39 / .59 | .00 / .00 / .33 |
  | Auth + collection, logistic | .25 / .38 / .43 | .10 / .21 / .25 | .09 / .22 / .26 | .17 / .17 / .17 |
  | Auth + collection, gradient boosting | .23 / .43 / .50 | .06 / .06 / .06 | .00 / .00 / .00 | .50 / .50 / .50 |

- **Sensitivity check:** Counting possible red-team rows as positives (85 keys and rows on test) lowers model recall by 0.0–0.12 (README section 4.4). At budget 1000, combined gradient boosting's lead disappears: it ties auth-only gradient boosting at .44.
- **Findings:**
  - Every model ranks far above both rules. The rate rule recovers nothing at any budget: the top distinct-destination counts belong to busy accounts, not the red-team.
  - The collection-artifact check (README section 6) behaves as the artifact hypothesis predicts. Adding only "the source has any earlier DNS record" gives the best validation recall, where no red-team source has DNS. On test days 27–30, where every red-team source has DNS, it gives the worst recall (gradient boosting .00 at every budget). The model learned "no DNS history means attack" from the collection gap.
  - On test days 27–30, combined gradient boosting recovers 27 of 46 keys at budget 1000, against 21 for auth-only gradient boosting (+6). Combined logistic recovers 20, against 19 for auth-only logistic (+1). At budgets 100 and 500 the combined models do not exceed auth-only gradient boosting. The comparison rests on 46 keys, most from the day-27 burst of source `C17693`.
- **Where:** notebook 03; `results/metrics/phase3_baselines.json` (not tracked).
