"""Authentication helpers.

* Sources (Android app, simulator) authenticate with a per-device bearer token (stored hashed).
* The dashboard is protected with HTTP Basic auth when GUARDIAN_DASHBOARD_PASSWORD is set.
  Without it the server should only listen on localhost (the default `make run`).
* Operator-only features (simulator, settings, transcripts) require GUARDIAN_OPERATOR_MODE=1.
"""

from __future__ import annotations

import base64
import secrets

from fastapi import HTTPException, Request, status

from app.config import get_settings
from app.db import session_scope
from app.devices import authenticate
from app.models import Device


def _basic_ok(request: Request, password: str) -> bool:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    _, _, pw = decoded.partition(":")
    return secrets.compare_digest(pw.encode(), password.encode())


def dashboard_auth(request: Request) -> None:
    pw = get_settings().dashboard_password
    if pw and not _basic_ok(request, pw):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "authentication required",
            headers={"WWW-Authenticate": 'Basic realm="guardian"'},
        )


def operator_required(request: Request) -> None:
    dashboard_auth(request)
    if not get_settings().operator_mode:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "operator mode is disabled")


def bearer_token(request_headers) -> str | None:
    header = request_headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def device_auth(request: Request) -> Device:
    token = bearer_token(request.headers)
    with session_scope() as s:
        dev = authenticate(s, token)
    if dev is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid device token")
    return dev
