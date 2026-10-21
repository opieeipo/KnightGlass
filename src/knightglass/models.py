"""Baseline rankers. See README section 8.

Every ranker scores feature rows; higher scores are reviewed first. Raw user
and computer IDs are never model inputs. Preprocessing is fit on training
rows only.
"""

from pathlib import Path

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from knightglass.evaluation import daily_ranks
from knightglass.features import (
    CATEGORICAL_FEATURES, DIAGNOSTICS, auth_feature_names, dns_feature_names, scan_days,
)

# Columns put on a log scale for logistic regression.
LOG_PREFIXES = ("user_events_", "user_distinct_dest_", "user_prior_events", "ud_gap",
                "dns_pair_gap", "dns_pair_count_")
CHUNK_ROWS = 2_000_000


def feature_sets(windows_seconds):
    """Model inputs besides the categorical event attributes."""
    auth = auth_feature_names(windows_seconds)
    return {
        "auth_only": auth,
        "combined": auth + dns_feature_names(windows_seconds),
        # Collection-artifact check (README section 6, treatment of the DNS break).
        "auth_plus_collection": auth + DIAGNOSTICS,
    }


class RuleRanker:
    """Fixed heuristics with no fitting (README section 8).

    ``novelty``: a user's first authentication to a destination ranks above
    any repeat; within each group, more distinct destinations in the past
    hour ranks higher. ``rate``: distinct destinations in the past hour.
    """

    def __init__(self, rule):
        self.rule = rule
        self.columns = ["ud_seen", "user_distinct_dest_3600"]

    def score(self, frame):
        d = frame["user_distinct_dest_3600"].cast(pl.Float64).to_numpy()
        if self.rule == "rate":
            return d
        return (~frame["ud_seen"]).cast(pl.Float64).to_numpy() + d / (1 + d)


class ModelRanker:
    """Logistic regression or HistGradientBoosting on one feature set."""

    def __init__(self, kind, numeric, params, weighted, seed):
        self.kind, self.numeric, self.params = kind, list(numeric), dict(params)
        self.weighted, self.seed = weighted, seed
        self.columns = CATEGORICAL_FEATURES + self.numeric

    def _matrix(self, frame):
        num = []
        for c in self.numeric:
            x = frame[c].cast(pl.Float64)
            if self.kind == "logistic":
                x = x.fill_null(0)
                if c.startswith(LOG_PREFIXES):
                    x = x.clip(lower_bound=0).log1p()
            num.append(x.to_numpy())
        if self.kind == "logistic":
            cats = [(frame[c] == v).fill_null(False).to_numpy()
                    for c in CATEGORICAL_FEATURES for v in self.categories[c]]
        else:
            # Codes learned in training; unseen values become missing.
            cats = [frame[c].replace_strict(self.categories[c],
                                            list(range(len(self.categories[c]))),
                                            default=None, return_dtype=pl.Float64)
                    .to_numpy() for c in CATEGORICAL_FEATURES]
        return np.column_stack(num + cats).astype(np.float32)

    def fit(self, frame, weight=None):
        y = (frame["label"] == "known_positive").to_numpy()
        self.categories = {c: frame[c].drop_nulls().value_counts(sort=True)
                           .filter(pl.col("count") >= 20)[c].head(200).to_list()
                           for c in CATEGORICAL_FEATURES}
        x = self._matrix(frame)
        w = weight.to_numpy() if (self.weighted and weight is not None) else None
        if w is not None:
            # Only relative weights matter. Raw inclusion weights grow with the
            # sample's day span, which made boosting diverge on the refit sample.
            w = w / w.mean()
        if self.kind == "logistic":
            self.scaler = StandardScaler().fit(x, sample_weight=w)
            self.model = LogisticRegression(max_iter=2000, random_state=self.seed, **self.params)
            self.model.fit(self.scaler.transform(x), y, sample_weight=w)
        else:
            mask = [False] * len(self.numeric) + [True] * len(CATEGORICAL_FEATURES)
            self.model = HistGradientBoostingClassifier(
                categorical_features=mask, early_stopping=False, random_state=self.seed,
                **self.params)
            self.model.fit(x, y, sample_weight=w)
        return self

    def score(self, frame):
        out = []
        for start in range(0, frame.height, CHUNK_ROWS):
            x = self._matrix(frame.slice(start, CHUNK_ROWS))
            if self.kind == "logistic":
                out.append(self.model.decision_function(self.scaler.transform(x)))
            else:
                out.append(self.model.predict_proba(x)[:, 1])
        return np.concatenate(out) if out else np.empty(0)


def load_sample(feature_dir, sample, columns):
    """Feature rows for a training sample, with its weights, in row order."""
    days = sample["day"].unique().to_list()
    return (scan_days(feature_dir).filter(pl.col("day").is_in(days))
            .select("row_id", "label", *columns)
            .join(sample.lazy().select("row_id", "weight"), on="row_id", how="inner")
            .sort("row_id").collect())


def labeled_days(feature_dir, days):
    """Days in the inclusive range ``days`` with any known-positive or
    possible row. Days without them cannot change budget recall, because
    each day is ranked on its own."""
    first, last = days
    return (scan_days(feature_dir)
            .filter(pl.col("day").is_between(first, last) & (pl.col("label") != "unlabeled"))
            .select(pl.col("day").unique().sort()).collect().to_series().to_list())


def score_period(rankers, feature_dir, days, keep_top, seed, cache_dir=None, progress=True):
    """Score every row of each day in ``days`` with every ranker.

    ``days`` is a list of days. Loads each day once. Returns ``{name: ranked}``, where ``ranked`` is
    the ``daily_ranks`` output: each day's top ``keep_top`` rows plus every
    labeled row, with its score and rank. With ``cache_dir``, each ranker's
    day is saved as it finishes and reused on later calls, so an interrupted
    run resumes where it stopped.
    """
    scan = scan_days(feature_dir)

    def cached(name, day):
        return None if cache_dir is None else Path(cache_dir) / name / f"day={day:02d}.parquet"

    ranked = {name: [] for name in rankers}
    for day in days:
        todo = {n: r for n, r in rankers.items() if cached(n, day) is None or not cached(n, day).exists()}
        if todo:
            columns = sorted({c for r in todo.values() for c in r.columns})
            frame = (scan.filter(pl.col("day") == day)
                     .select("row_id", "day", "label", "key_id", *columns).collect())
            base = frame.select("row_id", "day", "label", "key_id")
            for name, ranker in todo.items():
                result = daily_ranks(base.with_columns(pl.Series("score", ranker.score(frame))),
                                     seed, keep_top)
                if cache_dir is not None:
                    cached(name, day).parent.mkdir(parents=True, exist_ok=True)
                    result.write_parquet(cached(name, day))
                ranked[name].append(result)
                if progress:
                    print(f"scored day {day}: {name}", flush=True)
            del frame, base
        for name in rankers:
            if name not in todo:
                ranked[name].append(pl.read_parquet(cached(name, day)))
    return {name: pl.concat(parts) for name, parts in ranked.items()}


def fit_cached(name, ranker, frame, weight, cache_dir, progress=True):
    """Fit ``ranker`` unless a fitted copy is saved under ``cache_dir``."""
    import joblib

    path = Path(cache_dir) / f"{name}.joblib"
    if path.exists():
        return joblib.load(path)
    ranker.fit(frame, weight)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(ranker, path)
    if progress:
        print(f"fitted {name}", flush=True)
    return ranker
