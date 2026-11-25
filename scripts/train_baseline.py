"""Train the TF-IDF + logistic-regression stage classifier.

Training windows: for each other-party message, the window of the last `window_size` messages
ending there (the same windows the scorer classifies). Labels: the set of stages present among
the other party's messages in the window, or NONE.

If PAN-2012 is present in data/pan2012/, weakly-labeled windows are added (stage labels come
from Layer-1 signal hits on predator messages; non-predator conversations contribute NONE
windows). PAN text is never printed.

Usage: python scripts/train_baseline.py [--c 4.0] [--no-pan]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sklearn.metrics import f1_score  # noqa: E402
from sklearn.preprocessing import MultiLabelBinarizer  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.detection.classifiers.baseline import BaselineClassifier  # noqa: E402
from app.detection.signals import get_detector  # noqa: E402
from app.detection.stages import ALL_STAGES  # noqa: E402
from app.detection.windows import window_ending_at  # noqa: E402
from app.ingest import pan2012  # noqa: E402
from app.ingest.synthetic import load_split, window_labels  # noqa: E402


def synthetic_windows(split: str, size: int):
    X, Y = [], []
    for conv in load_split(split):
        for m in conv.messages:
            if m.is_child:
                continue
            start = max(0, m.index - size + 1)
            X.append(window_ending_at(conv.messages, m.index, size))
            Y.append(window_labels(conv, start, m.index))
    return X, Y


def pan_windows(size: int, max_none: int = 20000, seed: int = 7):
    det = get_detector(get_settings().rules_path)
    rng = random.Random(seed)
    X, Y, none_pool = [], [], []
    for conv in pan2012.iter_conversations():
        if conv.has_predator:
            for suspect in conv.predator_authors:
                msgs = conv.as_scored(suspect)
                for m in msgs:
                    if m.is_child:
                        continue
                    stages = {h.stage for h in det.detect(m)}
                    if stages:  # weak label only where a high-precision rule fired
                        X.append(window_ending_at(msgs, m.index, size))
                        Y.append(stages)
        else:
            msgs = conv.as_scored(conv.authors[0])
            for m in msgs:
                if not m.is_child and rng.random() < 0.2:
                    none_pool.append(window_ending_at(msgs, m.index, size))
    rng.shuffle(none_pool)
    none_pool = none_pool[:max_none]
    X.extend(none_pool)
    Y.extend([{"NONE"}] * len(none_pool))
    return X, Y


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--c", type=float, default=4.0)
    ap.add_argument("--no-pan", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    s = get_settings()
    out = args.out or Path(s.model_path)

    t0 = time.time()
    X, Y = synthetic_windows("train", s.window_size)
    sources = {"synthetic_train_windows": len(X)}
    if not args.no_pan and pan2012.available():
        px, py = pan_windows(s.window_size)
        X += px
        Y += py
        sources["pan2012_windows"] = len(px)
    print(f"training on {len(X)} windows {sources}")
    model = BaselineClassifier.train(X, Y, c=args.c)

    vx, vy = synthetic_windows("val", s.window_size)
    classes = [st.value for st in ALL_STAGES]
    mlb = MultiLabelBinarizer(classes=classes)
    y_true = mlb.fit_transform(vy)
    probs = model.classify_batch(vx)
    y_pred = [[1 if p[c] >= 0.5 else 0 for c in classes] for p in probs]
    per_stage = f1_score(y_true, y_pred, average=None, zero_division=0)
    report = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "c": args.c,
        "sources": sources,
        "val_windows": len(vx),
        "val_micro_f1": round(float(f1_score(y_true, y_pred, average="micro", zero_division=0)), 4),
        "val_macro_f1": round(float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 4),
        "val_f1_per_stage": {c: round(float(v), 4) for c, v in zip(classes, per_stage, strict=True)},
        "seconds": round(time.time() - t0, 1),
    }
    model.save(out)
    out.with_name(out.stem + "_meta.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
