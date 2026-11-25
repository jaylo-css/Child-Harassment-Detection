"""Salted hashing for participant handles and conversation IDs."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from functools import lru_cache

from app.config import ROOT


@lru_cache(maxsize=1)
def _salt() -> bytes:
    env = os.environ.get("GUARDIAN_HASH_SALT")
    if env:
        return env.encode()
    path = ROOT / "data" / ".hash_salt"
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(32)
    path.write_bytes(salt)
    return salt


def hash_handle(handle: str, length: int = 16) -> str:
    digest = hmac.new(_salt(), handle.strip().lower().encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:length]


def hash_token(token: str) -> str:
    """Hash for device tokens / pairing codes (unsalted SHA-256 of a high-entropy secret)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
