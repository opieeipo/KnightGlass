# NightGlass
## Authentication Threat Detection Under Incomplete DNS Telemetry

> **Status:** research proposal and notebook implementation plan. No experimental results or novelty claims have yet been established.

## Abstract

NightGlass investigates whether DNS context improves the ranking of suspicious authentication events and whether that improvement survives interruptions in DNS collection. The study uses the LANL Comprehensive, Multi-Source Cyber-Security Events dataset. Authentication events remain observable; supporting DNS events are selectively withheld in controlled experiments. Authentication-only, combined-source, and outage-trained models are compared using chronological evaluation and fixed analyst review budgets. Known red-team authentication events provide positive evaluation examples; other events remain unlabeled rather than verified benign. The intended contribution is an experimentally supported assessment of when DNS context is useful and whether structured-outage training provides operational value beyond a simpler authentication-only fallback.

## 1. Motivation and Scope

A detector that depends on supporting telemetry should not become less useful than a simpler detector when that source disappears. NightGlass asks whether additional DNS context is worth its operational dependency.

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

Use label language consistently:

- Known positive: matches a documented red-team compromise key.
- Unlabeled: does not match such a key; not certified benign.

For supervised baselines, any treatment of unlabeled records as negative training examples is a noisy-label modeling assumption, not ground truth.

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

Provisional boundaries, using `day_index = floor((time - 1) / 86400) + 1`:

| Period | Dataset days | Role |
|---|---|---|
| Warm-up | 1–7 | Historical state only |
| Training | 8–30 | Model fitting |
| Validation | 31–42 | Hyperparameters, review policy, and outage settings |
| Test | 43–58 | Final evaluation only |

Audit known-positive coverage before locking these boundaries. These are provisional engineering defaults, not a claim that positives occur in every interval. If test coverage is insufficient, select a documented alternative chronological or rolling-origin design. Do not select boundaries based on which model scores best. If supervised training positives are insufficient, use an anomaly-ranking formulation rather than borrowing future labels.

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
- Time since any DNS record from source `s`.
- Previous observation of the source–destination DNS relationship.

Represent never-observed history separately from a measured elapsed time. Distinguish missing identifiers from a valid pair with zero observed lookups.

A DNS match supplies context, not proof that an authentication used that lookup. DNS silence is not proof of sensor failure. The released DNS schema does not support domain-string, TTL, query-type, or response-code features.

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

Start with logistic regression and gradient-boosted trees. Fit preprocessing only on training data. Exclude raw user/computer IDs from the initial model inputs; use them for historical aggregation instead. Document any later identity-encoding experiment separately.

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
  05_evaluation_and_paper_tables.ipynb
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

Suggested starting stack: Python, JupyterLab, pandas or Polars, PyArrow, scikit-learn, and one gradient-boosting implementation. Pin package versions once a working environment is established.

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

Use de-identified data responsibly. Do not attempt re-identification or automatic access revocation. NightGlass is an analyst-prioritization study, not an authorization decision system.

## 13. Expected Deliverables

- Reproducible notebooks and supporting modules.
- Audited data and label-matching summary.
- Versioned sampling and outage specifications.
- Authentication-only and combined-source baselines.
- Paired resilience experiments and review-budget tables.
- A paper draft reporting positive, negative, and inconclusive findings.

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

LANL states that, to the extent possible under law, it has waived copyright and related or neighboring rights to this dataset. NightGlass code is released under the MIT License (see `LICENSE`). That license is separate from the dataset's terms.
