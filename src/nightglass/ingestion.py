"""Chunked, schema-explicit reading of the LANL archives into Parquet.

See README sections 3 and 5.2.
"""

AUTH_COLUMNS = [
    "time", "source_user", "destination_user", "source_computer",
    "destination_computer", "authentication_type", "logon_type",
    "authentication_orientation", "result",
]
DNS_COLUMNS = ["time", "source_computer", "resolved_computer"]
REDTEAM_COLUMNS = ["time", "user", "source_computer", "destination_computer"]

MISSING_VALUE = "?"
SECONDS_PER_DAY = 86400


def day_index(time):
    """Dataset day for a timestamp: floor((time - 1) / 86400) + 1."""
    return (time - 1) // SECONDS_PER_DAY + 1


def stream_to_parquet(source_path, columns, out_dir, chunk_rows=5_000_000):
    """Read a gzip CSV in chunks, assign stable row IDs, validate ordering,
    and write time-partitioned Parquet."""
    raise NotImplementedError


def match_redteam(auth_dir, redteam_path):
    """Match red-team keys to authentication rows on time, source user,
    source computer, and destination computer. Return matches plus an audit
    of unmatched and one-to-many keys."""
    raise NotImplementedError
