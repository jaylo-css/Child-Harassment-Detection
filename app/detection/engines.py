"""Engine selection (GUARDIAN_ENGINE=baseline|llm|ensemble).

* baseline - TF-IDF + logistic regression, offline.
* llm      - Claude rubric classifier; the baseline still scores every message immediately and
             the LLM refines selected windows (streaming: only once risk >= MEDIUM).
* ensemble - like llm, but refined windows use the average of baseline and LLM probabilities.

If no ANTHROPIC_API_KEY is set, llm/ensemble degrade to baseline.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.detection.classifiers.base import StageClassifier, StageProbs, average_probs
from app.detection.classifiers.baseline import load_baseline
from app.detection.signals import SignalDetector, get_detector

log = logging.getLogger(__name__)
_llm_cache: dict[tuple[str, str], object] = {}


def baseline(settings: Settings) -> StageClassifier:
    return load_baseline(settings.model_path)


def detector(settings: Settings) -> SignalDetector:
    return get_detector(settings.rules_path, settings.language)


def llm(settings: Settings):
    """Return the LLM rubric classifier, or None when unavailable (no key / SDK missing)."""
    if settings.engine == "baseline" or not settings.llm_available:
        return None
    key = (settings.llm_model, settings.anthropic_api_key or "")
    if key not in _llm_cache:
        try:
            from app.detection.classifiers.llm_rubric import LLMRubricClassifier

            _llm_cache[key] = LLMRubricClassifier(
                model=settings.llm_model, api_key=settings.anthropic_api_key, fallback=baseline(settings)
            )
        except Exception as exc:  # SDK missing or init error -> offline
            log.warning("LLM engine unavailable (%s); using baseline", type(exc).__name__)
            return None
    return _llm_cache[key]


def effective_engine(settings: Settings) -> str:
    return settings.engine if llm(settings) is not None else "baseline"


def combine(engine: str, base_probs: StageProbs, llm_probs: StageProbs | None) -> StageProbs:
    if not llm_probs:
        return base_probs
    if engine == "ensemble":
        return average_probs(base_probs, llm_probs) if base_probs else llm_probs
    return llm_probs
