import json
import threading
import time
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import session_scope
from app.ingest.sources.scenarios import load_scenario
from app.main import create_app
from app.models import MAX_EXCERPT_CHARS, Device, utcnow
from app.privacy.redact import contains_pii


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def scenario_payload(name: str):
    sc = load_scenario(name)
    return [
        {"sender": "Kid" if m["as"] == "child" else "Stranger", "text": m["text"]} for m in sc["messages"]
    ]


def pair_device(client) -> str:
    code = client.post("/api/devices/pair").json()["code"]
    r = client.post("/api/devices/claim", json={"code": code, "name": "Test phone"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def event(conv: str, text: str, is_child: bool, i: int = 0):
    return {
        "platform": "Roblox",
        "conversation_id": conv,
        "sender_hash": "kidhash" if is_child else "otherhash",
        "is_child": is_child,
        "text": text,
        "event_id": f"e{i}",
    }


def wait_for(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def test_api_analyze(client):
    r = client.post("/api/analyze", json={"messages": scenario_payload("grooming"), "child": "Kid"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["level"] in ("HIGH", "CRITICAL")
    assert body["timeline"] and body["explanation"]
    assert "pic of you" not in r.text  # assessments carry no message text
    r = client.post("/api/analyze", json={"messages": scenario_payload("benign"), "child": "Kid"})
    assert r.json()["level"] == "LOW"
    assert (
        client.post(
            "/api/analyze", json={"messages": scenario_payload("benign"), "child": "Nobody"}
        ).status_code
        == 422
    )


def test_dashboard_pages_render(client):
    for path in (
        "/alerts",
        "/analyze",
        "/simulator",
        "/devices",
        "/settings",
        "/partials/alerts",
        "/healthz",
    ):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:300])
    assert "Simulated data" in client.get("/simulator").text


def test_dashboard_analyze_flow(client):
    text = "\n".join(f"{m['sender']}: {m['text']}" for m in scenario_payload("grooming"))
    r = client.post("/analyze", data={"text": text})
    assert r.status_code == 200 and "Which participant is the child?" in r.text
    r = client.post("/analyze", data={"text": text, "child": "Kid", "platform": "Roblox"})
    assert r.status_code == 200
    assert "Assessment" in r.text and "<svg" in r.text and "Secrecy" in r.text
    assert "Other party 1" in r.text  # redacted names in operator transcript
    r = client.post(
        "/analyze",
        files={"upload": ("chat.json", json.dumps(scenario_payload("benign")), "application/json")},
        data={"child": "Kid"},
    )
    assert r.status_code == 200 and "LOW" in r.text


def test_ingest_http_requires_token(client):
    assert client.post("/api/events/message", json=event("x", "hi", False)).status_code == 401
    token = pair_device(client)
    r = client.post(
        "/api/events/message", json=event("x", "hi", False), headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 202 and r.json()["accepted"]


def test_pairing_code_single_use_and_revoke(client):
    code = client.post("/api/devices/pair").json()["code"]
    assert client.post("/api/devices/claim", json={"code": code}).status_code == 200
    assert client.post("/api/devices/claim", json={"code": code}).status_code == 400
    assert client.post("/api/devices/claim", json={"code": "ZZZZ-ZZZZ"}).status_code == 400
    before = {d["id"] for d in client.get("/api/devices").json()}
    token = pair_device(client)
    (new_id,) = {d["id"] for d in client.get("/api/devices").json()} - before
    hdr = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/devices/heartbeat", headers=hdr).status_code == 200
    assert client.post(f"/api/devices/{new_id}/revoke").status_code == 200
    assert client.post("/api/devices/heartbeat", headers=hdr).status_code == 401
    status = next(d["status"] for d in client.get("/api/devices").json() if d["id"] == new_id)
    assert status == "revoked"


def test_silent_device_flagged(client):
    pair_device(client)
    with session_scope() as s:
        for d in s.query(Device).filter(Device.platform == "android", Device.status == "active").all():
            d.last_seen = utcnow() - timedelta(hours=30)
            s.add(d)
        s.commit()
    statuses = {d["status"] for d in client.get("/api/devices").json() if d["platform"] == "android"}
    assert "silent" in statuses
    assert "No contact for over" in client.get("/devices").text


def test_websocket_ingest_produces_alert(client):
    token = pair_device(client)
    conv = f"ws-{uuid.uuid4().hex[:8]}"
    sc = load_scenario("grooming")
    with client.websocket_connect("/ws/ingest", headers={"Authorization": f"Bearer {token}"}) as ws:
        assert ws.receive_json()["type"] == "ready"
        for i, m in enumerate(sc["messages"]):
            ws.send_json({"type": "event", "event": event(conv, m["text"], m["as"] == "child", i)})
            ack = ws.receive_json()
            assert ack["type"] == "ack" and ack["event_id"] == f"e{i}"
        key = ack["conversation_key"]
        ws.send_json({"type": "event", "event": {"bad": 1}})
        assert ws.receive_json()["type"] == "nack"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"

    alerts = wait_for(
        lambda: [
            a
            for a in client.get("/api/alerts").json()
            if a["conversation_id"] == key and a["level"] in ("HIGH", "CRITICAL")
        ]
    )
    for a in client.get("/api/alerts").json():
        assert len(a["excerpts"]) <= 2
        for e in a["excerpts"]:
            assert len(e) <= MAX_EXCERPT_CHARS and not contains_pii(e)
    detail = client.get(f"/alerts/{alerts[0]['id']}")
    assert detail.status_code == 200 and "Verify numbers before relying on them" in detail.text
    assert "1326" in detail.text and "never sends reports automatically" in detail.text


def test_websocket_auth_via_first_frame_and_rejection(client):
    with client.websocket_connect("/ws/ingest") as ws:
        ws.send_json({"type": "auth", "token": "nope"})
        assert ws.receive_json() == {"type": "error", "error": "unauthorized"}
    token = pair_device(client)
    with client.websocket_connect("/ws/ingest") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "ready"


def test_sse_alert_stream(client):
    token = pair_device(client)
    received: list[str] = []

    def consume():
        with client.stream("GET", "/api/alerts/stream?max_events=2") as r:
            for line in r.iter_lines():
                received.append(line)

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    wait_for(
        lambda: (
            client.get("/healthz").json()
            and any("hello" in x for x in received)
            or client.app.state.broker.n_subscribers > 0
        )
    )
    conv = f"sse-{uuid.uuid4().hex[:8]}"
    for i, m in enumerate(load_scenario("grooming")["messages"]):
        client.post(
            "/api/events/message",
            json=event(conv, m["text"], m["as"] == "child", i),
            headers={"Authorization": f"Bearer {token}"},
        )
    t.join(timeout=10)
    assert not t.is_alive(), "SSE stream did not deliver an alert"
    assert "event: hello" in received and "event: alert" in received
    data = json.loads(next(x for x in received if x.startswith("data:") and '"level"' in x)[5:])
    assert data["level"] in ("MEDIUM", "HIGH", "CRITICAL") and len(data["excerpts"]) <= 2


def test_settings_update_and_validation(client):
    r = client.post("/api/settings", json={"retention_days": 14, "alert_cooldown_seconds": 60})
    assert r.status_code == 200 and r.json()["retention_days"] == 14
    bad = client.post("/api/settings", json={"thresholds": {"medium": 80, "high": 50, "critical": 90}})
    assert bad.status_code == 422
    r = client.post(
        "/settings",
        data={
            "engine": "baseline",
            "medium": 40,
            "high": 70,
            "critical": 85,
            "retention_days": 30,
            "alert_cooldown_seconds": 900,
            "redaction_enabled": "on",
        },
    )
    assert r.status_code == 200 and "Saved." in r.text
    assert client.get("/api/settings").json()["retention_days"] == 30


def test_live_partial_and_scenarios(client):
    assert [s["name"] for s in client.get("/api/simulator/scenarios").json()] == [
        "benign",
        "borderline",
        "grooming",
    ]
    assert client.get("/api/simulator/scenarios/../etc").status_code == 404
    r = client.get("/partials/live", params={"conversation": "missing"})
    assert r.status_code == 200 and "Waiting" in r.text
