import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knightglass.evaluation import daily_ranks, recall_at_daily_budget  # noqa: E402
from knightglass.features import CATEGORICAL_FEATURES  # noqa: E402
from knightglass.models import (  # noqa: E402
    ModelRanker, RuleRanker, fit_cached, labeled_days, score_period,
)


def scored(rows):
    return pl.DataFrame(rows, schema=["row_id", "day", "score", "label", "key_id"], orient="row")


def test_daily_ranks_and_recall():
    s = scored([
        (0, 1, 0.9, "unlabeled", None), (1, 1, 0.5, "known_positive", 10),
        (2, 1, 0.1, "known_positive", 10), (3, 1, 0.2, "unlabeled", None),
        (4, 2, 0.8, "known_positive", 11), (5, 2, 0.3, "possible", None),
        (6, 2, 0.1, "unlabeled", None),
    ])
    r = daily_ranks(s, seed=0, keep_top=1)
    # Day 1 keeps its top row and both labeled rows; row 3 is dropped.
    assert sorted(r["row_id"].to_list()) == [0, 1, 2, 4, 5]
    ranks = dict(zip(r["row_id"], r["rank"]))
    assert ranks[0] == 1 and ranks[1] == 2 and ranks[2] == 4 and ranks[5] == 2
    one = recall_at_daily_budget(r, 1)
    assert (one["positives"], one["recovered"]) == (2, 1)
    assert one["by_day"] == {1: (0, 1), 2: (1, 1)}
    # Key 10 counts once even though two of its rows are reviewed.
    assert recall_at_daily_budget(r, 4)["recall"] == 1.0
    assert recall_at_daily_budget(r, 1, include_possible=True)["positives"] == 3


def test_ties_are_broken_by_seed_not_file_order():
    s = scored([(i, 1, 0.0, "unlabeled", None) for i in range(50)])
    a = daily_ranks(s, seed=1, keep_top=1)["row_id"].item()
    b = daily_ranks(s, seed=2, keep_top=1)["row_id"].item()
    assert daily_ranks(s, seed=1, keep_top=1)["row_id"].item() == a
    assert (a, b) != (0, 0)


def frame(n, seed):
    rng = np.random.default_rng(seed)
    pos = rng.random(n) < 0.05
    return pl.DataFrame({
        "row_id": np.arange(n), "day": np.repeat([1, 2], n // 2),
        "label": np.where(pos, "known_positive", "unlabeled"),
        "key_id": np.where(pos, np.arange(n), -1),
        "authentication_type": np.where(pos, "NTLM", rng.choice(["Kerberos", "NTLM"], n)),
        "logon_type": "Network", "authentication_orientation": "LogOn", "result": "Success",
        "x": np.where(pos, 3.0, 0.0) + rng.normal(size=n),
        "ud_gap": rng.random(n) * 100, "_missing": rng.random(n) < 0.3,
    }).with_columns(pl.col("key_id").replace(-1, None),
                    pl.when(~pl.col("_missing")).then(pl.col("ud_gap")).alias("ud_gap")
                    ).drop("_missing")


def test_model_rankers_learn_signal(tmp_path):
    train = frame(4000, 0)
    for kind, params in [("logistic", {"C": 1.0}), ("hist_gb", {"max_iter": 30})]:
        m = ModelRanker(kind, ["x", "ud_gap"], params, weighted=False, seed=0).fit(train)
        test = frame(2000, 1)
        s = m.score(test)
        y = (test["label"] == "known_positive").to_numpy()
        assert s[y].mean() > s[~y].mean()
    # Unseen categories score without error.
    odd = frame(10, 2).with_columns(pl.lit("Weird").alias("authentication_type"))
    assert len(m.score(odd)) == 10 and set(CATEGORICAL_FEATURES) <= set(m.columns)


def test_weight_scale_does_not_change_fit():
    train = frame(4000, 0)
    weight = pl.Series(np.where(train["label"] == "known_positive", 1.0, 50.0))
    test = frame(500, 1)
    for kind, params in [("logistic", {"C": 1.0}), ("hist_gb", {"max_iter": 30})]:
        a = ModelRanker(kind, ["x", "ud_gap"], params, weighted=True, seed=0).fit(train, weight)
        b = ModelRanker(kind, ["x", "ud_gap"], params, weighted=True, seed=0).fit(train, weight * 10)
        assert np.allclose(a.score(test), b.score(test), rtol=1e-4, atol=1e-6)


def test_rules_and_score_period(tmp_path):
    f = pl.DataFrame({"row_id": [0, 1, 2], "day": [6, 6, 7],
                      "label": ["unlabeled", "known_positive", "unlabeled"], "key_id": [None, 5, None],
                      "ud_seen": [True, False, True], "user_distinct_dest_3600": [9, 1, 0]})
    assert RuleRanker("novelty").score(f).argmax() == 1
    assert RuleRanker("rate").score(f).argmax() == 0
    for day in (6, 7):
        part = tmp_path / f"day={day:02d}"
        part.mkdir()
        f.filter(pl.col("day") == day).drop("day").write_parquet(part / "part-0.parquet")
    assert labeled_days(tmp_path, [6, 7]) == [6]
    rules = {"novelty": RuleRanker("novelty")}
    ranked = score_period(rules, tmp_path, [6, 7], 1, 0, cache_dir=tmp_path / "c",
                          progress=False)["novelty"]
    assert recall_at_daily_budget(ranked, 1)["recall"] == 1.0
    assert (tmp_path / "c" / "novelty" / "day=07.parquet").exists()
    # A second call reads the cache; a ranker that would fail proves it is not rescored.
    class Broken:
        columns = []
        def score(self, frame):
            raise AssertionError("rescored a cached day")
    again = score_period({"novelty": Broken()}, tmp_path, [6, 7], 1, 0, cache_dir=tmp_path / "c",
                         progress=False)["novelty"]
    assert again.sort("row_id").equals(ranked.sort("row_id"))


def test_fit_cached(tmp_path):
    train = frame(2000, 0)
    m = fit_cached("lr", ModelRanker("logistic", ["x"], {"C": 1.0}, False, 0), train, None,
                   tmp_path, progress=False)
    loaded = fit_cached("lr", ModelRanker("logistic", ["x"], {"C": 9.9}, False, 0), train, None,
                        tmp_path, progress=False)
    assert loaded.params == {"C": 1.0}
    assert np.allclose(loaded.score(train), m.score(train))
