"""SafeChat Guardian - FastAPI app: API, real-time ingestion, SSE and the guardian dashboard.

Detection and alerting only: nothing in this app replies to, contacts, or engages the other
party, and nothing submits reports anywhere automatically.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import Any

import qrcode
import qrcode.image.svg
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import select

from app import devices as devmod
from app.alerts.broker import Broker, sse_stream
from app.alerts.escalation import load_resources
from app.config import APP_DIR, RUNTIME_EDITABLE, Settings, get_settings, settings_holder
from app.db import init_engine, session_scope
from app.detection import engines
from app.detection.pipeline import analyze as run_analysis
from app.detection.stages import STAGE_DEFS, Stage
from app.detection.stream import StreamEngine
from app.ingest.parsers import ChatMessage, ParseError, parse_auto, participants, to_scored
from app.ingest.sources.scenarios import list_scenarios, load_scenario
from app.models import Alert, AppSetting, AssessmentRow, MessageEvent, utcnow
from app.privacy.redact import redact
from app.privacy.retention import purge
from app.security import bearer_token, dashboard_auth, device_auth, operator_required
from app.ui import gauge_svg, stage_legend, timeline_svg

log = logging.getLogger("guardian")
MAX_UPLOAD_BYTES = 2_000_000
MAX_MESSAGES = 5000
TOUCH_INTERVAL_S = 30


# ---------------------------------------------------------------------------------------------
# App factory / lifespan
# ---------------------------------------------------------------------------------------------
def _load_overrides() -> None:
    with session_scope() as s:
        row = s.get(AppSetting, "runtime_overrides")
        if row and isinstance(row.value, dict):
            try:
                settings_holder.apply_overrides({k: v for k, v in row.value.items() if k in RUNTIME_EDITABLE})
            except (ValueError, ValidationError):
                log.warning("ignoring invalid saved settings")


def _save_overrides() -> None:
    with session_scope() as s:
        row = s.get(AppSetting, "runtime_overrides") or AppSetting(key="runtime_overrides")
        row.value = settings_holder.overrides
        s.add(row)
        s.commit()


def _run_purge() -> dict[str, int]:
    st = get_settings()
    with session_scope() as s:
        return purge(s, st.retention_days, st.conversation_ttl_seconds)


async def _daily_purge() -> None:
    while True:
        await asyncio.sleep(24 * 3600)
        with suppress(Exception):
            log.info("retention purge: %s", _run_purge())


@asynccontextmanager
async def lifespan(app: FastAPI):
    st = get_settings()
    init_engine(st.db_url)
    _load_overrides()
    log.info("retention purge on startup: %s", _run_purge())
    with session_scope() as s:
        sim_dev, sim_token = devmod.ensure_simulator_device(s)
    app.state.simulator_device_id = sim_dev.id
    app.state.simulator_token = sim_token
    await app.state.engine.start()
    purge_task = asyncio.create_task(_daily_purge())
    try:
        yield
    finally:
        purge_task.cancel()
        await app.state.engine.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="SafeChat Guardian", version="0.1.0", lifespan=lifespan)
    app.state.broker = Broker()
    app.state.engine = StreamEngine(broker=app.state.broker)
    app.state.last_touch = {}
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    _register_api(app)
    _register_ws(app)
    _register_dashboard(app)
    return app


def _touch(app: FastAPI, device_id: str, message: bool) -> None:
    now = time.monotonic()
    last = app.state.last_touch.get((device_id, message), 0.0)
    if now - last < TOUCH_INTERVAL_S:
        return
    app.state.last_touch[(device_id, message)] = now
    with session_scope() as s:
        devmod.touch(s, device_id, message=message)


def _server_url(request: Request) -> str:
    return (get_settings().public_url or str(request.base_url)).rstrip("/")


def _alert_dict(a: Alert) -> dict[str, Any]:
    return {
        "id": a.id,
        "conversation_id": a.conversation_id,
        "platform": a.platform,
        "level": a.level,
        "score": a.score,
        "created_at": a.created_at.isoformat(),
        "stages": a.stages,
        "stage_labels": [STAGE_DEFS[Stage(x)].label for x in a.stages if x in Stage.__members__],
        "excerpts": a.excerpts,
        "suggested_actions": a.suggested_actions,
        "summary": a.summary,
        "acknowledged": a.acknowledged,
    }


def _list_alerts(limit: int = 100) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.exec(select(Alert).order_by(Alert.created_at.desc()).limit(limit)).all()
        return [_alert_dict(a) for a in rows]


def _save_assessment(a) -> None:
    with session_scope() as s:
        s.add(
            AssessmentRow(
                conversation_id=a.conversation_id,
                engine=a.engine,
                score=a.score,
                level=a.level,
                stages_detected=a.stages_detected,
                timeline=[p.model_dump() for p in a.timeline],
                components=a.components,
                explanation=a.explanation,
            )
        )
        s.commit()


def _qr_svg(payload: str) -> Markup:
    img = qrcode.make(payload, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode("utf-8")
    return Markup(svg[svg.find("<svg") :])


def _pair(request: Request) -> dict[str, Any]:
    st = get_settings()
    with session_scope() as s:
        code, expires = devmod.create_pairing_code(s, st.pairing_code_ttl_seconds)
    server = _server_url(request)
    payload = json.dumps({"v": 1, "server": server, "code": code}, separators=(",", ":"))
    return {"code": code, "expires_at": expires.isoformat(), "server_url": server, "qr_payload": payload}


# ---------------------------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------------------------
class AnalyzeMessage(BaseModel):
    sender: str = Field(min_length=1, max_length=100)
    text: str = Field(max_length=4000)
    timestamp: str | None = None


class AnalyzeRequest(BaseModel):
    messages: list[AnalyzeMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    child: str
    platform: str | None = None
    engine: str | None = Field(default=None, pattern="^(baseline|llm|ensemble)$")


class ClaimRequest(BaseModel):
    code: str = Field(min_length=4, max_length=16)
    name: str = Field(default="Android phone", max_length=64)
    platform: str = Field(default="android", max_length=32)


class SettingsUpdate(BaseModel):
    engine: str | None = Field(default=None, pattern="^(baseline|llm|ensemble)$")
    thresholds: dict[str, float] | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    redaction_enabled: bool | None = None
    alert_cooldown_seconds: int | None = Field(default=None, ge=0, le=86400)


def _register_api(app: FastAPI) -> None:
    auth = [Depends(dashboard_auth)]

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        st = get_settings()
        return {"ok": True, "engine": engines.effective_engine(st), "stream": app.state.engine.stats()}

    @app.post("/api/analyze", dependencies=auth)
    def api_analyze(req: AnalyzeRequest) -> JSONResponse:
        from app.ingest.parsers import parse_timestamp

        msgs = [
            ChatMessage(index=i, sender=m.sender, text=m.text, timestamp=parse_timestamp(m.timestamp))
            for i, m in enumerate(req.messages)
        ]
        try:
            scored = to_scored(msgs, req.child)
        except ParseError as exc:
            raise HTTPException(422, str(exc)) from exc
        a = run_analysis(scored, f"upload:{uuid.uuid4().hex[:12]}", req.platform, engine=req.engine)
        _save_assessment(a)
        return JSONResponse(a.model_dump(mode="json", exclude={"signals"}))

    @app.get("/api/alerts", dependencies=auth)
    def api_alerts(limit: int = 100) -> list[dict[str, Any]]:
        return _list_alerts(min(max(limit, 1), 500))

    @app.get("/api/alerts/stream", dependencies=auth)
    async def api_alerts_stream(max_events: int | None = None) -> StreamingResponse:
        return StreamingResponse(
            sse_stream(app.state.broker, {"alert"}, max_events=max_events),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/live/stream", dependencies=auth)
    async def api_live_stream(
        conversation: str | None = None, max_events: int | None = None
    ) -> StreamingResponse:
        def flt(topic: str, data: dict) -> bool:
            return conversation is None or topic != "score" or data.get("conversation_id") == conversation

        return StreamingResponse(
            sse_stream(app.state.broker, {"score", "alert"}, max_events=max_events, filter_fn=flt),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/alerts/{alert_id}", dependencies=auth)
    def api_alert(alert_id: str) -> dict[str, Any]:
        with session_scope() as s:
            a = s.get(Alert, alert_id)
            if a is None:
                raise HTTPException(404, "alert not found")
            return _alert_dict(a)

    @app.post("/api/alerts/{alert_id}/ack", dependencies=auth)
    def api_alert_ack(alert_id: str) -> dict[str, Any]:
        with session_scope() as s:
            a = s.get(Alert, alert_id)
            if a is None:
                raise HTTPException(404, "alert not found")
            a.acknowledged = True
            s.add(a)
            s.commit()
            return _alert_dict(a)

    @app.post("/api/events/message", status_code=202)
    def api_event(event: MessageEvent, dev=Depends(device_auth)) -> dict[str, Any]:
        try:
            key = app.state.engine.submit(
                event, "device" if dev.platform != "simulator" else "simulator", dev.id
            )
        except asyncio.QueueFull as exc:
            raise HTTPException(503, "scoring queue full, retry later") from exc
        _touch(app, dev.id, message=True)
        return {"accepted": True, "event_id": event.event_id, "conversation_key": key}

    @app.post("/api/devices/pair", dependencies=auth)
    def api_pair(request: Request) -> dict[str, Any]:
        return _pair(request)

    @app.post("/api/devices/claim")
    def api_claim(req: ClaimRequest, request: Request) -> dict[str, Any]:
        with session_scope() as s:
            try:
                dev, token = devmod.claim(s, req.code, req.name, req.platform)
            except devmod.PairingError as exc:
                raise HTTPException(400, str(exc)) from exc
        server = _server_url(request)
        ws = server.replace("https://", "wss://").replace("http://", "ws://") + "/ws/ingest"
        return {"device_id": dev.id, "token": token, "ws_url": ws}

    @app.post("/api/devices/heartbeat")
    def api_heartbeat(dev=Depends(device_auth)) -> dict[str, Any]:
        with session_scope() as s:
            devmod.touch(s, dev.id)
        return {"ok": True, "server_time": utcnow().isoformat()}

    @app.get("/api/devices", dependencies=auth)
    def api_devices() -> list[dict[str, Any]]:
        with session_scope() as s:
            return devmod.list_devices(s, get_settings().device_silence_hours)

    @app.post("/api/devices/{device_id}/revoke", dependencies=auth)
    def api_revoke(device_id: str) -> dict[str, Any]:
        with session_scope() as s:
            if not devmod.revoke(s, device_id):
                raise HTTPException(404, "device not found")
        return {"revoked": device_id}

    @app.get("/api/settings", dependencies=[Depends(operator_required)])
    def api_settings_get() -> dict[str, Any]:
        st = get_settings()
        return {
            "engine": st.engine,
            "effective_engine": engines.effective_engine(st),
            "llm_available": st.llm_available,
            "thresholds": st.thresholds.model_dump(),
            "retention_days": st.retention_days,
            "redaction_enabled": st.redaction_enabled,
            "alert_cooldown_seconds": st.alert_cooldown_seconds,
        }

    @app.post("/api/settings", dependencies=[Depends(operator_required)])
    def api_settings_post(upd: SettingsUpdate) -> dict[str, Any]:
        changes = upd.model_dump(exclude_none=True)
        try:
            settings_holder.apply_overrides(changes)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, str(exc)) from exc
        _save_overrides()
        return api_settings_get()

    @app.get("/api/simulator/scenarios", dependencies=[Depends(operator_required)])
    def api_scenarios() -> list[dict]:
        return list_scenarios()

    @app.get("/api/simulator/scenarios/{name}", dependencies=[Depends(operator_required)])
    def api_scenario(name: str) -> dict:
        try:
            return load_scenario(name)
        except KeyError as exc:
            raise HTTPException(404, "unknown scenario") from exc


# ---------------------------------------------------------------------------------------------
# WebSocket ingestion
# ---------------------------------------------------------------------------------------------
def _register_ws(app: FastAPI) -> None:
    @app.websocket("/ws/ingest")
    async def ws_ingest(ws: WebSocket) -> None:
        """Protocol (JSON frames):
        -> {"type":"auth","token":...}   (only if no Authorization: Bearer header)
        <- {"type":"ready","device_id":...}
        -> {"type":"event","event":{MessageEvent}}   <- {"type":"ack","event_id":...,"conversation_key":...}
        -> {"type":"ping"}                            <- {"type":"pong"}
        """
        await ws.accept()
        token = bearer_token(ws.headers)
        try:
            if token is None:
                first = await asyncio.wait_for(ws.receive_json(), timeout=10)
                token = (
                    first.get("token") if isinstance(first, dict) and first.get("type") == "auth" else None
                )
            with session_scope() as s:
                dev = devmod.authenticate(s, token)
            if dev is None:
                await ws.send_json({"type": "error", "error": "unauthorized"})
                await ws.close(code=4401)
                return
            await ws.send_json({"type": "ready", "device_id": dev.id})
            _touch(app, dev.id, message=False)
            source = "simulator" if dev.platform == "simulator" else "device"
            while True:
                frame = await ws.receive_json()
                kind = frame.get("type") if isinstance(frame, dict) else None
                if kind == "ping":
                    _touch(app, dev.id, message=False)
                    await ws.send_json({"type": "pong"})
                    continue
                if kind != "event":
                    await ws.send_json({"type": "nack", "error": "unknown frame type"})
                    continue
                raw = frame.get("event") or {}
                try:
                    event = MessageEvent.model_validate(raw)
                    key = app.state.engine.submit(event, source, dev.id)
                except ValidationError:
                    await ws.send_json(
                        {"type": "nack", "event_id": raw.get("event_id"), "error": "invalid event"}
                    )
                    continue
                except asyncio.QueueFull:
                    await ws.send_json(
                        {"type": "nack", "event_id": event.event_id, "error": "busy", "retry": True}
                    )
                    continue
                _touch(app, dev.id, message=True)
                await ws.send_json({"type": "ack", "event_id": event.event_id, "conversation_key": key})
        except (WebSocketDisconnect, TimeoutError):
            return
        except (ValueError, json.JSONDecodeError):
            with suppress(Exception):
                await ws.close(code=1003)


# ---------------------------------------------------------------------------------------------
# Dashboard (Jinja2 + HTMX)
# ---------------------------------------------------------------------------------------------
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
LEVEL_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _ctx(request: Request, active: str, **kw: Any) -> dict[str, Any]:
    st = get_settings()
    sources = []
    if not st.simulator_mode:
        with session_scope() as s:
            sources = [
                d for d in devmod.list_devices(s, st.device_silence_hours) if d["platform"] != "simulator"
            ]
    return {
        "request": request,
        "active": active,
        "settings": st,
        "effective_engine": engines.effective_engine(st),
        "operator": st.operator_mode,
        "sources": sources,
        "stage_defs": STAGE_DEFS,
        **kw,
    }


def _assessment_view(a, st: Settings) -> dict[str, Any]:
    return {
        "a": a,
        "gauge": gauge_svg(a.score, a.level),
        "timeline": timeline_svg(a.timeline, st.thresholds),
        "legend": stage_legend(),
        "stage_labels": {k: STAGE_DEFS[Stage(k)].label for k in a.stage_evidence},
    }


def _register_dashboard(app: FastAPI) -> None:
    auth = [Depends(dashboard_auth)]
    op = [Depends(operator_required)]

    @app.get("/", dependencies=auth)
    def index() -> RedirectResponse:
        return RedirectResponse("/alerts", status_code=303)

    @app.get("/alerts", response_class=HTMLResponse, dependencies=auth)
    def alerts_page(request: Request):
        return templates.TemplateResponse(
            request, "alerts.html", _ctx(request, "alerts", alerts=_list_alerts())
        )

    @app.get("/partials/alerts", response_class=HTMLResponse, dependencies=auth)
    def alerts_partial(request: Request):
        return templates.TemplateResponse(
            request, "_alert_list.html", {"request": request, "alerts": _list_alerts()}
        )

    @app.get("/alerts/{alert_id}", response_class=HTMLResponse, dependencies=auth)
    def alert_detail(request: Request, alert_id: str):
        with session_scope() as s:
            a = s.get(Alert, alert_id)
            if a is None:
                raise HTTPException(404, "alert not found")
            alert = _alert_dict(a)
        res = load_resources(get_settings().escalation_path)
        return templates.TemplateResponse(
            request, "alert_detail.html", _ctx(request, "alerts", alert=alert, res=res)
        )

    @app.post("/alerts/{alert_id}/ack", response_class=HTMLResponse, dependencies=auth)
    def alert_ack(request: Request, alert_id: str):
        with session_scope() as s:
            a = s.get(Alert, alert_id)
            if a is None:
                raise HTTPException(404, "alert not found")
            a.acknowledged = True
            s.add(a)
            s.commit()
        return HTMLResponse('<span class="badge ok">Reviewed</span>')

    # -- analyze ----------------------------------------------------------------------------
    @app.get("/analyze", response_class=HTMLResponse, dependencies=auth)
    def analyze_page(request: Request):
        return templates.TemplateResponse(request, "analyze.html", _ctx(request, "analyze", form={}))

    @app.post("/analyze", response_class=HTMLResponse, dependencies=auth)
    async def analyze_submit(
        request: Request,
        text: str = Form(""),
        child: str = Form(""),
        platform: str = Form(""),
        engine: str = Form(""),
        upload: UploadFile | None = File(None),
    ):
        st = get_settings()
        filename = None
        content = text
        if upload is not None and upload.filename:
            raw = await upload.read(MAX_UPLOAD_BYTES + 1)
            if len(raw) > MAX_UPLOAD_BYTES:
                return _analyze_error(request, "File is larger than 2 MB.", text, child, platform)
            content = raw.decode("utf-8", errors="replace")
            filename = upload.filename
        if not content.strip():
            return _analyze_error(request, "Paste a chat or choose a file.", text, child, platform)
        try:
            msgs = parse_auto(content, filename)
        except ParseError as exc:
            return _analyze_error(request, f"Could not read the chat: {exc}", content, child, platform)
        if len(msgs) > MAX_MESSAGES:
            return _analyze_error(
                request, f"Chats are limited to {MAX_MESSAGES} messages.", content, child, platform
            )
        names = participants(msgs)
        if not child or child not in names:
            form = {"text": content, "platform": platform, "engine": engine}
            return templates.TemplateResponse(
                request,
                "analyze.html",
                _ctx(
                    request,
                    "analyze",
                    form=form,
                    participants=names,
                    error=None if not child else "Pick which participant is the child.",
                ),
            )
        scored = to_scored(msgs, child)
        eng = engine if engine in ("baseline", "llm", "ensemble") else None
        a = run_analysis(scored, f"upload:{uuid.uuid4().hex[:12]}", platform or None, engine=eng)
        _save_assessment(a)
        redacting = st.redaction_enabled
        other_names = [n for n in names if n != child]
        alias = {child: "Child", **{n: f"Other party {i + 1}" for i, n in enumerate(other_names)}}
        flagged = {h.message_id for h in a.signals}
        transcript = [
            {
                "i": m.index,
                "sender": alias[m.sender] if redacting else m.sender,
                "is_child": m.sender == child,
                "text": redact(m.text, names) if redacting else m.text,
                "flag": m.index in flagged,
            }
            for m in msgs
        ]
        view = _assessment_view(a, st)
        return templates.TemplateResponse(
            request,
            "assessment.html",
            _ctx(
                request,
                "analyze",
                transcript=transcript if st.operator_mode else None,
                redacting=redacting,
                **view,
            ),
        )

    def _analyze_error(request: Request, msg: str, text: str, child: str, platform: str):
        form = {"text": text, "child": child, "platform": platform}
        return templates.TemplateResponse(
            request, "analyze.html", _ctx(request, "analyze", form=form, error=msg), status_code=422
        )

    # -- simulator --------------------------------------------------------------------------
    @app.get("/simulator", response_class=HTMLResponse, dependencies=op)
    def simulator_page(request: Request):
        return templates.TemplateResponse(
            request,
            "simulator.html",
            _ctx(request, "simulator", scenarios=list_scenarios(), sim_token=app.state.simulator_token),
        )

    @app.get("/partials/live", response_class=HTMLResponse, dependencies=op)
    def live_partial(request: Request, conversation: str):
        st = get_settings()
        rt = app.state.engine.runtimes.get(conversation)
        if rt is None:
            return HTMLResponse('<p class="muted">Waiting for the first message…</p>')
        a = rt.scorer.assessment()
        return templates.TemplateResponse(
            request, "_live.html", {"request": request, **_assessment_view(a, st)}
        )

    # -- devices ----------------------------------------------------------------------------
    @app.get("/devices", response_class=HTMLResponse, dependencies=auth)
    def devices_page(request: Request):
        st = get_settings()
        with session_scope() as s:
            devs = devmod.list_devices(s, st.device_silence_hours)
        return templates.TemplateResponse(request, "devices.html", _ctx(request, "devices", devices=devs))

    @app.get("/partials/devices", response_class=HTMLResponse, dependencies=auth)
    def devices_partial(request: Request):
        st = get_settings()
        with session_scope() as s:
            devs = devmod.list_devices(s, st.device_silence_hours)
        return templates.TemplateResponse(
            request, "_devices.html", {"request": request, "devices": devs, "settings": st}
        )

    @app.post("/devices/pair", response_class=HTMLResponse, dependencies=auth)
    def devices_pair(request: Request):
        p = _pair(request)
        return templates.TemplateResponse(
            request, "_pairing.html", {"request": request, "p": p, "qr": _qr_svg(p["qr_payload"])}
        )

    @app.post("/devices/{device_id}/revoke", response_class=HTMLResponse, dependencies=auth)
    def devices_revoke(request: Request, device_id: str):
        with session_scope() as s:
            devmod.revoke(s, device_id)
        return devices_partial(request)

    # -- settings ---------------------------------------------------------------------------
    @app.get("/settings", response_class=HTMLResponse, dependencies=op)
    def settings_page(request: Request):
        return templates.TemplateResponse(
            request, "settings.html", _ctx(request, "settings", saved=False, error=None)
        )

    @app.post("/settings", response_class=HTMLResponse, dependencies=op)
    def settings_submit(
        request: Request,
        engine: str = Form(...),
        medium: float = Form(...),
        high: float = Form(...),
        critical: float = Form(...),
        retention_days: int = Form(...),
        alert_cooldown_seconds: int = Form(...),
        redaction_enabled: str | None = Form(None),
    ):
        error = None
        try:
            SettingsUpdate(
                engine=engine, retention_days=retention_days, alert_cooldown_seconds=alert_cooldown_seconds
            )
            settings_holder.apply_overrides(
                {
                    "engine": engine,
                    "thresholds": {"medium": medium, "high": high, "critical": critical},
                    "retention_days": retention_days,
                    "alert_cooldown_seconds": alert_cooldown_seconds,
                    "redaction_enabled": redaction_enabled is not None,
                }
            )
            _save_overrides()
        except (ValueError, ValidationError) as exc:
            error = str(exc).splitlines()[0]
        return templates.TemplateResponse(
            request, "settings.html", _ctx(request, "settings", saved=error is None, error=error)
        )


def _fmt_ago(dt) -> str:
    if dt is None:
        return "never"
    delta = utcnow() - dt
    if delta < timedelta(minutes=1):
        return "just now"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} min ago"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)} h ago"
    return f"{delta.days} d ago"


templates.env.filters["ago"] = _fmt_ago
templates.env.globals["LEVEL_ORDER"] = LEVEL_ORDER

app = create_app()
