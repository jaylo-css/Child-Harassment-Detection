"""Offline baseline: TF-IDF (word 1-2 grams + char 3-5 grams) + one-vs-rest logistic regression.

Features keep the other party's text and the child's context separate, so the model can learn,
for example, that "add me on discord" between classmates differs from the same request by an
unknown adult. Model files are joblib pickles: only load files you trained yourself.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import joblib
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MultiLabelBinarizer

from app.detection.classifiers.base import NullClassifier, StageClassifier, StageProbs, normalize_probs
from app.detection.stages import ALL_STAGES
from app.detection.windows import split_text
from app.models import ScoredMessage
from app.textproc import get_language_pack

log = logging.getLogger(__name__)
_LANG = get_language_pack("en")


def _norm(text: str) -> str:
    return _LANG.normalize(text)


def _features(windows: Sequence[Sequence[ScoredMessage]]) -> np.ndarray:
    rows = [split_text(w) for w in windows]
    return np.array(rows, dtype=object).reshape(len(rows), 2)


def build_pipeline(c: float = 4.0) -> Pipeline:
    feats = ColumnTransformer(
        [
            (
                "other_word",
                TfidfVectorizer(preprocessor=_norm, ngram_range=(1, 2), min_df=2, sublinear_tf=True),
                0,
            ),
            (
                "other_char",
                TfidfVectorizer(
                    preprocessor=_norm, analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True
                ),
                0,
            ),
            (
                "child_word",
                TfidfVectorizer(preprocessor=_norm, ngram_range=(1, 2), min_df=2, sublinear_tf=True),
                1,
            ),
        ],
        transformer_weights={"other_word": 1.0, "other_char": 1.0, "child_word": 0.4},
    )
    clf = OneVsRestClassifier(LogisticRegression(C=c, class_weight="balanced", max_iter=2000))
    return Pipeline([("features", feats), ("clf", clf)])


class BaselineClassifier:
    name = "baseline"

    def __init__(self, pipeline: Pipeline, binarizer: MultiLabelBinarizer) -> None:
        self.pipeline = pipeline
        self.binarizer = binarizer

    @classmethod
    def train(
        cls, windows: Sequence[Sequence[ScoredMessage]], labels: Sequence[set[str]], c: float = 4.0
    ) -> BaselineClassifier:
        mlb = MultiLabelBinarizer(classes=[s.value for s in ALL_STAGES])
        y = mlb.fit_transform(labels)
        pipe = build_pipeline(c)
        pipe.fit(_features(windows), y)
        return cls(pipe, mlb)

    def classify(self, window: Sequence[ScoredMessage]) -> StageProbs:
        return self.classify_batch([window])[0]

    def classify_batch(self, windows: Sequence[Sequence[ScoredMessage]]) -> list[StageProbs]:
        if not windows:
            return []
        proba = self.pipeline.predict_proba(_features(windows))
        classes = list(self.binarizer.classes_)
        return [normalize_probs(dict(zip(classes, row.tolist(), strict=True))) for row in proba]

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"pipeline": self.pipeline, "binarizer": self.binarizer, "version": 1}, path)

    @classmethod
    def load(cls, path: str | Path) -> BaselineClassifier:
        blob = joblib.load(path)
        return cls(blob["pipeline"], blob["binarizer"])


_cache: dict[str, StageClassifier] = {}


def load_baseline(path: str | Path) -> StageClassifier:
    """Load the trained model, or fall back to signals-only scoring if none exists."""
    key = str(path)
    if key not in _cache:
        if Path(path).exists():
            _cache[key] = BaselineClassifier.load(path)
        else:
            log.warning("baseline model not found at %s - run `make train`; using signals only", path)
            _cache[key] = NullClassifier()
    return _cache[key]


def clear_cache() -> None:
    _cache.clear()
