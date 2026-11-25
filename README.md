# SafeChat Guardian

A defensive child-safety prototype. It scores how far a chat conversation has moved along the
known stages of online grooming and alerts a guardian with a privacy-preserving summary. It scores
messages in real time from an Android companion app or the built-in simulator, and also accepts
uploaded chat logs.

> **Detection and alerting only.** SafeChat Guardian never replies to, contacts or engages the other
> party, and never files reports automatically. Alerts never contain transcripts: only a risk level,
> the detected stages and at most two redacted excerpts of 120 characters or fewer.
>
> **Use:** develop with synthetic data and the simulator only. Real monitoring is only for a child the
> guardian is legally responsible for, with the age-appropriate notice shown to the child. This is a
> prototype and has not had a legal review. Read [docs/PRIVACY.md](docs/PRIVACY.md) and
> [docs/ETHICS.md](docs/ETHICS.md) first.

## How it works

```
Android app / simulator --(WSS /ws/ingest or POST /api/events/message)--> asyncio queue --> StreamEngine
                                                                                                |
  Layer 1  signals.py       YAML red-flag rules (app/detection/rules/en.yaml)                    |
  Layer 2  classifiers/     newest window -> stage probabilities (baseline | llm | ensemble)     |
  Layer 3  trajectory.py    evidence + ordered progression + velocity + hard floors -> 0-100     |
                                                                                                v
                               level rise? (cooldown) --> redacted alert --> SSE --> guardian inbox
```

* **Stages:** FRIENDSHIP, RISK_ASSESSMENT, TRUST_EXCLUSIVITY, ISOLATION, SECRECY, PLATFORM_MOVE,
  ESCALATION, NONE ([app/detection/stages.py](app/detection/stages.py)).
* **Levels:** LOW < 40 <= MEDIUM < 70 <= HIGH < 85 <= CRITICAL (configurable).
* **Hard floor:** an ESCALATION signal together with SECRECY or PLATFORM_MOVE is at least HIGH.
* **Explainable:** every score comes with its components (base, order bonus, speed bonus, floor),
  the top signals and windows, and a plain-language explanation.
* **Streaming equals batch:** both replay the same scorer, and the scorer is a pure fold over
  text-free observations, so live scores match batch scores.

## Backend setup

Requires Python 3.11+.

```bash
make install        # python -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"
make seed           # python scripts/generate_synthetic.py        -> data/synthetic/{train,val,test}.jsonl
make train          # python scripts/train_baseline.py            -> models/baseline.joblib
make eval           # python scripts/evaluate.py                  -> reports/latest.md
make test           # python -m pytest && python -m ruff check .
make run            # python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows without `make`: run the commands after the `#`, using `.venv\Scripts\python` instead of
`.venv/bin/python`.

Open http://127.0.0.1:8000. The pages are **Alerts**, **Analyze** (paste or upload a
.txt/.json/.csv chat), **Simulator**, **Devices** and **Settings**.

### Configuration

Defaults are in [app/config/guardian.yaml](app/config/guardian.yaml). Environment variables override
them:

| Variable | Default | Meaning |
|---|---|---|
| `GUARDIAN_ENGINE` | `baseline` | `baseline`, `llm` or `ensemble` |
| `ANTHROPIC_API_KEY` | unset | Enables the LLM rubric engine. Without it the app runs fully offline |
| `GUARDIAN_LLM_MODEL` | `claude-sonnet-5-5` | Model for the LLM engine |
| `GUARDIAN_DASHBOARD_PASSWORD` | unset | HTTP Basic auth for the dashboard and API. **Set it whenever the server is reachable from other devices** |
| `GUARDIAN_PUBLIC_URL` | request URL | Server address encoded in the pairing QR (for example your LAN IP or tunnel URL) |
| `GUARDIAN_OPERATOR_MODE` | `1` | Shows the simulator, settings and the (redacted) demo transcript |
| `GUARDIAN_SIMULATOR_MODE` | `1` | `1` shows the "Simulated data" banner. `0` is live mode, with connected sources in the header |
| `GUARDIAN_RETENTION_DAYS` | `30` | Retention purge (runs on startup and daily) |
| `GUARDIAN_DB_URL` | `sqlite:///data/guardian.db` | Database URL |
| `GUARDIAN_HASH_SALT` | random file `data/.hash_salt` | Salt for hashing participant handles |

The operator can change engine, thresholds, retention, cooldown and transcript redaction at runtime
on the Settings page.

### LLM engine (optional)

`llm` / `ensemble` use Claude via the `anthropic` SDK:

* **Redaction:** PII is redacted before anything is sent.
* **Strict output:** the model returns strict JSON (structured outputs) with `{stage_probs, rationale, evidence_message_ids}`.
* **Safety prompt:** the system prompt frames the task as child-safety classification and forbids reproducing explicit content.
* **Caching:** results are cached by window hash.
* **Fallback:** any error or refusal falls back to the baseline. A circuit breaker pauses the LLM after repeated failures.
* **Refusal retry:** server-side refusal fallback (`fallbacks: "default"`) is enabled on current models.

During streaming, the baseline scores every message immediately. The LLM is called asynchronously
only once risk reaches MEDIUM, and the score is refined when it returns. The `ensemble` engine
averages the baseline and LLM probabilities.

### PAN-2012 (optional)

Put the corpus you obtained yourself in `data/pan2012/`. `make train` then adds weakly labeled
windows, and `make eval` reports PAN metrics. The project never downloads, bundles or prints PAN
text.

## Live simulator

Open **Simulator** and click one of the scenarios (benign gaming chat, borderline, clear grooming
trajectory), set the speed from 1x to 20x, or type as "stranger" and "child". Messages travel through
the same `/ws/ingest` WebSocket as a real phone. The risk gauge and stage timeline update after each
message, and alerts appear in the **Alerts** inbox live over SSE. You can opt in to browser
notifications there. All scenario text is synthetic and non-explicit.

## Android companion app

`android/` is Kotlin + Jetpack Compose, minSdk 26, targetSdk 35, Gradle Kotlin DSL. It is a
read-only source. A `NotificationListenerService` reads chat notifications from allowlisted apps
(`app/src/main/assets/allowlist.json`):

* **Parsing:** MessagingStyle first, then title/text.
* **Dedup:** re-posted notifications are deduplicated.
* **Hashing:** sender names and conversation IDs are hashed with a per-install salt on the phone.
* **Queue:** events wait in a SQLCipher-encrypted Room queue.
* **Sending:** a WorkManager job sends them over the authenticated WebSocket and deletes each one as soon as it is acknowledged.

The app has no screen that shows message contents.

### Build

Requires JDK 17 and the Android SDK (platform 35). Android Studio provides both.

```bash
cd android
./gradlew test assembleDebug          # JVM unit tests + app/build/outputs/apk/debug/app-debug.apk
./gradlew connectedDebugAndroidTest   # instrumented "Simulate chat" flow test (device/emulator)
```

### Pair a phone

1. Start the backend so that the phone can reach it (see networking below), and set
   `GUARDIAN_PUBLIC_URL` to that address.
2. Dashboard, **Devices**, **Pair a phone**: this shows a one-time QR code that expires in 10 minutes.
3. Install the APK on the child's phone. Complete the guardian consent screen, then show the
   **child notice** screen to the child.
4. In the app, choose **Pair with dashboard**, scan the QR code (or type the server address and code).

### Guide: permissions on the phone

The app's home screen is a checklist. Each item has a button that opens the right settings page.

1. **Notification access.** Turn on SafeChat Guardian in *Settings, Notifications, Device & app
   notifications* (the name varies by phone).
   * **Android 13+ with a sideloaded APK:** the toggle may be greyed out ("Restricted setting"). Open
     *App info* for SafeChat Guardian, tap the **⋮** menu, choose **Allow restricted settings** and
     confirm with your PIN. Then go back and turn on notification access.
2. **Battery optimization off.** *Settings, Apps, SafeChat Guardian, Battery:* choose
   **Unrestricted** (or "Don't optimize"). Without this, Android may stop the listener and the
   dashboard will flag the device as *silent*.
3. **Notifications allowed** (Android 13+), so the persistent "SafeChat Guardian protection is on"
   notification can be shown.
4. **Monitored apps:** switch individual chat apps on or off.

The dashboard's **Devices** panel shows each phone's last-seen time. It warns when a phone has been
silent for more than 24 hours, which usually means notification access was revoked or the app was
stopped.

### Phone-to-PC networking (development)

**Option A: same Wi-Fi (debug build only).**
1. Find the PC's LAN IP (`ipconfig` on Windows, `ip addr` on Linux/macOS), for example `192.168.1.20`.
2. Run the server on the LAN:
   ```bash
   GUARDIAN_DASHBOARD_PASSWORD=change-me GUARDIAN_PUBLIC_URL=http://192.168.1.20:8000 make run-lan
   ```
   On Windows, allow Python through the firewall for *private* networks.
3. Pair with `http://192.168.1.20:8000`. Only **debug** builds accept `http://`, and only for private
   LAN addresses (10.x, 172.16-31.x, 192.168.x, the emulator host `10.0.2.2`). Release builds require
   HTTPS/WSS.

**Option B: tunnel (works for debug and release).**
```bash
make run                                             # localhost:8000
cloudflared tunnel --url http://localhost:8000       # prints https://<random>.trycloudflare.com
GUARDIAN_PUBLIC_URL=https://<random>.trycloudflare.com GUARDIAN_DASHBOARD_PASSWORD=change-me make run
```
Pair with the `https://` URL. The WebSocket automatically uses `wss://`. A tunnel exposes the
dashboard to the internet, so always set `GUARDIAN_DASHBOARD_PASSWORD`.

### Debug tools

Debug builds have **Debug: Simulate chat**. It posts fake MessagingStyle notifications from a
synthetic scenario on a test channel, so you can test the whole phone, backend and alert path without
any real chats. The listener accepts the app's own package only in debug builds and only on that
channel.

## Screenshots

_Placeholder: add screenshots of the Alerts inbox, an assessment, the simulator and the Android status
checklist here._

## Evaluation

`make eval` writes `reports/latest.md`. The first run, with the baseline on the held-out synthetic
test split, is in [reports/](reports/). The synthetic data is template-generated: about 25% of the
phrasings appear only in the test split, but numbers on real chats will be lower.

## Known limitations

**Notification-based capture (Android)**
* It only sees what appears in notifications. Muted chats, chats the child has open on screen, and
  in-game chat in games that show it without notifications are missed.
* Long messages may be truncated by the app that posts the notification.
* There is no iPhone support (iOS does not allow third-party notification reading).
* A Play Store release would need Google's parental-control / notification-listener disclosure and
  review. Sideloaded installs need the "Allow restricted settings" step on Android 13+.

**Detection**
* English only. The text layer is isolated (`app/textproc`, `app/detection/rules/<lang>.yaml`) so
  Taglish can be added.
* Trained on synthetic templates. Real grooming language is more varied and adaptive, and early
  stages (friendship, risk assessment) are often indistinguishable from normal chat.
* Rule-based redaction is best effort. Alerts limit exposure further, to 2 excerpts of 120 characters
  or fewer.

**Prototype security**
* The dashboard has a single shared password and no per-user accounts.
* SQLite is not encrypted at rest. Use SQLCipher or disk encryption in production (see `app/db.py`).
* The Android build has not been tested on many OEM skins.

## Repository layout

```
app/            FastAPI app, detection layers, privacy, alerts, templates, static
android/        Android companion app (Kotlin/Compose)
scripts/        generate_synthetic.py, train_baseline.py, evaluate.py
data/synthetic/ generated labeled JSONL (deterministic; regenerate with `make seed`)
docs/           PRIVACY.md, ETHICS.md
reports/        evaluation reports
tests/          pytest suite
```
