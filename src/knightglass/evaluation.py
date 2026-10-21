"""Review-budget evaluation. See README section 9.

The primary metric is recall of unique known red-team keys at fixed daily
review budgets. Unlabeled rows are not treated as verified benign.
"""

from collections import Counter

import polars as pl

from knightglass.ingestion import day_index


def daily_ranks(scored, seed, keep_top):
    """Rank rows within each day by descending score; 1 is reviewed first.

    Ties are broken by a hash of ``row_id`` seeded with ``seed``, so a
    constant-score model gets chance-level recall instead of file order.
    ``scored`` needs ``row_id``, ``day``, ``score``, ``label`` and
    ``key_id``. Keeps the top ``keep_top`` rows of each day plus every
    labeled row, which is all that budget recall needs.
    """
    return (scored.with_columns(pl.col("row_id").hash(seed).alias("_tie"))
            .sort(["day", "score", "_tie"], descending=[False, True, False])
            .with_columns(pl.int_range(1, pl.len() + 1).over("day").alias("rank"))
            .filter((pl.col("rank") <= keep_top) | (pl.col("label") != "unlabeled"))
            .drop("_tie"))


def recall_at_daily_budget(ranked, budget, include_possible=False):
    """Recall of unique known red-team keys within each day's top ``budget``.

    A key is recovered if at least one of its rows is reviewed (README
    section 4). With ``include_possible``, each possible red-team row also
    counts as one positive unit (the section 4.4 sensitivity check).
    Returns totals and per-day counts.
    """
    known = ranked.filter(pl.col("label") == "known_positive")
    keys = known.group_by("key_id").agg(pl.col("day").first(), (pl.col("rank") <= budget).any()
                                        .alias("hit"))
    units = keys.select("day", "hit")
    if include_possible:
        units = pl.concat([units, ranked.filter(pl.col("label") == "possible")
                           .select("day", (pl.col("rank") <= budget).alias("hit"))])
    by_day = units.group_by("day").agg(pl.len().alias("positives"),
                                       pl.col("hit").sum().alias("recovered")).sort("day")
    total, hit = units.height, int(units["hit"].sum())
    return {"budget": budget, "positives": total, "recovered": hit,
            "recall": hit / total if total else None,
            "by_day": {d: (r, p) for d, p, r in by_day.iter_rows()}}


def split_of(day, splits):
    """Name of the split containing ``day``, or None if it is in none."""
    for name, (first, last) in splits.items():
        if first <= day <= last:
            return name
    return None


def split_coverage(keys, matches, possible, splits):
    """Known-positive and possible red-team coverage for each split.

    ``splits`` maps a split name to an inclusive ``[first_day, last_day]``.
    Only matched keys count toward recall (README section 4.3), so unmatched
    keys are reported separately. Days outside every split go under
    ``"outside"``.
    """
    matched = set(matches.column("key_id").to_pylist())
    names = list(splits) + ["outside"]
    out = {n: {"days": None, "matched_keys": 0, "unmatched_keys": 0,
               "known_positive_rows": 0, "possible_rows": 0,
               "days_with_keys": set(), "keys_by_source": Counter()} for n in names}
    for n, (first, last) in splits.items():
        out[n]["days"] = [first, last]

    for key_id, time, source in zip(keys.column("key_id").to_pylist(),
                                    keys.column("time").to_pylist(),
                                    keys.column("source_computer").to_pylist()):
        day = day_index(time)
        row = out[split_of(day, splits) or "outside"]
        if key_id in matched:
            row["matched_keys"] += 1
            row["days_with_keys"].add(day)
            row["keys_by_source"][source] += 1
        else:
            row["unmatched_keys"] += 1

    for time in matches.column("time").to_pylist():
        out[split_of(day_index(time), splits) or "outside"]["known_positive_rows"] += 1
    for day in possible.column("day").to_pylist():
        out[split_of(day, splits) or "outside"]["possible_rows"] += 1

    for row in out.values():
        row["days_with_keys"] = sorted(row["days_with_keys"])
        row["keys_by_source"] = dict(row["keys_by_source"].most_common())
    return out
