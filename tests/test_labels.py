import gzip
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knightglass.evaluation import split_coverage  # noqa: E402
from knightglass.ingestion import (  # noqa: E402
    AUTH_COLUMNS, match_redteam, possible_redteam, stream_to_parquet,
)

# Row IDs in comments. Red-team source C1, user U1; keys on days 1 and 2.
AUTH = """\
10,U1@DOM1,U1@DOM1,C1,C2,NTLM,Network,LogOn,Success
20,U1@DOM1,U1@DOM1,C1,C3,NTLM,Network,LogOn,Success
30,U1@DOM1,U1@DOM1,C1,C3,Kerberos,Network,LogOn,Success
40,U2@DOM1,U2@DOM1,C1,C3,NTLM,Network,LogOn,Success
50,U1@DOM1,U1@DOM1,C5,C3,NTLM,Network,LogOn,Success
86410,U1@DOM1,U1@DOM1,C1,C4,NTLM,Network,LogOn,Success
172810,U1@DOM1,U1@DOM1,C1,C4,NTLM,Network,LogOn,Success
"""
# 0: known positive    1: possible       2: not NTLM     3: other user
# 4: other source      5: known positive 6: day 3 has no key

REDTEAM = """\
10,U1@DOM1,C1,C2
86410,U1@DOM1,C1,C4
86500,U1@DOM1,C1,C9
"""


def write_gz(path, text):
    with gzip.open(path, "wt") as f:
        f.write(text)
    return path


@pytest.fixture
def labels(tmp_path):
    out = tmp_path / "auth"
    stream_to_parquet(write_gz(tmp_path / "auth.txt.gz", AUTH), AUTH_COLUMNS, out,
                      block_size=64, progress_every=0)
    matches, _ = match_redteam(out, write_gz(tmp_path / "redteam.txt.gz", REDTEAM),
                               tmp_path / "labels")
    keys = pq.read_table(tmp_path / "labels" / "redteam_keys.parquet")
    return out, keys, matches


def test_possible_rule(labels):
    out, keys, matches = labels
    possible = possible_redteam(out, keys, matches)
    assert possible.column("row_id").to_pylist() == [1]
    assert possible.column("reason").to_pylist() == ["rule"]


def test_possible_exceptions(labels):
    out, keys, matches = labels
    possible = possible_redteam(out, keys, matches, exceptions=[6, 1])
    assert possible.column("row_id").to_pylist() == [1, 6]
    assert possible.column("reason").to_pylist() == ["rule", "exception"]
    with pytest.raises(ValueError, match="already known"):
        possible_redteam(out, keys, matches, exceptions=[0])
    with pytest.raises(ValueError, match="missing"):
        possible_redteam(out, keys, matches, exceptions=[99])


def test_split_coverage(labels):
    out, keys, matches = labels
    possible = possible_redteam(out, keys, matches, exceptions=[6])
    cov = split_coverage(keys, matches, possible, {"train": [1, 1], "test": [2, 2]})
    assert cov["train"]["matched_keys"] == 1 and cov["train"]["possible_rows"] == 1
    assert cov["test"]["matched_keys"] == 1 and cov["test"]["unmatched_keys"] == 1
    assert cov["test"]["keys_by_source"] == {"C1": 1}
    assert cov["outside"]["possible_rows"] == 1
