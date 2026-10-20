"""Training-sample construction. See README section 5.4.

Sampling happens after features are computed. Every sample saves its row IDs,
inclusion probabilities, seed, and counts.
"""

import json
from pathlib import Path

import numpy as np
import polars as pl


def day_quotas(eligible, cap):
    """Split ``cap`` across days in proportion to ``eligible`` counts.

    Uses largest remainders so the quotas sum exactly to
    ``min(cap, total)``, and never exceeds a day's eligible count.
    """
    total = sum(eligible.values())
    cap = min(cap, total)
    exact = {d: cap * n / total for d, n in eligible.items()}
    quotas = {d: int(q) for d, q in exact.items()}
    by_remainder = sorted(eligible, key=lambda d: (exact[d] - quotas[d], -d), reverse=True)
    for d in by_remainder[:cap - sum(quotas.values())]:
        quotas[d] += 1
    return quotas


def time_stratified_reservoir(feature_dir, days, cap, seed, out_dir=None, name="sample"):
    """Sample unlabeled training rows with per-day quotas, keeping all known
    positives. Possible red-team rows are excluded (README section 4.4).

    Each day's quota is proportional to its unlabeled row count, and rows
    are drawn uniformly without replacement with a generator seeded by
    ``(seed, day)``. This gives the same inclusion probabilities as a
    per-day reservoir. ``days`` is an inclusive ``[first, last]``. Returns
    ``(sample, manifest)``; ``sample`` has ``row_id``, ``day``, ``label``,
    ``inclusion_probability`` and ``weight`` (its inverse).
    """
    first, last = days
    rows = (pl.scan_parquet(Path(feature_dir) / "day=*" / "*.parquet", hive_partitioning=True)
            .filter(pl.col("day").is_between(first, last))
            .select("row_id", "day", "label"))
    counts = rows.group_by("day", "label").len().collect()
    eligible = {d: n for d, n in counts.filter(pl.col("label") == "unlabeled")
                .select("day", "len").sort("day").iter_rows()}
    quotas = day_quotas(eligible, cap)

    # One day at a time keeps memory to a single day's row IDs.
    parts = []
    for day, quota in quotas.items():
        ids = (rows.filter((pl.col("day") == day) & (pl.col("label") == "unlabeled"))
               .select("row_id").collect().to_series().to_numpy())
        chosen = np.random.default_rng([seed, day]).choice(ids, size=quota, replace=False)
        parts.append(pl.DataFrame({"row_id": np.sort(chosen), "day": day, "label": "unlabeled",
                                   "inclusion_probability": quota / len(ids)}))
    positives = rows.filter(pl.col("label") == "known_positive").collect().with_columns(
        pl.lit(1.0).alias("inclusion_probability"))
    possible = counts.filter(pl.col("label") == "possible")["len"].sum()
    sample = (pl.concat([positives, *[p.cast(positives.schema) for p in parts]])
              .with_columns((1 / pl.col("inclusion_probability")).alias("weight"))
              .sort("row_id"))

    manifest = {
        "name": name,
        "days": [first, last],
        "seed": seed,
        "unlabeled_cap": cap,
        "eligible": "every unlabeled authentication row; possible red-team rows excluded",
        "unlabeled_rows_by_day": {str(d): n for d, n in eligible.items()},
        "quotas_by_day": {str(d): q for d, q in quotas.items()},
        "inclusion_probability_by_day": {str(d): quotas[d] / eligible[d] for d in quotas},
        "known_positive_rows": positives.height,
        "possible_rows_excluded": possible,
        "sampled_unlabeled_rows": sum(quotas.values()),
        "total_rows": sample.height,
    }
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        sample.write_parquet(out_dir / f"{name}.parquet")
        (out_dir / f"{name}_manifest.json").write_text(json.dumps(manifest, indent=2))
    return sample, manifest
