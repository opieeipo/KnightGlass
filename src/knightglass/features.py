"""Causal feature state. See README section 6.

Events at time t see only observations with timestamps strictly less than t.
Equal-timestamp records are scored as a batch before state is updated.
"""

from pathlib import Path

import polars as pl


def scan_days(data_dir):
    """Lazy scan of a day-partitioned Parquet directory written by ingestion."""
    return pl.scan_parquet(Path(data_dir) / "day=*" / "*.parquet", hive_partitioning=True)


def last_dns_before(auth, dns):
    """Add the time of the most recent DNS record strictly before each event.

    ``auth`` needs ``time``, ``source_computer`` and ``destination_computer``;
    ``dns`` needs ``time``, ``source_computer`` and ``resolved_computer``.
    Both must be sorted by time. Adds ``last_pair_dns`` (source resolved this
    destination) and ``last_source_dns`` (source resolved anything); null
    means never observed before the event.
    """
    pair = dns.select(
        pl.col("time").alias("last_pair_dns"),
        pl.col("source_computer"),
        pl.col("resolved_computer").alias("destination_computer"),
    )
    source = dns.select(pl.col("time").alias("last_source_dns"), pl.col("source_computer"))
    # Ingestion guarantees time order; Polars cannot verify it within groups.
    strictly_before = dict(strategy="backward", allow_exact_matches=False, coalesce=False,
                           check_sortedness=False)
    out = auth.join_asof(pair, left_on="time", right_on="last_pair_dns",
                         by=["source_computer", "destination_computer"], **strictly_before)
    return out.join_asof(source, left_on="time", right_on="last_source_dns",
                         by="source_computer", **strictly_before)


def dns_pair_coverage(auth_dir, dns_dir, windows_seconds, flagged=None):
    """How often authentication events have prior DNS context, per day.

    For every authentication event, finds the latest earlier DNS record for
    its (source, destination) pair and for its source. Returns
    ``(by_day, flagged_rows)``: ``by_day`` counts events, events with a
    different source and destination, and how many of those have pair DNS
    ever before and within each window, and source DNS within each window.
    ``flagged`` maps a group name to a collection of row IDs (for example
    known positives); their per-row results are returned in ``flagged_rows``.
    """
    auth = scan_days(auth_dir).select(
        "row_id", "time", "day", "source_computer", "destination_computer")
    dns = scan_days(dns_dir).select("time", "day", "source_computer", "resolved_computer")
    groups = {name: pl.Series(sorted(ids), dtype=pl.Int64) for name, ids in (flagged or {}).items()}

    days = auth.select(pl.col("day").unique().sort()).collect().to_series().to_list()
    by_day, rows = [], []
    for day in days:
        events = last_dns_before(auth.filter(pl.col("day") == day),
                                 dns.filter(pl.col("day") <= day).drop("day")).collect()
        nonself = pl.col("source_computer") != pl.col("destination_computer")
        gap_pair = pl.col("time") - pl.col("last_pair_dns")
        gap_source = pl.col("time") - pl.col("last_source_dns")
        stats = [pl.len().alias("events"), nonself.sum().alias("nonself_events"),
                 (nonself & pl.col("last_pair_dns").is_not_null()).sum().alias("pair_ever")]
        stats += [(nonself & (gap_pair <= w)).sum().alias(f"pair_within_{w}") for w in windows_seconds]
        stats += [(nonself & (gap_source <= w)).sum().alias(f"source_within_{w}")
                  for w in windows_seconds]
        by_day.append(events.select(stats).with_columns(pl.lit(day).alias("day")))
        for name, ids in groups.items():
            rows.append(events.filter(pl.col("row_id").is_in(ids.implode()))
                        .with_columns(pl.lit(name).alias("group")))

    by_day = pl.concat(by_day).select("day", pl.exclude("day"))
    rows = pl.concat(rows) if rows else None
    return by_day, rows


def build_auth_features(auth_dir, windows_seconds):
    """Relationship novelty, recency, volume, failure-rate, and type-frequency
    features from authentication history."""
    raise NotImplementedError


def build_dns_features(auth_dir, dns_dir, windows_seconds):
    """Source/destination DNS context features. Never-observed history is
    represented separately from a measured elapsed time."""
    raise NotImplementedError
