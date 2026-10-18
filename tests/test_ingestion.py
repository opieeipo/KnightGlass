import gzip
import json
import sys
from pathlib import Path

import pyarrow.dataset as ds
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nightglass.ingestion import (  # noqa: E402
    AUTH_COLUMNS, day_index, match_redteam, stream_to_parquet,
)

AUTH = """\
1,U1@DOM1,U1@DOM1,C1,C2,NTLM,Network,LogOn,Success
1,U2@DOM1,U2@DOM1,C3,C4,?,Network,LogOff,Success
86400,U1@DOM1,U1@DOM1,C1,C5,Kerberos,Network,LogOn,Success
86401,U1@DOM1,U1@DOM1,C1,C5,Kerberos,Network,LogOn,Success
86401,U1@DOM1,U9@DOM1,C1,C5,NTLM,Network,LogOn,Fail
172801,U3@DOM1,U3@DOM1,C7,C8,Kerberos,Network,LogOn,Success
"""

REDTEAM = """\
86401,U1@DOM1,C1,C5
86401,U1@DOM1,C1,C5
172801,U3@DOM1,C7,C8
200000,U4@DOM1,C9,C9
86401,U9@DOM1,C1,C5
"""


def write_gz(path, text):
    with gzip.open(path, "wt") as f:
        f.write(text)
    return path


@pytest.fixture
def auth_dir(tmp_path):
    src = write_gz(tmp_path / "auth.txt.gz", AUTH)
    out = tmp_path / "auth"
    manifest = stream_to_parquet(src, AUTH_COLUMNS, out, block_size=64, progress_every=0)
    return out, manifest


def test_day_index_boundaries():
    assert day_index(1) == 1
    assert day_index(86400) == 1
    assert day_index(86401) == 2


def test_partitions_row_ids_and_manifest(auth_dir):
    out, manifest = auth_dir
    assert sorted(p.name for p in out.glob("day=*")) == ["day=01", "day=02", "day=03"]
    table = ds.dataset(out, format="parquet", partitioning="hive").to_table().sort_by("row_id")
    assert table.column("row_id").to_pylist() == list(range(6))
    assert table.column("day").to_pylist() == [1, 1, 1, 2, 2, 3]
    assert table.column("authentication_type")[1].as_py() is None
    assert manifest["rows"] == 6
    assert manifest["missing_counts"]["authentication_type"] == 1
    assert manifest["rows_per_day"] == {"1": 3, "2": 2, "3": 1}
    assert manifest["rows_sharing_previous_timestamp"] == 2
    assert manifest["time_min"] == 1 and manifest["time_max"] == 172801
    assert json.loads((out / "_manifest.json").read_text())["rows"] == 6


def test_decreasing_time_raises(tmp_path):
    src = write_gz(tmp_path / "bad.txt.gz", "5,C1,C2\n4,C1,C3\n")
    with pytest.raises(ValueError, match="decreases"):
        stream_to_parquet(src, ["time", "source_computer", "resolved_computer"],
                          tmp_path / "dns", progress_every=0)


def test_malformed_rows_fail_by_default(tmp_path):
    src = write_gz(tmp_path / "bad.txt.gz", "1,C1,C2\n2,C1\n")
    with pytest.raises(Exception):
        stream_to_parquet(src, ["time", "source_computer", "resolved_computer"],
                          tmp_path / "dns", progress_every=0)


def test_refuses_non_empty_output(auth_dir, tmp_path):
    out, _ = auth_dir
    with pytest.raises(FileExistsError):
        stream_to_parquet(tmp_path / "auth.txt.gz", AUTH_COLUMNS, out, progress_every=0)


def test_match_redteam(auth_dir, tmp_path):
    out, _ = auth_dir
    rt = write_gz(tmp_path / "redteam.txt.gz", REDTEAM)
    matches, audit = match_redteam(out, rt, tmp_path / "labels")
    assert audit["redteam_rows"] == 5
    assert audit["unique_keys"] == 4
    assert audit["duplicate_redteam_rows"] == 1
    # U1 key matches two auth rows; U3 matches one; U4 has no auth row.
    # U9 appears only as a destination user and must not match.
    assert audit["matched_keys"] == 2
    assert audit["unmatched_keys"] == 2
    assert audit["matched_auth_rows"] == 3
    assert audit["keys_with_multiple_rows"] == 1
    assert sorted(matches.column("row_id").to_pylist()) == [3, 4, 5]
    assert (tmp_path / "labels" / "redteam_audit.json").exists()
