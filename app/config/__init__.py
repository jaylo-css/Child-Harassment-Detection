"""Configuration.

Precedence (lowest to highest): built-in defaults -> app/config/guardian.yaml -> environment
variables -> runtime overrides saved from the Settings page (stored in the DB).
"""

from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
APP_DIR = Path(__file__).resolve().parents[1]

Engine = Literal["baseline", "llm", "ensemble"]


class Thresholds(BaseModel):
    medium: float = 40.0
    high: float = 70.0
    critical: float = 85.0

    @model_validator(mode="after")
    def _ordered(self) -> Thresholds:
        if not (0 < self.medium < self.high < self.critical <= 100):
            raise ValueError("thresholds must satisfy 0 < medium < high < critical <= 100")
        return self


class TrajectoryConfig(BaseModel):
    half_life_hours: float = 72.0
    stage_seen_threshold: float = 0.5
    progression_max_bonus: float = 15.0
    velocity_max_bonus: float = 10.0
    velocity_ref_messages: int = 40
    velocity_ref_hours: float = 48.0
    # Severity of each stage when fully evidenced (combined with noisy-OR, scaled to 0-100).
    severity: dict[str, float] = Field(
        default_factory=lambda: {
            "FRIENDSHIP": 0.05,
            "RISK_ASSESSMENT": 0.22,
            "TRUST_EXCLUSIVITY": 0.35,
            "ISOLATION": 0.40,
            "SECRECY": 0.55,
            "PLATFORM_MOVE": 0.40,
            "ESCALATION": 0.80,
        }
    )


class Settings(BaseModel):
    engine: Engine = "baseline"
    llm_model: str = "claude-sonnet-5-5"
    anthropic_api_key: str | None = Field(default=None, repr=False)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    trajectory: TrajectoryConfig = Field(default_factory=TrajectoryConfig)
    window_size: int = 6
    window_stride: int = 3
    retention_days: int = 30
    redaction_enabled: bool = True
    alert_cooldown_seconds: int = 900
    conversation_ttl_seconds: int = 6 * 3600
    device_silence_hours: int = 24
    pairing_code_ttl_seconds: int = 600
    db_url: str = f"sqlite:///{(ROOT / 'data' / 'guardian.db').as_posix()}"
    model_path: str = str(ROOT / "models" / "baseline.joblib")
    rules_path: str = str(APP_DIR / "detection" / "rules" / "en.yaml")
    escalation_path: str = str(APP_DIR / "alerts" / "escalation.yaml")
    language: str = "en"
    operator_mode: bool = True
    simulator_mode: bool = True
    dashboard_password: str | None = Field(default=None, repr=False)
    public_url: str | None = None
    # The async LLM refinement runs only once the baseline risk reaches this level.
    llm_trigger_level: str = "MEDIUM"

    @property
    def llm_available(self) -> bool:
        return bool(self.anthropic_api_key)


# Settings the dashboard may change at runtime (operator only).
RUNTIME_EDITABLE = {"engine", "thresholds", "retention_days", "redaction_enabled", "alert_cooldown_seconds"}

_ENV_MAP: dict[str, tuple[str, type]] = {
    "GUARDIAN_ENGINE": ("engine", str),
    "GUARDIAN_LLM_MODEL": ("llm_model", str),
    "ANTHROPIC_API_KEY": ("anthropic_api_key", str),
    "GUARDIAN_DB_URL": ("db_url", str),
    "GUARDIAN_MODEL_PATH": ("model_path", str),
    "GUARDIAN_RULES_PATH": ("rules_path", str),
    "GUARDIAN_RETENTION_DAYS": ("retention_days", int),
    "GUARDIAN_ALERT_COOLDOWN_SECONDS": ("alert_cooldown_seconds", int),
    "GUARDIAN_CONVERSATION_TTL_SECONDS": ("conversation_ttl_seconds", int),
    "GUARDIAN_OPERATOR_MODE": ("operator_mode", bool),
    "GUARDIAN_SIMULATOR_MODE": ("simulator_mode", bool),
    "GUARDIAN_DASHBOARD_PASSWORD": ("dashboard_password", str),
    "GUARDIAN_PUBLIC_URL": ("public_url", str),
    "GUARDIAN_LANGUAGE": ("language", str),
}


def _coerce(raw: str, typ: type) -> Any:
    if typ is bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return typ(raw)


def load_settings(env: dict[str, str] | None = None, config_file: Path | None = None) -> Settings:
    env = dict(os.environ) if env is None else env
    data: dict[str, Any] = {}
    cfg = config_file or Path(env.get("GUARDIAN_CONFIG", APP_DIR / "config" / "guardian.yaml"))
    if cfg.exists():
        data.update(yaml.safe_load(cfg.read_text(encoding="utf-8")) or {})
    for var, (field, typ) in _ENV_MAP.items():
        if env.get(var):
            data[field] = _coerce(env[var], typ)
    return Settings(**data)


class SettingsHolder:
    """Process-wide settings with runtime overrides."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._base = load_settings()
        self._overrides: dict[str, Any] = {}
        self._current = self._base

    def get(self) -> Settings:
        return self._current

    def reload(self, env: dict[str, str] | None = None) -> Settings:
        with self._lock:
            self._base = load_settings(env)
            return self._rebuild()

    def replace(self, settings: Settings) -> Settings:
        """Swap in an explicit Settings object (used by tests and the app factory)."""
        with self._lock:
            self._base = settings
            self._overrides = {}
            return self._rebuild()

    def apply_overrides(self, overrides: dict[str, Any]) -> Settings:
        bad = set(overrides) - RUNTIME_EDITABLE
        if bad:
            raise ValueError(f"not runtime-editable: {sorted(bad)}")
        with self._lock:
            candidate = dict(self._overrides)
            candidate.update(overrides)
            merged = self._base.model_dump()
            merged.update(candidate)
            Settings(**merged)  # validate before committing
            self._overrides = candidate
            return self._rebuild()

    @property
    def overrides(self) -> dict[str, Any]:
        return dict(self._overrides)

    def _rebuild(self) -> Settings:
        merged = self._base.model_dump()
        merged.update(self._overrides)
        self._current = Settings(**merged)
        return self._current


settings_holder = SettingsHolder()


def get_settings() -> Settings:
    return settings_holder.get()
