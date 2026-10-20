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


MISSING = "?"

AUTH_EVENT_COLUMNS = ["row_id", "time", "source_user", "source_computer", "destination_computer",
                      "authentication_type", "logon_type", "authentication_orientation", "result"]

# Event attributes passed through for the models to encode. Raw user and
# computer IDs are deliberately absent (README section 8).
CATEGORICAL_FEATURES = ["authentication_type", "logon_type", "authentication_orientation", "result"]


def auth_feature_names(windows_seconds):
    names = ["ud_seen", "ud_gap", "user_prior_events", "user_auth_type_freq",
             "user_logon_type_freq", "auth_type_missing", "logon_type_missing", "is_self",
             "dest_is_computer"]
    for w in windows_seconds:
        names += [f"user_events_{w}", f"user_distinct_dest_{w}", f"user_fail_rate_{w}"]
    return names


def dns_feature_names(windows_seconds):
    # "Time since any DNS record from source s" is excluded because it mainly
    # measures whether the host is collected (README section 6, DNS break).
    return (["dns_pair_seen", "dns_pair_gap", "dns_full_collection"]
            + [f"dns_pair_count_{w}" for w in windows_seconds])


# Diagnostics are stored with the features but never used as model inputs.
DIAGNOSTICS = ["diag_source_dns_seen"]


def count_before(frame, points, by, at, name):
    """Add ``name``: the summed ``weight`` of ``points`` strictly before ``at``.

    ``frame`` must be sorted so that the ``at`` expression is non-decreasing.
    ``points`` has the ``by`` columns, ``time`` and ``weight``. Groups with no
    earlier point get 0.
    """
    cum = (points.group_by(*by, "time").agg(pl.col("weight").sum())
           .sort("time")
           .with_columns(pl.col("weight").cum_sum().over(by).alias(name))
           .select(*by, pl.col("time").alias("_at"), name))
    return (frame.with_columns(at.alias("_q"))
            .join_asof(cum, left_on="_q", right_on="_at", by=by, strategy="backward",
                       allow_exact_matches=False, coalesce=False, check_sortedness=False)
            .with_columns(pl.col(name).fill_null(0))
            .drop("_q", "_at"))


def empty_auth_state():
    return {
        "pair_last": pl.DataFrame(schema={"source_user": pl.String,
                                          "destination_computer": pl.String, "time": pl.Int64}),
        "type_counts": pl.DataFrame(schema={"source_user": pl.String, "_atype": pl.String,
                                            "weight": pl.Int64}),
        "logon_counts": pl.DataFrame(schema={"source_user": pl.String, "_ltype": pl.String,
                                             "weight": pl.Int64}),
        "user_counts": pl.DataFrame(schema={"source_user": pl.String, "weight": pl.Int64}),
    }


def _with_types(events):
    return events.with_columns(pl.col("authentication_type").fill_null(MISSING).alias("_atype"),
                               pl.col("logon_type").fill_null(MISSING).alias("_ltype"))


def build_auth_features(today, recent, state, windows_seconds):
    """Authentication history features for one day of events. README section 6.

    ``today`` and ``recent`` (the previous day, for windows up to 24 hours)
    are DataFrames of ``AUTH_EVENT_COLUMNS`` sorted by time. ``state`` holds
    all-history summaries from earlier days (see ``empty_auth_state``).
    Returns ``(features, new_state)``; ``features`` keeps ``today``'s row order.
    """
    if max(windows_seconds) > 86400:
        raise ValueError("windows longer than one day need more than the previous day")
    ev = _with_types(today).lazy()
    window_events = pl.concat([recent, today]).lazy()
    user = ["source_user"]

    # Volume and failure counts in [t - w, t).
    volume = window_events.select(*user, "time", pl.lit(1).alias("weight"))
    fails = window_events.select(*user, "time",
                                 (pl.col("result") == "Fail").cast(pl.Int64).alias("weight"))
    ev = count_before(ev, volume, user, pl.col("time"), "_n")
    ev = count_before(ev, fails, user, pl.col("time"), "_f")

    # Distinct destinations in [t - w, t): an occurrence of a (user,
    # destination) pair at t' counts for queries t in (t', min(next, t' + w)],
    # where next is the pair's next occurrence. So the count at t is the
    # number of interval starts before t minus the number of ends before t.
    pair = ["source_user", "destination_computer"]
    occurrences = (window_events.select(*pair, "time").unique().sort("time")
                   .with_columns(pl.col("time").shift(-1).over(pair).alias("_next")))
    starts = occurrences.select(*user, "time", pl.lit(1).alias("weight"))
    ev = count_before(ev, starts, user, pl.col("time"), "_s")

    for w in windows_seconds:
        ev = count_before(ev, volume, user, pl.col("time") - w, f"_n{w}")
        ev = count_before(ev, fails, user, pl.col("time") - w, f"_f{w}")
        ends = occurrences.select(
            *user, pl.min_horizontal(pl.col("_next"), pl.col("time") + w).alias("time"),
            pl.lit(1).alias("weight"))
        ev = count_before(ev, ends, user, pl.col("time"), f"_e{w}")
        n = pl.col("_n") - pl.col(f"_n{w}")
        ev = ev.with_columns(
            n.alias(f"user_events_{w}"),
            (pl.col("_s") - pl.col(f"_e{w}")).alias(f"user_distinct_dest_{w}"),
            pl.when(n > 0).then((pl.col("_f") - pl.col(f"_f{w}")) / n).alias(f"user_fail_rate_{w}"),
        )

    # All-history features: earlier days come from state, kept at time 0.
    today_typed = _with_types(today).lazy()
    pair_points = pl.concat([state["pair_last"].lazy(), today_typed.select(*pair, "time")])
    last = pair_points.sort("time").select(pl.col("time").alias("_ud_last"), *pair)
    ev = ev.join_asof(last, left_on="time", right_on="_ud_last", by=pair, strategy="backward",
                      allow_exact_matches=False, coalesce=False, check_sortedness=False)

    def history(counts, keys):
        return pl.concat([counts.lazy().select(*keys, pl.lit(0, pl.Int64).alias("time"), "weight"),
                          today_typed.select(*keys, "time", pl.lit(1, pl.Int64).alias("weight"))])

    ev = count_before(ev, history(state["user_counts"], user), user, pl.col("time"), "_u")
    ev = count_before(ev, history(state["type_counts"], [*user, "_atype"]), [*user, "_atype"],
                      pl.col("time"), "_ua")
    ev = count_before(ev, history(state["logon_counts"], [*user, "_ltype"]), [*user, "_ltype"],
                      pl.col("time"), "_ul")
    ev = ev.with_columns(
        pl.col("_ud_last").is_not_null().alias("ud_seen"),
        (pl.col("time") - pl.col("_ud_last")).alias("ud_gap"),
        pl.col("_u").alias("user_prior_events"),
        pl.when(pl.col("_u") > 0).then(pl.col("_ua") / pl.col("_u")).alias("user_auth_type_freq"),
        pl.when(pl.col("_u") > 0).then(pl.col("_ul") / pl.col("_u")).alias("user_logon_type_freq"),
        pl.col("authentication_type").is_null().alias("auth_type_missing"),
        pl.col("logon_type").is_null().alias("logon_type_missing"),
        (pl.col("source_computer") == pl.col("destination_computer")).alias("is_self"),
        pl.col("destination_computer").str.contains(r"^C\d+$").alias("dest_is_computer"),
    )
    features = ev.select("row_id", "time", *CATEGORICAL_FEATURES,
                         *auth_feature_names(windows_seconds)).collect()

    today_typed = today_typed.collect()
    new_state = {
        "pair_last": pl.concat([state["pair_last"], today_typed.select(*pair, "time")])
        .group_by(pair).agg(pl.col("time").max()),
        "type_counts": _add_counts(state["type_counts"], today_typed, [*user, "_atype"]),
        "logon_counts": _add_counts(state["logon_counts"], today_typed, [*user, "_ltype"]),
        "user_counts": _add_counts(state["user_counts"], today_typed, user),
    }
    return features, new_state


def _add_counts(counts, events, keys):
    return (pl.concat([counts, events.group_by(keys).agg(pl.len().cast(pl.Int64).alias("weight"))])
            .group_by(keys).agg(pl.col("weight").sum()))


def build_dns_features(today, dns_history, dns_recent, windows_seconds, full_collection):
    """Pair-level DNS context features for one day of events. README section 6.

    ``dns_history`` holds every DNS record up to the end of the day and
    ``dns_recent`` the records from the previous day and this day, both
    sorted by time. ``full_collection`` is the day's ``dns_full_collection``
    value. Never-observed pairs get a null gap and ``dns_pair_seen`` false.
    """
    pair = ["source_computer", "destination_computer"]
    ev = last_dns_before(today.lazy().select("row_id", "time", *pair), dns_history.lazy())
    points = dns_recent.lazy().select(
        "source_computer", pl.col("resolved_computer").alias("destination_computer"),
        "time", pl.lit(1).alias("weight"))
    ev = count_before(ev, points, pair, pl.col("time"), "_d")
    for w in windows_seconds:
        ev = count_before(ev, points, pair, pl.col("time") - w, f"_d{w}")
        ev = ev.with_columns((pl.col("_d") - pl.col(f"_d{w}")).alias(f"dns_pair_count_{w}"))
    ev = ev.with_columns(
        pl.col("last_pair_dns").is_not_null().alias("dns_pair_seen"),
        (pl.col("time") - pl.col("last_pair_dns")).alias("dns_pair_gap"),
        pl.lit(full_collection).alias("dns_full_collection"),
        pl.col("last_source_dns").is_not_null().alias("diag_source_dns_seen"),
    )
    return ev.select("row_id", *dns_feature_names(windows_seconds), *DIAGNOSTICS).collect()


def label_rows(matches, possible):
    """Row-level label groups (README section 4.1): ``label`` is
    ``known_positive``, ``possible`` or ``unlabeled``; ``key_id`` is set for
    known positives."""
    known = pl.from_arrow(matches).select("row_id", "key_id").with_columns(
        pl.lit("known_positive").alias("label"))
    maybe = pl.from_arrow(possible).select("row_id", pl.lit(None, pl.Int64).alias("key_id"),
                                           pl.lit("possible").alias("label"))
    return pl.concat([known, maybe])


def build_features(auth_dir, dns_dir, labels, out_dir, windows_seconds,
                   full_collection_from_day, first_day, progress=True):
    """Write causal features for every authentication event from ``first_day``.

    History starts at day 1; earlier days only update state. ``labels`` is
    the output of ``label_rows``. Writes ``<out_dir>/day=NN/part-0.parquet``
    and a ``_manifest.json``; returns the manifest.
    """
    import json
    import time as clock

    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir} is not empty; remove it to rebuild.")
    out_dir.mkdir(parents=True, exist_ok=True)

    auth, dns = scan_days(auth_dir), scan_days(dns_dir)
    days = auth.select(pl.col("day").unique().sort()).collect().to_series().to_list()
    state = empty_auth_state()
    recent = pl.DataFrame(schema=auth.select(AUTH_EVENT_COLUMNS).collect_schema())
    rows_per_day, started = {}, clock.time()

    for day in days:
        today = auth.filter(pl.col("day") == day).select(AUTH_EVENT_COLUMNS).sort("row_id").collect()
        features, state = build_auth_features(today, recent, state, windows_seconds)
        if day >= first_day:
            dns_history = dns.filter(pl.col("day") <= day).drop("day").collect()
            dns_recent = dns_history.filter(pl.col("time") > (day - 2) * 86400)
            dns_features = build_dns_features(today, dns_history, dns_recent, windows_seconds,
                                              day >= full_collection_from_day)
            table = (features.join(dns_features, on="row_id", how="left", maintain_order="left")
                     .join(labels, on="row_id", how="left", maintain_order="left")
                     .with_columns(pl.col("label").fill_null("unlabeled"),
                                   pl.lit(day, pl.Int32).alias("day")))
            part = out_dir / f"day={day:02d}"
            part.mkdir()
            table.write_parquet(part / "part-0.parquet", compression="zstd")
            rows_per_day[str(day)] = table.height
        recent = today
        if progress:
            print(f"features: day {day} done, {clock.time() - started:.0f}s elapsed", flush=True)

    manifest = {
        "windows_seconds": list(windows_seconds),
        "full_collection_from_day": full_collection_from_day,
        "first_day": first_day,
        "categorical_features": CATEGORICAL_FEATURES,
        "auth_features": auth_feature_names(windows_seconds),
        "dns_features": dns_feature_names(windows_seconds),
        "diagnostics": DIAGNOSTICS,
        "rows_per_day": rows_per_day,
        "seconds": round(clock.time() - started),
    }
    (out_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
