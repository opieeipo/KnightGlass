"""Review-budget evaluation. See README section 9.

The primary metric is recall of unique known red-team keys at fixed daily
review budgets. Unlabeled rows are not treated as verified benign.
"""

from collections import Counter

from knightglass.ingestion import day_index


def recall_at_daily_budget(scores, keys, budget):
    """Fraction of unique red-team keys with at least one matching row inside
    each day's top-`budget` scored authentication records."""
    raise NotImplementedError


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
