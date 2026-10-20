import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knightglass.sampling import day_quotas, time_stratified_reservoir  # noqa: E402


def test_day_quotas_sum_exactly_and_cap():
    assert day_quotas({1: 50, 2: 30, 3: 20}, 10) == {1: 5, 2: 3, 3: 2}
    q = day_quotas({1: 3, 2: 3, 3: 3}, 4)
    assert sum(q.values()) == 4 and max(q.values()) - min(q.values()) <= 1
    assert day_quotas({1: 2, 2: 1}, 100) == {1: 2, 2: 1}


def write_features(root):
    labels = {6: ["unlabeled"] * 80 + ["known_positive", "possible"],
              7: ["unlabeled"] * 20, 8: ["unlabeled"] * 10}
    row = 0
    for day, ls in labels.items():
        part = root / f"day={day:02d}"
        part.mkdir(parents=True)
        pl.DataFrame({"row_id": list(range(row, row + len(ls))), "label": ls}).write_parquet(
            part / "part-0.parquet")
        row += len(ls)


def test_reservoir_sample(tmp_path):
    write_features(tmp_path / "f")
    sample, manifest = time_stratified_reservoir(tmp_path / "f", [6, 7], 10, seed=3,
                                                 out_dir=tmp_path / "s", name="train")
    assert manifest["quotas_by_day"] == {"6": 8, "7": 2}
    assert manifest["known_positive_rows"] == 1 and manifest["possible_rows_excluded"] == 1
    assert sample.height == 11 and sample["row_id"].n_unique() == 11
    assert (sample.filter(pl.col("label") == "known_positive")["row_id"].to_list() == [80])
    assert 81 not in sample["row_id"].to_list()  # possible row never sampled
    assert sample.filter(pl.col("day") == 8).height == 0
    day7 = sample.filter(pl.col("day") == 7)
    assert day7["inclusion_probability"].to_list() == [0.1, 0.1]
    assert day7["weight"].to_list() == [10.0, 10.0]
    again, _ = time_stratified_reservoir(tmp_path / "f", [6, 7], 10, seed=3)
    assert again["row_id"].to_list() == sample["row_id"].to_list()
    other, _ = time_stratified_reservoir(tmp_path / "f", [6, 7], 10, seed=4)
    assert other["row_id"].to_list() != sample["row_id"].to_list()
    saved = json.loads((tmp_path / "s" / "train_manifest.json").read_text())
    assert saved["seed"] == 3 and (tmp_path / "s" / "train.parquet").exists()
