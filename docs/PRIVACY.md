# SafeChat Guardian: Privacy and compliance by design

> **Not legal advice.** This document explains how the prototype's design relates to several laws and
> codes. It is not a legal opinion. **Real deployment needs review by a qualified lawyer** in each
> country where the app is used, and possibly a Data Protection Impact Assessment and registration with
> the regulator.

## 1. What the system does, and does not do

* It **detects and alerts only.** It never replies to, contacts or engages the other party. It never
  opens chats or acts on notifications, and **never files reports automatically**.
* A guardian (parent or legal guardian) installs and configures it, on a phone used by a child they
  are legally responsible for. The **child is told** that protection is on.
* A human, the guardian, decides what to do. The app offers guidance and a report *checklist*. Any
  report to the platform, the CICC, the police or NCMEC is made by the guardian, after the guardian
  has reviewed it.

## 2. Data flow and minimization

| Step | Data | Where | How long |
|---|---|---|---|
| Notification arrives on the child's phone | Notification from an **allowlisted chat app only** (default: Messenger, Discord, Roblox, WhatsApp, Telegram, Instagram, Snapchat, Mobile Legends). Banking, OTP and email apps are never read. | Phone memory | Parsed immediately |
| Parsing | Sender name, conversation title, message text, time | Phone memory | Discarded after hashing |
| Hashing (on the phone) | Sender and conversation are replaced by **salted HMAC-SHA256 hashes**. The salt is a per-install secret that never leaves the phone. | Phone | n/a |
| Offline queue | `{app name, hashed conversation ID, hashed sender ID, is_child flag, message text, timestamp, event ID}` | SQLCipher-encrypted Room DB. The key is in EncryptedSharedPreferences / Android Keystore | **Deleted as soon as the backend acknowledges it** (normally seconds; at most 5,000 events are queued while offline) |
| Transport | Same fields | HTTPS / WSS. Plain HTTP is only possible in debug builds, and only to LAN addresses | n/a |
| Scoring (backend) | Raw text of the **last 6 messages** of a conversation (the rolling window) | Memory, plus the conversation-state row in SQLite | Overwritten as new messages arrive. **Removed when the conversation is idle** (default 6 h), or at the retention cutoff |
| Stored scoring state | Scores, stage evidence, signal IDs and spans, message metadata (index, hashed sender, child flag, time). **No message text.** | SQLite | Retention period (default **30 days**) |
| Alerts | Risk level, score, stages, **at most 2 excerpts of 120 characters or fewer, redacted** | SQLite, shown in the guardian inbox | Retention period |
| Uploaded chats (Analyze page) | Parsed in memory for one request. Only the assessment (scores, stages, explanation, no text) is saved. | Memory / SQLite | Text: not stored. Assessment: retention period |
| Optional LLM engine | **Redacted** window text (6 messages) sent to the Anthropic API | Anthropic, under its API data policies | Results cached in memory by window hash |

**Redaction** ([app/privacy/redact.py](../app/privacy/redact.py)) covers phone numbers, emails,
@handles, "my snap/discord is ..." usernames, URLs, street addresses, barangays, school names,
"I live in ..." locations, introduced names and known participant names. It is applied before any
external API call and before rendering alerts, and alerts are re-checked before they are emitted.
Regex redaction is best effort, which is why alerts are also capped at 2 short excerpts.

**What the system never collects:** images, video, audio, contacts, location, screen contents, or
notifications from non-allowlisted apps. It processes text only, so it never handles images. This
matters under RA 11930: the system must not collect or store child sexual abuse or exploitation
material (CSAEM).

**Retention purge** ([app/privacy/retention.py](../app/privacy/retention.py)) runs on startup and
daily. It deletes alerts, assessments, message metadata and conversation states older than the
retention period, and strips raw-text buffers from idle conversations.

**Encryption at rest:** the phone queue is encrypted with SQLCipher. The prototype's server SQLite
file is **not** encrypted. For production, use SQLCipher on the server, or full-disk encryption plus
restricted file permissions (see [app/db.py](../app/db.py)).

## 3. Consent, transparency and roles

* **Guardian consent:** onboarding requires the guardian to confirm that (a) they are the child's
  parent or legal guardian, (b) they will tell the child, and (c) they understand alerts can be wrong.
* **Transparency to the child:** an age-appropriate screen explains, in plain words, that the app
  checks chat notifications for signs that someone is trying to trick or hurt them, and that a parent
  is told only about warning signs, not every message. A persistent, low-priority notification
  ("SafeChat Guardian protection is on") stays visible. The app **never hides its icon or disguises
  itself**.
* **Guardian's role:** the guardian receives summaries, not transcripts. The guidance says to talk
  calmly, not blame the child, preserve evidence and not confront the other person.
* **Human review before any report:** reports are always a manual, guardian-initiated action based on
  the checklist.
* **The other party:** their messages are processed to protect the child. They are identified only by
  a salted hash. Only redacted, minimal excerpts reach the guardian.

## 4. Mapping to laws and codes

### Philippines: Data Privacy Act of 2012 (RA 10173) and its IRR

| Principle / requirement | Design response |
|---|---|
| Transparency, legitimate purpose, proportionality (Sec. 11) | Single, stated purpose (child protection). Allowlisted apps only. Text only. Rolling 6-message window. Summaries instead of transcripts. |
| Lawful basis (Secs. 12-13) | The guardian's consent on behalf of the minor, plus the child's notice. Protection of the child's vital interests and safety may also be relevant, but this needs legal review. |
| Data minimization and retention | Hashing on the device, deletion on acknowledgement, purge after a configurable retention period (default 30 days). |
| Security measures (Secs. 20-21) | TLS, SQLCipher on the phone, hashed device tokens, one-time pairing codes, redaction. Production requires server-side encryption at rest, access control and logging. |
| Data subject rights (Secs. 16-18) | The guardian can review and acknowledge alerts, and data ages out. **To do for production:** export/erasure endpoints, a privacy notice and a contact for the data protection officer (DPO). |
| Personal information controller / DPO, NPC registration and breach notification | The deploying organization (or the guardian, in personal use) is the controller. Registration, DPO appointment and a 72-hour breach procedure are **deployment requirements**, not covered by the prototype. |

### Philippines: Anti-OSAEC and Anti-CSAEM Act (RA 11930, 2022)

* RA 11930 sets out online sexual abuse and exploitation of children (OSAEC) and CSAEM offences, and
  imposes duties on internet intermediaries, platforms and others. **Assess with counsel** whether a
  deployer is covered by any reporting or preservation duty.
* The design never stores or transmits images or video, and alerts carry redacted text only. This
  limits the risk of the system itself becoming a repository of CSAEM.
* Escalation resources point guardians to the CICC Inter-Agency Response Center (1326), PNP-WCPC, the
  NBI Cybercrime Division and Bantay Bata 163. The app guides **evidence preservation by the guardian**
  (screenshots) without collecting the material itself.

### United States: COPPA (16 CFR Part 312)

* COPPA applies to operators of services directed to children under 13 that collect children's
  personal information. A guardian-operated monitoring tool may still be in scope, depending on who
  operates the backend. If a company offers it as a service, expect requirements for **verifiable
  parental consent**, a clear notice, data minimization, security, and retention limits.
* Design alignment: parental setup and consent first, minimal data, short retention, no advertising,
  no profiling for other purposes, and no disclosure to third parties except the optional, redacted
  LLM processing (which needs to be in the notice and the vendor contract).

### EU GDPR Article 8, and the UK GDPR / Age Appropriate Design Code (Children's Code)

* **Art. 8** sets the age (13-16, depending on the member state) below which consent for information
  society services must come from the holder of parental responsibility. Here the guardian consents.
  The processing also covers the other party's data, which needs its own lawful basis (likely
  legitimate interests or vital interests; get legal review). A DPIA is likely required (Art. 35).
* **UK AADC standard 11 (parental controls):** give the child age-appropriate information about
  monitoring, and an **obvious sign when they are being monitored**. These are the child notice screen
  and the persistent "protection is on" notification.
* Other relevant AADC standards: best interests of the child (1), DPIA (2), transparency (4), data
  minimization (8), default settings (7: the most protective defaults, such as redaction on and
  30-day retention), and no detrimental use of data (5).

## 5. Optional LLM processing

* Disabled unless `ANTHROPIC_API_KEY` is set. Without a key, everything runs offline.
* Text is redacted before sending. Only 6-message windows are sent, and only when needed (in
  streaming, only after the baseline risk reaches MEDIUM).
* The system prompt forbids reproducing or extending explicit content. Rationales are redacted and
  truncated locally.
* For production, a data processing agreement with the provider and disclosure in the privacy notice
  are required. Check data-retention settings and regional requirements.

## 6. Production checklist (not done in the prototype)

- [ ] Legal review in each target country. DPIA. Privacy notice for guardians and an age-appropriate version for children.
- [ ] Server-side encryption at rest, per-guardian accounts with MFA, audit logging, backups with the same retention rules.
- [ ] Data subject request handling (export and erasure), a DPO contact, a breach-notification procedure.
- [ ] Vendor DPAs (hosting, LLM provider).
- [ ] Re-verify every escalation number in `app/alerts/escalation.yaml`.
- [ ] Google Play parental-control and notification-listener policy review before any store release.
