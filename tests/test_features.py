import gzip
import sys
from pathlib import Path

import polars as pl
import pyarrow as pa
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knightglass.features import (  # noqa: E402
    AUTH_EVENT_COLUMNS, build_auth_features, build_dns_features, build_features,
    dns_pair_coverage, empty_auth_state, label_rows,
)
from knightglass.ingestion import AUTH_COLUMNS, DNS_COLUMNS, stream_to_parquet  # noqa: E402

AUTH = """\
100,U1@DOM1,U1@DOM1,C1,C2,NTLM,Network,LogOn,Success
100,U1@DOM1,U1@DOM1,C1,C1,?,Network,LogOff,Success
200,U1@DOM1,U1@DOM1,C1,C2,NTLM,Network,LogOn,Success
5000,U1@DOM1,U1@DOM1,C1,C3,NTLM,Network,LogOn,Success
86500,U1@DOM1,U1@DOM1,C1,C2,NTLM,Network,LogOn,Success
"""
# Row 0 shares its second with the DNS record, so that record is not "before".
DNS = """\
100,C1,C2
4900,C9,C3
"""


def write_gz(path, text):
    with gzip.open(path, "wt") as f:
        f.write(text)
    return path


def test_dns_pair_coverage(tmp_path):
    auth, dns = tmp_path / "auth", tmp_path / "dns"
    stream_to_parquet(write_gz(tmp_path / "a.gz", AUTH), AUTH_COLUMNS, auth,
                      block_size=64, progress_every=0)
    stream_to_parquet(write_gz(tmp_path / "d.gz", DNS), DNS_COLUMNS, dns,
                      block_size=64, progress_every=0)
    by_day, rows = dns_pair_coverage(auth, dns, [300, 86400], {"positive": [2, 3, 4]})
    day1 = by_day.filter(by_day["day"] == 1).row(0, named=True)
    # Day 1: rows 0, 2, 3 are non-self; only row 2 has an earlier C1->C2 lookup.
    assert day1["events"] == 4 and day1["nonself_events"] == 3
    assert day1["pair_ever"] == 1 and day1["pair_within_300"] == 1
    assert day1["source_within_300"] == 1  # row 3's last C1 lookup is 4,900 s old
    assert day1["source_within_86400"] == 2
    day2 = by_day.filter(by_day["day"] == 2).row(0, named=True)
    # Day 2 still sees day-1 DNS history; 86,400 s have passed since the lookup.
    assert day2["pair_ever"] == 1 and day2["pair_within_86400"] == 1
    rows = rows.sort("row_id")
    assert rows["row_id"].to_list() == [2, 3, 4]
    assert rows["last_pair_dns"].to_list() == [100, None, 100]



def events(rows):
    return pl.DataFrame(rows, schema={c: (pl.Int64 if c in ("row_id", "time") else pl.String)
                                      for c in AUTH_EVENT_COLUMNS}, orient="row")


DAY1 = events([
    (0, 10, "U1", "C1", "C2", "NTLM", "Network", "LogOn", "Success"),
    (1, 10, "U1", "C1", "C3", "Kerberos", "Network", "LogOn", "Fail"),
    (2, 15, "U1", "C1", "C2", "NTLM", "Network", "LogOn", "Success"),
    (3, 30, "U1", "C1", "C4", None, "Network", "LogOn", "Success"),
    (4, 30, "U2", "C5", "C5", "Kerberos", None, "LogOff", "Success"),
])


def by_row(features):
    return {r["row_id"]: r for r in features.iter_rows(named=True)}


def test_auth_features_one_day():
    f, _ = build_auth_features(DAY1, DAY1.clear(), empty_auth_state(), [10, 100])
    f = by_row(f)
    # Rows 0 and 1 share a timestamp, so neither sees the other.
    for r in (0, 1):
        assert f[r]["user_events_100"] == 0 and f[r]["user_prior_events"] == 0
        assert f[r]["ud_seen"] is False and f[r]["user_fail_rate_100"] is None
    r2 = f[2]
    assert r2["user_events_10"] == 2 and r2["user_distinct_dest_10"] == 2
    assert r2["user_fail_rate_10"] == 0.5
    assert r2["ud_seen"] is True and r2["ud_gap"] == 5
    assert r2["user_prior_events"] == 2
    assert r2["user_auth_type_freq"] == 0.5 and r2["user_logon_type_freq"] == 1.0
    r3 = f[3]
    # [20, 30) is empty; [-70, 30) holds rows 0-2 with destinations C2, C3.
    assert r3["user_events_10"] == 0 and r3["user_distinct_dest_10"] == 0
    assert r3["user_events_100"] == 3 and r3["user_distinct_dest_100"] == 2
    assert abs(r3["user_fail_rate_100"] - 1 / 3) < 1e-12
    assert r3["ud_seen"] is False and r3["ud_gap"] is None
    assert r3["auth_type_missing"] is True and r3["user_auth_type_freq"] == 0.0
    assert f[4]["is_self"] is True and f[4]["logon_type_missing"] is True
    assert f[4]["user_events_100"] == 0


def test_auth_state_carries_across_days():
    _, state = build_auth_features(DAY1, DAY1.clear(), empty_auth_state(), [10, 100, 86400])
    day2 = events([(5, 86410, "U1", "C1", "C2", "NTLM", "Network", "LogOn", "Success")])
    f = by_row(build_auth_features(day2, DAY1, state, [10, 100, 86400])[0])[5]
    assert f["ud_seen"] is True and f["ud_gap"] == 86410 - 15
    assert f["user_prior_events"] == 4 and f["user_auth_type_freq"] == 0.5
    assert f["user_events_100"] == 0
    # [10, 86410) includes the boundary events at time 10.
    assert f["user_events_86400"] == 4 and f["user_distinct_dest_86400"] == 3


def test_dns_features():
    today = events([(0, 100, "U1", "C1", "C2", "NTLM", "Network", "LogOn", "Success"),
                    (1, 200, "U1", "C1", "C2", "NTLM", "Network", "LogOn", "Success"),
                    (2, 200, "U1", "C1", "C3", "NTLM", "Network", "LogOn", "Success")])
    dns = pl.DataFrame({"time": [50, 100, 150], "source_computer": ["C1", "C1", "C9"],
                        "resolved_computer": ["C2", "C2", "C3"]})
    f = by_row(build_dns_features(today, dns, dns, [60, 1000], True))
    # Row 0 shares its second with the second lookup, so only time 50 counts.
    assert f[0]["dns_pair_gap"] == 50 and f[0]["dns_pair_count_60"] == 1
    assert f[1]["dns_pair_gap"] == 100 and f[1]["dns_pair_count_60"] == 0
    assert f[1]["dns_pair_count_1000"] == 2
    assert f[2]["dns_pair_seen"] is False and f[2]["dns_pair_gap"] is None
    assert f[2]["diag_source_dns_seen"] is True
    assert all(r["dns_full_collection"] for r in f.values())


def test_build_features_end_to_end(tmp_path):
    auth, dns = tmp_path / "auth", tmp_path / "dns"
    stream_to_parquet(write_gz(tmp_path / "a.gz", AUTH), AUTH_COLUMNS, auth,
                      block_size=64, progress_every=0)
    stream_to_parquet(write_gz(tmp_path / "d.gz", DNS), DNS_COLUMNS, dns,
                      block_size=64, progress_every=0)
    labels = label_rows(pa.table({"row_id": [2], "key_id": [7]}),
                        pa.table({"row_id": [3], "day": [1], "reason": ["rule"]}))
    out = tmp_path / "features"
    manifest = build_features(auth, dns, labels, out, [300, 86400], 2, first_day=1,
                              progress=False)
    assert manifest["rows_per_day"] == {"1": 4, "2": 1}
    table = pl.read_parquet(out / "day=*" / "*.parquet", hive_partitioning=True).sort("row_id")
    assert table["label"].to_list() == ["unlabeled", "unlabeled", "known_positive", "possible",
                                        "unlabeled"]
    assert table["key_id"].to_list() == [None, None, 7, None, None]
    assert table["dns_full_collection"].to_list() == [False] * 4 + [True]
    assert table["ud_seen"].to_list() == [False, False, True, False, True]
    with pytest.raises(FileExistsError):
        build_features(auth, dns, labels, out, [300], 2, first_day=1, progress=False)
