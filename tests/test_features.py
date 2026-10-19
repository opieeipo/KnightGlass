import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knightglass.features import dns_pair_coverage  # noqa: E402
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
