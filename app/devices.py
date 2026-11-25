"""Device pairing and authentication.

Flow: the guardian dashboard calls POST /api/devices/pair, which creates a one-time pairing code
(shown as a QR code, expires after `pairing_code_ttl_seconds`). The Android app scans it and calls
POST /api/devices/claim with the code; it receives a device token once. Only SHA-256 hashes of
codes and tokens are stored.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.models import Device, PairingCode, utcnow
from app.privacy.hashing import hash_token, new_token

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def _code() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def create_pairing_code(s: Session, ttl_seconds: int, device_name: str | None = None) -> tuple[str, datetime]:
    code = _code()
    expires = utcnow() + timedelta(seconds=ttl_seconds)
    s.add(PairingCode(code_hash=hash_token(code), expires_at=expires, device_name=device_name))
    s.commit()
    return code, expires


class PairingError(ValueError):
    pass


def claim(s: Session, code: str, name: str, platform: str) -> tuple[Device, str]:
    row = s.get(PairingCode, hash_token(code.strip().upper()))
    if row is None or row.used or row.expires_at < utcnow():
        raise PairingError("invalid or expired pairing code")
    row.used = True
    token = new_token()
    dev = Device(
        name=(name or row.device_name or "Device")[:64],
        platform=(platform or "android")[:32],
        token_hash=hash_token(token),
        last_seen=utcnow(),
    )
    s.add(row)
    s.add(dev)
    s.commit()
    s.refresh(dev)
    return dev, token


def authenticate(s: Session, token: str | None) -> Device | None:
    if not token:
        return None
    dev = s.exec(select(Device).where(Device.token_hash == hash_token(token))).first()
    if dev is None or dev.status != "active":
        return None
    return dev


def touch(s: Session, device_id: str, message: bool = False) -> None:
    dev = s.get(Device, device_id)
    if dev is None:
        return
    now = utcnow()
    dev.last_seen = now
    if message:
        dev.last_message_at = now
    s.add(dev)
    s.commit()


def ensure_simulator_device(s: Session) -> tuple[Device, str]:
    """The dashboard's live simulator authenticates like any other source. A fresh token is issued
    on every process start and only ever lives in memory / the operator's page."""
    token = new_token()
    dev = s.exec(select(Device).where(Device.platform == "simulator")).first()
    if dev is None:
        dev = Device(name="Live simulator", platform="simulator", token_hash=hash_token(token))
    else:
        dev.token_hash = hash_token(token)
        dev.status = "active"
    s.add(dev)
    s.commit()
    s.refresh(dev)
    return dev, token


def device_status(dev: Device, silence_hours: int, now: datetime | None = None) -> str:
    if dev.status != "active":
        return dev.status
    now = now or utcnow()
    if dev.platform != "simulator" and (
        dev.last_seen is None or now - dev.last_seen > timedelta(hours=silence_hours)
    ):
        return "silent"
    return "active"


def list_devices(s: Session, silence_hours: int) -> list[dict]:
    out = []
    for d in s.exec(select(Device).order_by(Device.created_at)).all():
        out.append(
            {
                "id": d.id,
                "name": d.name,
                "platform": d.platform,
                "status": device_status(d, silence_hours),
                "created_at": d.created_at,
                "last_seen": d.last_seen,
                "last_message_at": d.last_message_at,
            }
        )
    return out


def revoke(s: Session, device_id: str) -> bool:
    dev = s.get(Device, device_id)
    if dev is None:
        return False
    dev.status = "revoked"
    s.add(dev)
    s.commit()
    return True
