# KnightGlass
## Authentication Threat Detection Under Incomplete DNS Telemetry

> **Status:** research proposal and notebook implementation plan. No experimental results or novelty claims have yet been established.

## Project Progress

**Overall: 54%** (19 of 35 tasks) -- see [TODO](#todo) for details

```
Phase 0 - Setup               ████████████████████ 100% (5/5) ✓
Phase 1 - Data audit          ████████████████████ 100% (10/10) ✓
Phase 2 - Features & sampling ████████████░░░░░░░░  60% (3/5)
Phase 3 - Baselines           ████░░░░░░░░░░░░░░░░  20% (1/5)
Phase 4 - Outage replay       ░░░░░░░░░░░░░░░░░░░░   0% (0/5)
Phase 5 - Evaluation          ░░░░░░░░░░░░░░░░░░░░   0% (0/5)
```

*Last updated: 2026-10-19*

## Abstract

KnightGlass investigates whether DNS context improves the ranking of suspicious authentication events and whether that improvement survives interruptions in DNS collection. The study uses the LANL Comprehensive, Multi-Source Cyber-Security Events dataset. Authentication events remain observable; supporting DNS events are selectively withheld in controlled experiments. Authentication-only, combined-source, and outage-trained models are compared using chronological evaluation and fixed analyst review budgets. Known red-team authentication events provide positive evaluation examples; other events remain unlabeled rather than verified benign. The intended contribution is an experimentally supported assessment of when DNS context is useful and whether structured-outage training provides operational value beyond a simpler authentication-only fallback.

## 1. Motivation and Scope

A detector that depends on supporting telemetry should not become less useful than a simpler detector when that source disappears. KnightGlass asks whether additional DNS context is worth its operational dependency.

The project does not attempt to infer business authorization, detect every attack, prove that DNS inactivity represents collection failure, or attribute outages to an attacker. It is a retrospective, controlled study of authentication-event ranking under specified supporting-source loss.

Implementation targets reproducible Jupyter Notebooks, not a production SIEM or a real-time collection platform.

## 2. Research Questions and Hypotheses

### RQ1: Does DNS context add useful signal?

Does a model using historical authentication and DNS features recover more known red-team events within a fixed daily review budget than an authentication-only model?

H1: Under complete observed telemetry, adding DNS context improves known-positive recall at the same review budget.

### RQ2: Does structured-outage training improve robustness?

When DNS records are suppressed in contiguous time windows, does a model trained on similar disruptions retain more detection performance than a combined-source model trained only on complete telemetry?

H2: Structured-outage training reduces performance degradation under held-out DNS-loss scenarios.

### RQ3: Is the added complexity justified?

Does an outage-trained model outperform an authentication-only detector or a simple fallback strategy across complete and interrupted telemetry conditions?

H3: The proposed model provides a measurable benefit beyond these simpler alternatives without a substantial complete-telemetry penalty.

These are hypotheses to test, not expected findings. A negative result is valid. If DNS contributes no meaningful baseline benefit, the proposed resilience contribution should be reconsidered.

## 3. Dataset and Provenance

Official source: https://csr.lanl.gov/data/cyber1/

LANL documents 58 consecutive days of de-identified enterprise events, common computer identifiers across sources, and timestamps sharing an epoch with one-second resolution. The release is from 2015; the actual collection dates are undisclosed. Dataset facts and schemas below follow the official LANL documentation linked above.

| File | Compressed size reported by LANL | Initial use |
|---|---:|---|
| `auth.txt.gz` | 7.2 GB | Candidate events and authentication history |
| `dns.txt.gz` | 177 MB | Supporting DNS history |
| `redteam.txt.gz` | 4.8 KB | Known-positive event matching |

Process and network-flow files are outside the initial scope. Sizes are compressed; intermediate storage requirements must be measured during the pilot.

### Data mirror and checksums

The notebooks download the archives from a Google Drive mirror with gdown, for convenience. The mirror is an unmodified copy of LANL's public release, which LANL has dedicated to the public domain under CC0. LANL remains the authoritative source. Download from the official page above to register your use and to hear about any dataset corrections LANL publishes.

The SHA-256 checksums below were computed on copies downloaded from LANL. Verify a mirrored file before use:

```bash
shasum -a 256 data/raw/*.gz
```

| File | Bytes | SHA-256 |
|---|---:|---|
| `auth.txt.gz` | 7626505158 | `9c6b0cc261b0edd19324f6fd1839743224938a7f644ed202ca70bd70a89bf672` |
| `dns.txt.gz` | 185104940 | `3e1cb718baa6be7af7bd376324285be45d99ee2563d22bc4abf023572120cab9` |
| `redteam.txt.gz` | 4846 | `606635837c684ad11e464075ecf97bc5df325ff7d7f64614d2d8c8af18051669` |

### Authentication schema

```text
time,source_user,destination_user,source_computer,destination_computer,
authentication_type,logon_type,authentication_orientation,result
```

### DNS schema

```text
time,source_computer,resolved_computer
```

### Red-team schema

```text
time,user,source_computer,destination_computer
```

Missing field values are represented by `?`. LANL also notes that failed authentication events are included only for users with at least one successful authentication somewhere in the dataset. Failure-rate features therefore describe this released population, not an unfiltered enterprise authentication stream.

## 4. Unit of Analysis and Label Semantics

The initial scoring unit is an authentication record. Assign a stable row identifier during ingestion.

Match red-team entries to authentication records using timestamp, source user, source computer, and destination computer. Audit match counts, unmatched entries, and one-to-many matches before training. Do not silently substitute destination user if source-user matching fails.

Because the red-team key omits some authentication fields, multiple authentication records may match a single red-team entry. Preserve this ambiguity. Report raw matching-row counts, but evaluate known-positive recall primarily at the unique red-team-key level: a key is recovered if at least one matching authentication record is reviewed. Deduplicate keys before calculating recall.

### 4.1 Label groups

The red-team file lists positive events only; it has no label column and no benign labels. Presence of a matching key is the label. Every authentication record falls into exactly one of three groups:

- **Known positive:** matches a documented red-team compromise key exactly. Reliable when present, but the red-team record is not exhaustive.
- **Possible red-team:** unlabeled, but matches the rule in section 4.2. Likely unrecorded red-team activity; not a confirmed positive.
- **Unlabeled:** everything else. Not certified benign.

Never extend a label by identity alone. A red-team key marks one event, not the user account or computer it involves: those accounts belong to real users who keep using them, and red-team source computers also carry ordinary activity.

### 4.2 Possible red-team rule

An unlabeled authentication record is possible red-team if all of these hold:

- its source computer is a source computer in the red-team file;
- its source user is a user in the red-team file;
- its authentication type is NTLM and its logon type is Network, as in every matched red-team event;
- it falls on a dataset day with at least one red-team key.

The rule uses only facts taken from the red-team file. Do not add behavioral conditions such as login volume, novelty or rate: those are the signals the models score, so a label built from them would make evaluation circular. Freeze the rule before modeling and record any change.

Documented exceptions, also possible red-team:

- Row 897415494 (time 4340961, day 51): `U349@DOM1` from `C17693` to `C3601`, NTLM network logon. It repeats red-team key 81 (day 9) exactly, `U349@DOM1` otherwise never authenticates from `C17693`, and the account's owner was making ordinary Kerberos logons from their usual workstations in the same hour. It fails only the labeled-day condition.

### 4.3 Unmatched red-team keys

A red-team key with no exact authentication match cannot be recovered and is excluded from the recall denominator; report it separately. Where an unmatched key has authentication records that agree on time, user and source computer but differ in destination computer, do not substitute them as known positives. They fall in the possible red-team group when they satisfy section 4.2.

### 4.4 Use in training and evaluation

| Group | Training | Primary evaluation | Sensitivity check |
|---|---|---|---|
| Known positive | Positive class | Counts toward recall | Counts toward recall |
| Possible red-team | Excluded | Not counted | Counts as positive |
| Unlabeled | Negative class (assumed) | Not counted | Not counted |

Models output one score per authentication record; the groups are evaluation bookkeeping, not classes the model predicts. Treating unlabeled records as negative training examples is a noisy-label modeling assumption, not ground truth. An alert on an unlabeled record is not a proven false positive.

## 5. Sampling and Chronological Evaluation

### 5.1 Protocol status

The following is the proposed initial sampling and evaluation protocol, subject to a documented feasibility audit.

### 5.2 Stream first; do not load all raw events into memory

1. Read compressed files in chunks with explicit schemas.
2. Validate timestamp ordering, field counts, and missing-value rates.
3. Preserve stable row identifiers and write typed, time-partitioned Parquet intermediates.
4. Generate causal features from the full observable history in the selected study period.
5. Sample scoring rows only after features are computed. Do not remove history merely because an event is not selected for model fitting.

All raw records may contribute to history, including failures and machine-account activity. Any restriction of scoring eligibility must be specified separately and its coverage reported.

### 5.3 Chronological split

Locked boundaries, using `day_index = floor((time - 1) / 86400) + 1`:

| Period | Dataset days | Role | Matched keys | Possible rows |
|---|---|---|---:|---:|
| Warm-up | 1–5 | Historical state only | 25 | 19 |
| Training | 6–12 | Model fitting | 291 | 340 |
| Validation | 13–20 | Hyperparameters, review policy, and outage settings | 333 | 225 |
| Test | 21–30 | Final evaluation only, scored once | 52 | 33 |
| Alert volume | 31–58 | Positive-free check of daily alert load; no recall | 0 | 1 |

After settings are chosen on validation, refit the final models on days 6–20 with those settings fixed, then score the test period once. Validation results come from models fit on days 6–12 only.

The cuts fall on days without known positives (11–12 and 17–20), so no attack burst is split across periods. Each period is still dominated by one burst: day 9 (261 keys) in training, day 13 (207) in validation, and day 27 (26) in test. Source computer `C17693` accounts for most keys in every period; the test period also has 11 keys from `C19932`. Of the 52 test keys, 46 fall in the DNS full-collection era (days 27–30, section 6). Days 31–58 report daily alert counts and score distributions only; an alert there is not a measured false positive (section 4.4).

The boundaries were chosen from label coverage alone, before any model was fit. Do not change them based on which model scores best; record any change and its reason.

#### Why the provisional boundaries were replaced

The original provisional boundaries were warm-up 1–7, training 8–30, validation 31–42 and test 43–58. Their coverage audit (matched keys and possible red-team rows, section 4):

| Period | Days | Matched keys | Possible rows | Days with keys |
|---|---|---:|---:|---|
| Warm-up | 1–7 | 49 | 184 | 2, 3, 6, 7 |
| Training | 8–30 | 652 | 433 | 8–10, 13–16, 21–23, 27–30 |
| Validation | 31–42 | 0 | 0 | none |
| Test | 43–58 | 0 | 1 | none |

Validation and test contained no known positives, so those boundaries could not measure recall. All known positives fall on days 2–30; 669 of the 701 matched keys come from source computer `C17693`.

Within each evaluation period, historical features may update from earlier observed events, but model parameters remain fixed. Future labels are never used in feature construction.

### 5.4 Training sample

For the first supervised experiment:

- Retain all known-positive training records, provided they exist.
- Use a reproducible, time-stratified reservoir sample of unlabeled training rows.
- Initial engineering cap: 1,000,000 unlabeled rows, with quotas proportional to each training day's eligible event count.
- Save sampling probabilities, row IDs, seeds, counts, and inclusion rules.
- Use inverse-inclusion weights where appropriate and compare weighted and unweighted fits as a sensitivity check.
- Never interpret probabilities from the enriched training sample as calibrated population attack probabilities without additional justification.

The cap is adjustable for hardware, not a scientific constant. Report the final sample and its class composition.

### 5.5 Validation and test populations

Primary review-budget evaluation must score all eligible authentication events in the chosen validation and test periods. Do not retain every positive while downsampling background events and then report the resulting ranking as operational performance.

If full-period scoring is infeasible, choose complete contiguous evaluation windows in advance and disclose their coverage. A smaller sampled evaluation may be used for debugging only; it is not the primary operational result.

## 6. Causal Feature Engineering

For an event at time `t`, use only observations with timestamps strictly less than `t`. Process equal-timestamp records as a batch, scoring before updating state, because within-second ordering is unknown.

Initial candidate windows are 5 minutes, 1 hour, and 24 hours. Select any changes using training/validation only.

### Authentication features

- Previous observation of the source-user/destination-computer relationship.
- Time since the user's last observed authentication to the destination.
- Recent user authentication volume and distinct destination count.
- Recent observed failure proportion.
- Historical frequency of authentication type and logon type for the user.
- Missing-field indicators.

### DNS features

- Whether source computer `s` resolved destination computer `d` within a preceding window.
- Number of observed DNS records for `(s, d)` within that window.
- Time since the last observed matching DNS record.
- ~~Time since any DNS record from source `s`.~~ Excluded; see "Treatment of the DNS break" below.
- Previous observation of the source–destination DNS relationship.

Represent never-observed history separately from a measured elapsed time. Distinguish missing identifiers from a valid pair with zero observed lookups.

A DNS match supplies context, not proof that an authentication used that lookup. DNS silence is not proof of sensor failure. The released DNS schema does not support domain-string, TTL, query-type, or response-code features.

### DNS coverage audit

The DNS release changes scale at day 27. Days 1–23 hold about 45,000–95,000 records a day from 4,348 source computers; day 24 holds 9,610; days 25–26 hold none; days 27–58 hold about 0.6–1.6 million a day from 14,875 sources. Authentication has about 14,500 source computers throughout, so DNS before day 27 covers under a third of them. DNS has no missing identifiers.

Share of authentication events with different source and destination computers that have earlier DNS context:

| Days | Pair resolved within 24 h | Pair resolved within 1 h | Source resolved anything within 1 h |
|---|---:|---:|---:|
| 1–23 | 4.6% | 0.8% | 4.4% |
| 24–26 | 1.4% | 0.0% | 0.2% |
| 27–58 | 67.3% | 19.9% | 86.3% |

No red-team source computer has a DNS record before day 27, so none of the 655 matched known-positive rows on days 2–23, and none of the 585 possible red-team rows there, has any earlier DNS context. Of the 47 matched rows on days 27–30, all have source DNS within 24 hours and 3 have ever resolved their destination.

About 12.3 million authentication rows (1.2%) have a destination that is not a computer identifier, mostly `TGT` (10.9 million, Kerberos ticket requests) and user-like values such as `U22`. These can never match a DNS pair.

### Treatment of the DNS break

Decision: compute DNS features on all days and mark the collection era.

- Every authentication record carries a `dns_full_collection` indicator, true from day 27 (`configs/experiment.yaml`, `dns.full_collection_from_day`). It describes the release, not any host, and is known to an operator in advance, so it is not an outage oracle.
- Drop DNS features that mainly measure whether a host is being collected. "Time since any DNS record from source `s`" is excluded; pair-level features are kept.
- Because no red-team source computer is collected before day 27, a model can learn "no DNS history means attack" from the collection gap alone. Test for this: fit the authentication-only model plus a single indicator of whether the source computer has any earlier DNS record. If that indicator reproduces most of the combined model's gain, report the gain as a collection artifact, not a DNS benefit.
- Report DNS comparisons separately for known positives in the full-collection era (days 27–30, 46 matched keys) and before it.
- Days 25–26, with no DNS at all, are part of the incomplete era, not a simulated outage.

## 7. Controlled DNS-Loss Experiments

Authentication scoring events remain intact. Only supporting DNS observations are suppressed.

Proposed scenarios:

1. Global DNS interruption over contiguous intervals.
2. DNS interruption for a seeded subset of source computers.
3. Longer interruptions than those represented in outage training.

Initial durations: 5 minutes, 1 hour, and 6 hours. Reserve at least one duration or source-subset condition for held-out testing. Choose windows and hosts independently of red-team labels and model performance; repeat across saved seeds and report how many known positives were actually exposed to each outage condition.

Remove DNS records before feature generation and replay the feature-state updates. Masking final feature cells does not reproduce a source outage. Pre-outage history remains available, and history resumes from subsequently observed records after recovery. Compare models on identical authentication rows and identical outage realizations.

Outage schedules are experiment metadata. Do not feed the true synthetic outage flag to the primary model: an operational detector would not automatically know it. A model supplied with verified collector-health flags may be reported separately as an explicitly oracle/instrumented condition.

## 8. Models and Baselines

| Model | Purpose |
|---|---|
| Simple relationship-novelty/rate rules | Tests whether ML adds value beyond basic heuristics |
| Authentication-only model | Supporting-source-independent baseline |
| Complete-telemetry combined model | Measures DNS benefit and vulnerability to source loss |
| Outage-trained combined model | Tests structured-loss training |
| Simple authentication-only fallback | Tests whether added complexity is necessary |

Start with logistic regression and gradient-boosted trees. The gradient-boosting implementation is scikit-learn's `HistGradientBoostingClassifier`: it handles missing values and categorical inputs natively and adds no dependency beyond scikit-learn. Fit preprocessing only on training data. Exclude raw user/computer IDs from the initial model inputs; use them for historical aggregation instead. Document any later identity-encoding experiment separately.

A fallback triggered by a known outage flag is an instrumented/oracle baseline. A fallback based on observed DNS inactivity must use a causal, validation-tuned trigger and acknowledge that inactivity does not diagnose collection failure.

## 9. Evaluation and Statistical Reporting

Primary metric: recall of unique known red-team keys at fixed daily authentication-review budgets. Initial budgets: 100, 500, and 1,000 records per day. These are experimental operating points, not claims about a particular SOC's capacity.

Also report:

- Known-positive recovery versus review budget curves.
- Complete-telemetry versus outage performance on paired events.
- Performance restricted to known positives affected by the outage.
- Daily variation, outage exposure counts, and repeated-seed variability.
- Complete-telemetry penalty from outage training.
- Training/inference time and peak memory where measurable.

Report both total recovered positives divided by total known positives and day-level results, identifying days without known positives. For uncertainty, use paired time-block resampling only where sufficient blocks exist; do not treat millions of correlated events as independent evidence.

Unmatched alerts are not proven false positives. Avoid claims of true false-positive rate, true precision, or calibrated attack probability from these labels alone. State clearly when any exploratory classification metric treats unlabeled examples as negative.

## 10. Notebook Implementation Plan

```text
notebooks/
  01_data_audit.ipynb
  02_causal_features_and_sampling.ipynb
  03_baseline_models.ipynb
  04_dns_outage_replay.ipynb
  05_evaluation_and_tables.ipynb
src/
  ingestion.py
  features.py
  sampling.py
  outages.py
  evaluation.py
configs/
  experiment.yaml
data/
  raw/          # local files; excluded from version control
  interim/      # derived partitions; excluded from version control
  processed/    # feature tables; excluded from version control
results/
  metrics/
  tables/
  figures/
```

Notebooks provide the executable narrative; reusable streaming and state-management code belongs in small imported modules. This avoids duplicating complex logic across notebooks.

Suggested starting stack: Python, JupyterLab, pandas or Polars, PyArrow, scikit-learn, and one gradient-boosting implementation. Package versions are pinned in `requirements.txt`.

Every experiment should save configuration, seeds, data hashes or file sizes, split boundaries, sampling manifests, model settings, and metric tables. Notebooks must run in order from a clean kernel without hidden state. Derived data and downloaded archives should not be committed to Git.

## 11. Feasibility Gates

Before extensive modeling:

1. Confirm download, parsing, storage, and full-history processing feasibility.
2. Validate red-team matching and positive coverage across proposed splits.
3. Quantify DNS pair coverage and missing identifiers.
4. Establish an authentication-only baseline.
5. Determine whether DNS improves ranking under complete telemetry.

Proceed with a DNS-resilience contribution only if the signal and evaluation support it. If DNS adds no meaningful benefit, report that finding or revise the source/question; do not force an outage-trained model to justify the project name.

## 12. Threats to Validity and Research Boundaries

- One organization and a historical release limit generalizability.
- Red-team compromise labels are sparse and incomplete; attacks are not independent trials merely because many events exist.
- Simulated outages do not establish behavior during real collection failures or adversarial suppression.
- Collection gaps, DNS caching, and ordinary inactivity cannot be separated from the published DNS fields alone.
- Chronological drift and uneven positive coverage can affect comparisons.
- Experimental outage durations and review budgets require sensitivity analysis.
- No claim of methodological novelty is made until focused related-work review and experiments establish differentiation.

Use de-identified data responsibly. Do not attempt re-identification or automatic access revocation. KnightGlass is an analyst-prioritization study, not an authorization decision system.

## 13. Expected Deliverables

- Reproducible notebooks and supporting modules.
- Audited data and label-matching summary.
- Versioned sampling and outage specifications.
- Authentication-only and combined-source baselines.
- Paired resilience experiments and review-budget tables.

## 14. Dataset Attribution and Publication Citations

LANL requests citation of both the source-description chapter and the dataset.

A. D. Kent, "Cybersecurity Data Sources for Dynamic Network Research," in Dynamic Networks in Cybersecurity, Imperial College Press, 2015.

```bibtex
@InProceedings{akent-2015-enterprise-data,
   author = {Alexander D. Kent},
   title = {{Cybersecurity Data Sources for Dynamic Network Research}},
   year = 2015,
   booktitle = {Dynamic Networks in Cybersecurity},
   month = jun,
   publisher = {Imperial College Press}
}
```

A. D. Kent, "Comprehensive, Multi-Source Cybersecurity Events," Los Alamos National Laboratory, 2015. DOI: 10.17021/1179829.

```bibtex
@Misc{kent-2015-cyberdata1,
  author = {Alexander D. Kent},
  title = {{Comprehensive, Multi-Source Cyber-Security Events}},
  year = {2015},
  howpublished = {Los Alamos National Laboratory},
  doi = {10.17021/1179829}
}
```

LANL states that, to the extent possible under law, it has waived copyright and related or neighboring rights to this dataset. KnightGlass code is released under the MIT License (see `LICENSE`). That license is separate from the dataset's terms.

## TODO

### Phase 0 - Setup

- [x] Scaffold notebooks, modules, config and ignore rules
- [x] MIT license
- [x] Google Drive mirror with checksums for all three archives
- [x] gdown download step, verified against checksums
- [x] Pin package versions in `requirements.txt`

### Phase 1 - Data audit

- [x] Streaming ingestion to day-partitioned Parquet
- [x] Ingestion tests on synthetic data
- [x] Red-team matching with duplicate, unmatched and one-to-many audit
- [x] Ingest the full authentication archive
- [x] Run the full red-team matching audit
- [x] Investigate unlabeled red-team-account NTLM logons from C17693 on day 51
- [x] Audit known-positive coverage across the proposed splits
- [x] Quantify DNS pair coverage and missing identifiers
- [x] Decide how to treat the DNS break around days 24 to 27
- [x] Lock chronological split boundaries

### Phase 2 - Causal features and sampling

- [x] Authentication history features
- [x] DNS context features
- [x] Equal-timestamp batch scoring before state updates
- [ ] Time-stratified reservoir sample of unlabeled training rows
- [ ] Sampling manifest with probabilities, row IDs and seeds

### Phase 3 - Baselines

- [ ] Relationship-novelty and rate rule baselines
- [ ] Authentication-only model
- [ ] Complete-telemetry combined model
- [x] Choose a gradient-boosting library
- [ ] Decide whether DNS improves ranking under complete telemetry

### Phase 4 - DNS outage replay

- [ ] Seeded outage schedules for each scenario
- [ ] DNS suppression with feature-state replay
- [ ] Outage-trained combined model
- [ ] Causal-trigger fallback and oracle fallback baselines
- [ ] Held-out outage conditions

### Phase 5 - Evaluation

- [ ] Recall of unique red-team keys at daily review budgets
- [ ] Recovery versus review budget curves
- [ ] Paired complete versus outage comparisons
- [ ] Time-block resampling for uncertainty
- [ ] Tables and figures
