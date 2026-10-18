"""Chunked, schema-explicit reading of the LANL archives into Parquet.

See README sections 3, 4 and 5.2.

Each source is streamed from its gzip archive in blocks, never loaded whole.
Rows keep their file order and get a stable ``row_id`` (0-based line order
among valid rows). Output is partitioned by dataset day as
``<out_dir>/day=NN/part-0.parquet``, and a JSON manifest records counts,
missing-value rates, ordering checks and the input file size.
"""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.dataset as ds
import pyarrow.parquet as pq

AUTH_COLUMNS = [
    "time", "source_user", "destination_user", "source_computer",
    "destination_computer", "authentication_type", "logon_type",
    "authentication_orientation", "result",
]
DNS_COLUMNS = ["time", "source_computer", "resolved_computer"]
REDTEAM_COLUMNS = ["time", "user", "source_computer", "destination_computer"]

SCHEMAS = {
    "auth": AUTH_COLUMNS,
    "dns": DNS_COLUMNS,
    "redteam": REDTEAM_COLUMNS,
}

MISSING_VALUE = "?"
SECONDS_PER_DAY = 86400
REDTEAM_KEY = ["time", "user", "source_computer", "destination_computer"]


def day_index(time):
    """Dataset day for a timestamp: floor((time - 1) / 86400) + 1."""
    return (time - 1) // SECONDS_PER_DAY + 1


def _arrow_schema(columns):
    return pa.schema(
        [pa.field("time", pa.int64(), nullable=False)]
        + [pa.field(c, pa.string()) for c in columns if c != "time"]
    )


def open_source(source_path, columns, block_size=64 << 20, on_invalid=None):
    """Return a streaming reader over a headerless LANL CSV (gzip or plain).

    ``?`` is read as null, so missing identifiers are distinct from any real
    value. ``on_invalid`` receives each malformed row and decides whether to
    skip it; by default malformed rows raise.
    """
    schema = _arrow_schema(columns)
    return pacsv.open_csv(
        source_path,
        read_options=pacsv.ReadOptions(column_names=columns, block_size=block_size),
        parse_options=pacsv.ParseOptions(invalid_row_handler=on_invalid),
        convert_options=pacsv.ConvertOptions(
            column_types=schema,
            null_values=[MISSING_VALUE],
            strings_can_be_null=True,
            quoted_strings_can_be_null=False,
        ),
    )


def stream_to_parquet(source_path, columns, out_dir, block_size=64 << 20,
                      max_invalid=0, progress_every=100_000_000):
    """Stream a LANL archive into day-partitioned Parquet with stable row IDs.

    Raises if timestamps ever decrease, because day partitions and causal
    replay both depend on file order being chronological. Malformed rows are
    skipped and counted up to ``max_invalid``; beyond that ingestion fails.
    Returns the manifest, which is also written to ``<out_dir>/_manifest.json``.
    """
    source_path = Path(source_path)
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir} is not empty; remove it to re-ingest.")
    out_dir.mkdir(parents=True, exist_ok=True)

    invalid = []

    def on_invalid(row):
        invalid.append(row.text[:200])
        return "skip" if len(invalid) <= max_invalid else "error"

    reader = open_source(source_path, columns, block_size, on_invalid)
    out_schema = pa.schema([pa.field("row_id", pa.int64(), nullable=False)]).append(
        pa.field("time", pa.int64(), nullable=False)
    )
    for c in columns[1:]:
        out_schema = out_schema.append(pa.field(c, pa.string()))

    null_counts = {c: 0 for c in columns}
    day_rows = {}
    rows = 0
    last_time = None
    equal_time_rows = 0
    writer = None
    writer_day = None
    next_report = progress_every

    try:
        for batch in reader:
            if batch.num_rows == 0:
                continue
            times = batch.column("time")

            # Ordering: non-decreasing within the batch and across batches.
            first = times[0].as_py()
            if last_time is not None and first < last_time:
                raise ValueError(f"time decreases at row {rows}: {last_time} -> {first}")
            if batch.num_rows > 1:
                diffs = pc.subtract(times.slice(1), times.slice(0, batch.num_rows - 1))
                if pc.min(diffs).as_py() < 0:
                    raise ValueError(f"time decreases inside block starting at row {rows}")
                equal_time_rows += pc.sum(pc.equal(diffs, 0)).as_py() or 0
            if last_time is not None and first == last_time:
                equal_time_rows += 1
            last_time = times[-1].as_py()

            for c in columns:
                null_counts[c] += batch.column(c).null_count

            row_ids = pa.array(range(rows, rows + batch.num_rows), pa.int64())
            table = pa.Table.from_batches([batch]).add_column(0, "row_id", row_ids)
            days = pa.array(day_index(times.to_numpy()))

            # Batches are chronological, so each day is one contiguous slice.
            for day in pc.unique(days).to_pylist():
                day = int(day)
                mask = pc.equal(days, day)
                part = table.filter(mask)
                if day != writer_day:
                    if writer is not None:
                        writer.close()
                    part_dir = out_dir / f"day={day:02d}"
                    part_dir.mkdir(exist_ok=True)
                    writer = pq.ParquetWriter(part_dir / "part-0.parquet", out_schema,
                                              compression="zstd")
                    writer_day = day
                writer.write_table(part.cast(out_schema))
                day_rows[day] = day_rows.get(day, 0) + part.num_rows

            rows += batch.num_rows
            if progress_every and rows >= next_report:
                print(f"{source_path.name}: {rows:,} rows, through day {writer_day}")
                next_report += progress_every
    finally:
        if writer is not None:
            writer.close()

    manifest = {
        "source": source_path.name,
        "source_bytes": source_path.stat().st_size,
        "columns": columns,
        "rows": rows,
        "invalid_rows_skipped": len(invalid),
        "invalid_row_examples": invalid[:10],
        "time_min": None if rows == 0 else _first_time(out_dir),
        "time_max": last_time,
        "time_non_decreasing": True,
        "rows_sharing_previous_timestamp": equal_time_rows,
        "missing_counts": null_counts,
        "missing_rates": {c: (n / rows if rows else None) for c, n in null_counts.items()},
        "rows_per_day": {str(d): n for d, n in sorted(day_rows.items())},
    }
    (out_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def _first_time(out_dir):
    first_day = sorted(p for p in Path(out_dir).glob("day=*"))[0]
    return pq.ParquetFile(first_day / "part-0.parquet").read_row_group(0, columns=["time"])[
        "time"][0].as_py()


def read_redteam(redteam_path):
    """Read the small red-team file whole, with a 0-based ``redteam_row``."""
    table = open_source(redteam_path, REDTEAM_COLUMNS).read_all()
    return table.add_column(0, "redteam_row", pa.array(range(table.num_rows), pa.int64()))


def match_redteam(auth_dir, redteam_path, out_dir=None):
    """Match red-team keys to authentication rows. See README section 4.

    The key is (time, user, source_computer, destination_computer), joined to
    the authentication record's (time, source_user, source_computer,
    destination_computer). Destination user is never substituted for source
    user. Duplicate red-team rows collapse to one ``key_id``.

    Returns ``(matches, audit)``. ``matches`` has one row per (key_id,
    row_id) pair, keeping one-to-many ambiguity. When ``out_dir`` is given,
    both are written there.
    """
    redteam = read_redteam(redteam_path)

    keys = redteam.group_by(REDTEAM_KEY).aggregate([("redteam_row", "count")])
    keys = keys.rename_columns(REDTEAM_KEY + ["redteam_rows"])
    keys = keys.sort_by([(c, "ascending") for c in REDTEAM_KEY])
    keys = keys.add_column(0, "key_id", pa.array(range(keys.num_rows), pa.int64()))

    times = pc.unique(keys.column("time"))
    auth = ds.dataset(auth_dir, format="parquet", partitioning="hive")
    candidates = auth.to_table(
        columns=["row_id", "time", "source_user", "source_computer", "destination_computer"],
        filter=ds.field("time").isin(times),
    ).rename_columns(["row_id", "time", "user", "source_computer", "destination_computer"])

    matches = keys.join(candidates, keys=REDTEAM_KEY, join_type="inner")
    matches = matches.select(["key_id", "row_id"] + REDTEAM_KEY).sort_by(
        [("key_id", "ascending"), ("row_id", "ascending")])

    per_key = matches.group_by("key_id").aggregate([("row_id", "count")])
    rows_per_key = dict(zip(per_key.column("key_id").to_pylist(),
                            per_key.column("row_id_count").to_pylist()))
    matched_ids = set(rows_per_key)
    unmatched = keys.filter(pc.invert(pc.is_in(keys.column("key_id"),
                                                pa.array(sorted(matched_ids), pa.int64()))))

    days = day_index(keys.column("time").to_numpy())
    key_days = {}
    for d, k in zip(days.tolist(), keys.column("key_id").to_pylist()):
        key_days.setdefault(str(d), {"keys": 0, "matched": 0})
        key_days[str(d)]["keys"] += 1
        key_days[str(d)]["matched"] += int(k in matched_ids)

    audit = {
        "redteam_rows": redteam.num_rows,
        "unique_keys": keys.num_rows,
        "duplicate_redteam_rows": redteam.num_rows - keys.num_rows,
        "matched_keys": len(matched_ids),
        "unmatched_keys": unmatched.num_rows,
        "matched_auth_rows": matches.num_rows,
        "keys_with_multiple_rows": sum(1 for n in rows_per_key.values() if n > 1),
        "max_rows_per_key": max(rows_per_key.values(), default=0),
        "keys_by_day": dict(sorted(key_days.items(), key=lambda kv: int(kv[0]))),
        "unmatched_examples": unmatched.slice(0, 20).to_pylist(),
    }

    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(matches, out_dir / "redteam_matches.parquet")
        pq.write_table(keys, out_dir / "redteam_keys.parquet")
        (out_dir / "redteam_audit.json").write_text(json.dumps(audit, indent=2, default=str))
    return matches, audit
