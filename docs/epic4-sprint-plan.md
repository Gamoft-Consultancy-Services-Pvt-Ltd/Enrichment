# Epic 4 — Lead Ingestion Sprint Plan

## Context

`modules/lead_ingestion/` is currently empty. Epic 4 builds the intake boundary of the platform: it receives raw events from multiple channels, validates authenticity, normalises to a standard format, filters noise, deduplicates contacts, checks tenant readiness, and writes a `Lead` record at `pipeline_stage = captured` — emitting `LeadReceived` for Epic 5 (enrichment/scoring) to pick up.

The original spec was written for a multi-team setup involving TypeScript/Inngest and AWS Secrets Manager. This repo is a Python modular monolith using ARQ + Groq + env/config. All conflicts have been reconciled — see the table below.

---

## Architecture Reconciliations (spec vs. codebase — all locked)

| # | Spec says | Resolution |
|---|---|---|
| 1 | Emit `lead/received` Inngest event | **Use ARQ + `LeadReceived` event** from `shared/events/schemas.py` (no sibling TS repo exists) |
| 2 | Import `ChannelConnection` from `modules/tenant_onboarding/db/models.py` | **Create `shared/channels/models.py`** — cross-module imports are forbidden by project rules |
| 3 | Pre-flight checks Persona/Signal/PromptRegistry separately | **Check ACTIVE `TenantConfig` with non-empty signals** via `get_active_config()` — `prompt_registry` was merged into `tenant_config` |
| 4 | Store tokens in AWS Secrets Manager (boto3) | **Encrypted DB column** — `ChannelConnection.credentials_encrypted`, AES-256-GCM, key from `CHANNEL_CREDENTIALS_ENCRYPTION_KEY` env var |
| 5 | `haiku_client.py` wraps Anthropic claude-haiku | **Extend existing `clients/groq_client.py`** with `classify_message()` — no new deps, no `import anthropic` anywhere |
| 6 | Meta Graph API v21.0 | Follow spec — no conflict |
| 7 | Google Forms via Apps Script webhook | **Removed** — tenants upload CSV/XLSX/doc files directly |

---

## Progress Tracker

| Sprint | Status | Gate |
|---|---|---|
| Sprint 1 — Foundation | ⬜ Not started | `make test-unit` + migration applies |
| Sprint 2 — Core Pipeline + File Upload | ⬜ Not started | File upload + idempotency tests pass |
| Sprint 3 — WhatsApp DM End-to-End | ⬜ Not started | Golden path + noise integration tests pass |
| Sprint 4 — Meta OAuth + Lead Ads | ⬜ Not started | OAuth state tests + Lead Ad golden path pass |
| Sprint 5 — Email + Sheets | ⬜ Not started | Email + Sheets integration tests pass |
| Sprint 6 — Router + Final Wiring | ⬜ Not started | `make ci` fully green |

Update status to ✅ Done / 🔄 In Progress as you go.

---

## Sprint 1 — Foundation

**Goal:** Every file every later sprint imports from is built, typed, and tested. Nothing else starts until this gate passes.

### Files to create

```
shared/channels/
├── __init__.py
├── models.py          ChannelConnection SQLAlchemy model
└── schemas.py         ChannelConnectionRead Pydantic v2 schema

modules/lead_ingestion/
├── __init__.py
├── config.py          META_GRAPH_API_VERSION = "v21.0", META_GRAPH_API_BASE
├── exceptions.py      HmacValidationError, PreFlightHaltError, DuplicateEventError,
│                      ChannelApiError, FilterClientError
├── schemas/
│   ├── __init__.py
│   ├── normalised_event.py   NormalisedChannelEvent Pydantic v2
│   ├── lead_form.py          LeadFormFields + STANDARD_FIELD_MAP + extra_fields
│   └── filter_result.py      FilterResult: classification enum + extracted fields
└── db/
    ├── __init__.py
    └── models.py      Lead, IntakeEventLog, LeadFormFieldMap, LeadTouchpoint
```

### Existing files to modify

| File | Change |
|---|---|
| `shared/events/schemas.py` | Add to `LeadSource` enum: `FACEBOOK`, `FACEBOOK_LEAD_AD`, `INSTAGRAM_LEAD_AD`, `FILE_UPLOAD` |
| `clients/groq_client.py` | Add `classify_message(text: str) -> dict[str, Any]` for two-stage filter Stage 2 (returns dict, not FilterResult — clients/ cannot import from modules/) |
| `core/config.py` | Add Settings fields: `channel_credentials_encryption_key: str`, `meta_app_id: str`, `meta_app_secret: str`, `meta_webhook_verify_token: str` |
| `pyproject.toml` | Add `cryptography` (AES-GCM for token encryption) |

### Migration

```bash
alembic revision --autogenerate -m "lead_ingestion_and_channel_connection_models"
# down_revision = "8fccb46cd58e"  (current head)
# Creates: channel_connections, leads, intake_event_logs, lead_form_field_maps, lead_touchpoints
alembic upgrade head
```

### Tests to write

- `tests/unit/lead_ingestion/test_schemas.py` — NormalisedChannelEvent, FilterResult, LeadFormFields round-trips
- `tests/unit/lead_ingestion/test_models.py` — Lead/IntakeEventLog field constraints
- `tests/unit/test_lead_source_enum.py` — new LeadSource values present

### Sprint 1 Gate

```bash
python -c "from modules.lead_ingestion.db.models import Lead, IntakeEventLog; print('OK')"
python -c "from shared.channels.models import ChannelConnection; print('OK')"
uv run alembic upgrade head && echo "Migration OK"
make test-unit
```

---

## Sprint 2 — Core Pipeline + File Upload

**Goal:** Build the full pipeline core (repository, pre-flight, dedup, logging, orchestrator) and validate it end-to-end with CSV/XLSX file upload — the simplest source, no webhook auth, no LLM filter. Team can load real data immediately. WhatsApp and all other channel sources plug into this same pipeline in later sprints.

### Files to create (module logic)

```
modules/lead_ingestion/
├── db/
│   └── repository.py       Async CRUD for Sprint 1 models + ChannelConnection reads/writes
├── normaliser.py            File row adapter: dict row → NormalisedChannelEvent
│                            (WhatsApp/Instagram/Facebook/email/sheets adapters added in later sprints)
├── pre_flight.py            check_pre_flight() → get_active_config(); raises PreFlightHaltError
├── deduplicator.py          phone → email → name+location; LeadTouchpoint on duplicate match
├── intake_logger.py         IntakeEventLog writer; ON CONFLICT (platform_event_id) DO NOTHING
├── pipeline.py              Orchestrator: preflight→dedup→log→construct LeadReceived
│                            Returns (Lead, LeadReceived | None)
│                            Filter stage not yet present — added in Sprint 3 for message sources
└── file_upload_handler.py   CSV/XLSX upload; sync ≤100 rows; ARQ job >100 rows
```

### Files to create (infrastructure layer)

```
workers/jobs/lead_ingestion.py   Partial — file upload job only:
                                   run_lead_capture_batch(ctx, payload_dict)
                                   Creates own DB session. Calls pipeline.run_capture() per row.
                                   Follows workers/jobs/onboarding.py pattern exactly.

api/lead_ingestion.py            Partial — file upload endpoint only:
                                   POST /channels/inbound/file-upload
```

### Existing files to modify (Sprint 2)

| File | Change |
|---|---|
| `workers/worker.py` | Register `run_lead_capture_batch` from `workers.jobs.lead_ingestion` |
| `main.py` | Partial include: file upload route only (full router wired in Sprint 6) |
| `pyproject.toml` | Add `openpyxl` |

### Key implementation rules

**`file_upload_handler.py`:**
- Accepted types: `.csv`, `.xlsx`, `.xls`; reject others → 422
- Max 10 MB, max 5,000 rows → 422
- Parse CSV via `csv.DictReader`; XLSX via `openpyxl`
- Map headers via `STANDARD_FIELD_MAP` (e.g. "Mobile" → phone, "Email Address" → email, "Contact Name" → full_name)
- Unrecognised columns → `extra_fields` JSONB verbatim — never drop columns
- Full raw row → `raw_event_json`
- Row with no phone/email/name → create `Lead(pre_flight_block_reason='insufficient_identity_fields')` — never discard
- ≤100 rows: process synchronously, return list of lead IDs
- >100 rows: enqueue ARQ job, return `{"job_id": "...", "row_count": N}`

**`pre_flight.py`:**
- HALT if `get_active_config()` returns `None` or `config.signals == []`
- Set `pre_flight_block_reason` on Lead, write IntakeEventLog, raise `PreFlightHaltError`

**`deduplicator.py`:**
- E.164-normalise phone before lookup
- phone → email → name+location match order
- LeadTouchpoint on duplicate match

**`pipeline.py`:**
- File upload path: preflight → dedup → log → construct LeadReceived
- NOISE path not present yet (no filter in this sprint — added Sprint 3)
- EXISTING_CUSTOMER → `'existing_customer'` — terminal
- LEAD → `'captured'`, return `(lead, LeadReceived(...))`

### Tests to write

- `tests/integration/lead_ingestion/test_dedup_idempotency.py` — same `platform_event_id` twice → exactly 1 `IntakeEventLog` row, Lead created once (ON CONFLICT DO NOTHING)
- `tests/integration/lead_ingestion/test_file_upload_golden_path.py`:
  - `csv_01.csv`: mixed column names map correctly; "Budget Range"/"Notes" in `extra_fields`; identity-less row → Lead with `pre_flight_block_reason`
  - File > 5000 rows → 422
  - `.docx` file → 422
  - pre-flight HALT fires for missing config; fires for empty signals

### Fixtures to create

- `tests/fixtures/lead_ingestion/csv_01.csv`

### Sprint 2 Gate

```bash
pytest tests/integration/lead_ingestion/test_dedup_idempotency.py tests/integration/lead_ingestion/test_file_upload_golden_path.py -v
make typecheck
```

---

## Sprint 3 — WhatsApp DM End-to-End

**Goal:** A real WhatsApp DM webhook arrives → HMAC validated → filtered → pre-flighted → deduped → persisted as `Lead(pipeline_stage=captured)`. Pipeline core already exists from Sprint 2 — this sprint adds the webhook receiver, two-stage filter, and WhatsApp normaliser adapter. A `LeadReceived` object is **constructed** in-process and its fields verified in tests. No downstream enqueue — Epic 5 (enrichment) is not yet built.

### Files to create

```
modules/lead_ingestion/
├── webhook_receiver.py      HMAC-SHA256 on raw bytes; route by payload["object"]; HTTP 200 before async
└── two_stage_filter.py      Stage 1: rule filter (no LLM). Stage 2: Groq classify_message()
```

### Files to extend (Sprint 3)

```
modules/lead_ingestion/
├── normaliser.py            Add WhatsApp adapter: raw payload → NormalisedChannelEvent
└── pipeline.py              Add filter stage for message-based sources:
                             filter→preflight→dedup→log→construct LeadReceived
                             NOISE → Lead(pipeline_stage='insufficient_signal') — terminal, return (lead, None)
                             UNCLEAR → 'awaiting_clarification'

workers/jobs/lead_ingestion.py   Add run_lead_capture(ctx, payload_dict)
                                 Creates own DB session. Calls pipeline.run_capture().

api/lead_ingestion.py            Add webhook endpoints:
                                   GET  /channels/webhook  (Meta hub challenge verify)
                                   POST /channels/webhook  (HMAC validate → enqueue run_lead_capture)
```

### Existing files to modify (Sprint 3)

| File | Change |
|---|---|
| `workers/worker.py` | Register `run_lead_capture` from `workers.jobs.lead_ingestion` |
| `main.py` | Add webhook routes (alongside file upload route from Sprint 2) |

### Key implementation rules

- `webhook_receiver.py`: `await request.body()` **before** `.json()` — HMAC on raw bytes, not parsed JSON. Tampered payload → 403.
- `two_stage_filter.py` Stage 1 discards (zero LLM calls): emoji-only, single greeting ("hi"/"hello"/"hey"/"ok"/"thanks"), story reactions, one-word replies, read receipts.
- `pipeline.py` filter extension: NOISE → `Lead(pipeline_stage='insufficient_signal')` — terminal, return `(lead, None)`. EXISTING_CUSTOMER → `'existing_customer'` — terminal. UNCLEAR → `'awaiting_clarification'`. LEAD → `'captured'`, return `(lead, LeadReceived(...))`.
- File upload rows **bypass the filter** — they enter the pipeline directly at pre-flight (no change to Sprint 2 behaviour).

### Tests to write

**Unit (no DB):**
- `tests/unit/lead_ingestion/test_hmac.py` — valid signature passes; tampered byte → HmacValidationError; state sign/verify round-trip
- `tests/unit/lead_ingestion/test_filter.py` — Stage 1 discards emoji/greeting with call_count == 0 on mocked Groq; Stage 2 returns all 4 `FilterResult` classifications

**Integration (needs DB session):**
- `tests/integration/lead_ingestion/test_whatsapp_golden_path.py` — `wa_dm_01.json` → Lead `pipeline_stage=captured`, IntakeEventLog row, `LeadReceived` object fields correct, zero enrichment calls
- `tests/integration/lead_ingestion/test_noise_exit.py` — `wa_noise_01.json` → `insufficient_signal`, no `LeadReceived` constructed

### Fixtures to create

- `tests/fixtures/lead_ingestion/wa_dm_01.json`
- `tests/fixtures/lead_ingestion/wa_noise_01.json`

### Sprint 3 Gate

```bash
pytest tests/unit/lead_ingestion/test_hmac.py tests/unit/lead_ingestion/test_filter.py -v
pytest tests/integration/lead_ingestion/test_whatsapp_golden_path.py tests/integration/lead_ingestion/test_noise_exit.py -v
grep -r "import anthropic" modules/ | wc -l   # must print 0
make typecheck
```

---

## Sprint 4 — Meta OAuth + Lead Ads + Instagram/Facebook

**Goal:** Tenants connect WhatsApp/Instagram/Facebook via OAuth. Instagram and Facebook DMs route through the same filter pipeline. Lead Ads bypass the filter entirely.

### Files to create

```
modules/lead_ingestion/
├── crypto.py                   encrypt_credentials() / decrypt_credentials() — AES-256-GCM
├── lead_retrieval_worker.py    Fetch lead form data from Meta Graph API; retry on error code=100
├── instagram_token_refresh.py  Daily ARQ cron job; refresh if expires_at - now < 7 days
└── oauth/
    ├── __init__.py
    ├── whatsapp.py    WhatsApp Embedded Signup; HMAC-signed state param
    ├── instagram.py   api.instagram.com/oauth/authorize; 60-day token; store expires_at
    └── facebook.py    3-step chain: short-lived user → long-lived user → page access token
```

### Normaliser extensions

Add adapters to `normaliser.py` for: Instagram DM (IGSID), Facebook DM (PSID), Lead Ad form fields.

### Token storage pattern

```python
# On OAuth callback — store encrypted credentials
conn.credentials_encrypted = encrypt_credentials(
    {"access_token": token, "phone_number_id": pid, ...},
    key=settings.channel_credentials_encryption_key,
)

# On webhook receipt — retrieve and decrypt
creds = decrypt_credentials(conn.credentials_encrypted, key=settings.channel_credentials_encryption_key)
```

### Key rules

- Lead Ads (`event_type='lead_ad'`) **bypass two_stage_filter** entirely — go straight to pre_flight.
- `lead_retrieval_worker.py`: on `leadgen` webhook, fetch `{META_GRAPH_API_BASE}/{leadgen_id}?fields=field_data`. If Meta returns `code=100` (race condition — lead not indexed yet), wait 3s and retry once.
- `instagram_token_refresh.py`: query `channel_connections WHERE channel_type='instagram' AND status='active'`; for each: if `expires_at - now < 7 days` AND not yet expired → call refresh endpoint → update encrypted credentials + expires_at. If already expired → set `status='expired'`, emit admin alert.

### HITL required before this sprint's tests pass

1. Create Meta App at https://developers.facebook.com — add products: WhatsApp, Instagram, Facebook Login
2. Collect: App ID, App Secret, chosen Verify Token string
3. Set redirect URIs in Meta App: `{BASE_URL}/channels/oauth/{whatsapp|instagram|facebook}/callback`
4. Create `{env}/system/meta-app` (or set `META_APP_SECRET` in `.env` for local dev)
5. Confirm App API version is v21.0

### Tests to write

- `tests/unit/lead_ingestion/test_hmac.py` — add: OAuth state sign/verify round-trip; forged state → 403
- `tests/unit/lead_ingestion/test_instagram_refresh.py`:
  - Mock clock at `expires_at - 6d` → refresh API called
  - Mock clock at `expires_at - 10d` → no action
  - Mock clock at `expires_at + 1d` (expired) → `status='expired'`, refresh NOT called
- `tests/integration/lead_ingestion/test_lead_ad_golden_path.py` — `lead_ad_webhook_01.json` + `lead_form_data_01.json`; asserts zero LLM calls, `source_channel='facebook_lead_ad'`

### Fixtures to create

- `tests/fixtures/lead_ingestion/lead_ad_webhook_01.json`
- `tests/fixtures/lead_ingestion/lead_form_data_01.json`

### Sprint 4 Gate

```bash
pytest tests/unit/lead_ingestion/test_hmac.py tests/unit/lead_ingestion/test_instagram_refresh.py -v
pytest tests/integration/lead_ingestion/test_lead_ad_golden_path.py -v
make typecheck
```

---

## Sprint 5 — Email + Sheets

**Goal:** Email inbound and Google Sheets polling flow through the same preflight→dedup→capture pipeline built in Sprint 2. Neither calls the two-stage filter.

### Files to create

```
modules/lead_ingestion/
├── email_inbound.py       Sender allowlist + subject keyword filter; structural template parse
└── sheets_poller.py       Service account poll; watermark-based delta; 15-min ARQ cron
```

### Normaliser extensions

Add adapters to `normaliser.py` for: email, sheets row.

### Key rules

**`sheets_poller.py`:**
- Read watermark from `channel_connection.metadata->>'last_watermark'`
- Fetch rows newer than watermark using Google Sheets API (service account JSON from `credentials_encrypted`)
- Update watermark after successful batch — not before

**`email_inbound.py`:**
- Check sender against `tenant_config`-stored allowlist
- Check subject against keyword list
- Parse body using structural template from `LeadFormFieldMap`
- No LLM call

### HITL required before sheets_poller tests pass

1. Go to Google Cloud Console → enable Sheets API + Drive API
2. Create service account, download JSON key
3. Share target Sheet with service account email (Viewer)
4. Store JSON key in `ChannelConnection.credentials_encrypted` for the test tenant

### Tests to write

- `tests/integration/lead_ingestion/test_email_golden_path.py` — valid sender + matching subject → Lead `pipeline_stage=captured`; blocked sender → no Lead created
- `tests/integration/lead_ingestion/test_sheets_poller.py` — watermark advances after batch; rows already seen are not re-ingested

### Sprint 5 Gate

```bash
pytest tests/integration/lead_ingestion/test_email_golden_path.py tests/integration/lead_ingestion/test_sheets_poller.py -v
make typecheck
```

---

## Sprint 6 — Router + Final Wiring

**Goal:** All endpoints registered, all ARQ jobs registered, `make ci` fully green.

### Files to complete

```
api/lead_ingestion.py        Complete all routes (extends partials from Sprints 2 & 3):
                               GET  /channels/webhook                     (Meta hub challenge)
                               POST /channels/webhook                     (Meta webhook receiver)
                               GET  /channels/oauth/{channel}/start       (initiate OAuth)
                               GET  /channels/oauth/{channel}/callback    (OAuth callback)
                               GET  /channels/{connection_id}/status      (connection health)
                               POST /channels/inbound/file-upload         (CSV/XLSX upload)

workers/jobs/lead_ingestion.py   Complete all job wrappers (extends partials from Sprints 2 & 3):
                                   run_lead_capture, run_lead_capture_batch,
                                   refresh_instagram_tokens, poll_sheets
```

### Existing files to modify

| File | Change |
|---|---|
| `workers/worker.py` | Register remaining jobs: `refresh_instagram_tokens`, `poll_sheets`; add retry policy (1 retry → dead-letter) |
| `main.py` | `app.include_router(lead_ingestion_router, prefix="/channels")` (full router, replaces partials) |
| `tests/integration/conftest.py` | Import `shared.channels.models` and `modules.lead_ingestion.db.models` so truncation sweep covers new tables |

### Sprint 6 Gate

```bash
make ci   # lint + typecheck + full test suite
grep -r "import anthropic" modules/ | wc -l   # must be 0
pytest tests/unit/lead_ingestion/ tests/integration/lead_ingestion/ -v
```

---

## Verification Rule (every sprint)

A sprint is **done** when all four pass:
1. `make test-unit` — unit tests green
2. `make test-integration` — integration tests for that sprint green
3. `make typecheck` — mypy strict, zero errors
4. `make lint` — ruff clean

Do not start the next sprint until all four are green for the current one.
