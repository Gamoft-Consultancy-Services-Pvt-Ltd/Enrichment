# Epic 4 — Lead Ingestion Module

## Commands that must be running

### 1. Infrastructure (Docker Compose)

```bash
make up
```

This starts Postgres (`leadengine` database on port 5432) and Redis (port 6379). The FastAPI app and ARQ worker also start inside Docker. Access the app at http://localhost:8000.

To stop:

```bash
make down
```

To tail logs from all services:

```bash
make logs
```

### 2. Apply migrations

Run this once after `make up`, and again whenever new migration files are added:

```bash
make migrate
```

Applies all Alembic migrations in order. Epic 4 added four migrations:
- `4f55595dc4ff` — creates `channel_connections`, `leads`, `intake_event_logs`, `lead_form_field_maps`, `lead_touchpoints`
- `c8d2f1a9e3b7` — GIN index on `channel_connections.metadata` for fast JSON key lookups
- `0149ad5bb26e` — adds `platform_event_id` to `lead_touchpoints`
- `2b4e569e8653` — makes `intake_event_logs.tenant_id` nullable (for unroutable webhook events)

### 3. ARQ background worker

The ARQ worker processes lead capture jobs enqueued by the API. It runs automatically inside Docker via `make up`, but to run it standalone:

```bash
uv run arq workers.worker.WorkerSettings
```

The worker registers four functions and one cron job:
- `run_lead_capture` — processes one WhatsApp / Facebook / Instagram DM webhook event
- `run_lead_ad_capture` — fetches Lead Ads form data from Meta Graph API and captures
- `run_lead_capture_batch` — processes a batch of file-upload rows (> 100 rows async path)
- `run_onboarding_pipeline` — tenant onboarding (not lead ingestion specific)
- `run_instagram_token_refresh` — daily cron at 02:00 UTC, refreshes expiring Instagram tokens

### 4. ngrok (local Meta webhook testing only)

Meta's webhook delivery requires a public HTTPS URL. For local development:

```bash
ngrok http 8000
```

Then set `BASE_URL` in `.env` to the ngrok URL and register it in the Meta Developer Portal:

```
Webhook callback URL: {BASE_URL}/channels/webhook
```

Subscribed webhook fields to enable in Meta Developer Portal:
- **Page object**: `messages`, `leadgen`
- **Instagram object**: `messages`
- **WhatsApp Business Account**: `messages`

### 5. CI gate (before pushing)

```bash
make ci
```

Runs lint (`ruff`), typecheck (`mypy` strict, 156 files), and the full test suite (302 tests). All three must pass.

Run individual tiers:

```bash
make test-unit         # 246 unit tests — no DB, no network
make test-integration  # 56 integration tests — requires real Postgres + Redis
make lint
make typecheck
```

Run a single test:

```bash
uv run pytest tests/unit/lead_ingestion/test_oauth_facebook.py -v
```

---

## What this module does

`modules/lead_ingestion` accepts leads from four inbound sources, attributes each to a tenant, filters noise from genuine leads, normalises all events to a canonical schema, deduplicates against existing contacts, and persists a `Lead` row. It emits a `LeadReceived` event on the happy path. It does not enrich or score.

---

## Inbound channels

| Channel | Transport | Source adapter | Filter |
|---|---|---|---|
| File upload (CSV / XLSX) | HTTP `POST /channels/inbound/file-upload` | `normalise_file_row` | None — skips LLM filter |
| WhatsApp DM | Meta webhook `POST /channels/webhook` | `normalise_whatsapp_message` | Two-stage filter |
| Facebook Messenger DM | Meta webhook `POST /channels/webhook` | `normalise_facebook_dm` | Two-stage filter |
| Instagram DM | Meta webhook `POST /channels/webhook` | `normalise_instagram_dm` | Two-stage filter |
| Facebook Lead Ads | Meta webhook `POST /channels/webhook` → `run_lead_ad_capture` ARQ job | `normalise_lead_ad_form` | None — explicit intent |

---

## Webhook routing (`api/lead_ingestion.py`)

All Meta platforms send to the same endpoint: `POST /channels/webhook`.

The API layer:
1. Validates the `X-Hub-Signature-256` HMAC header before parsing JSON (`validate_signature`).
2. Routes by the `object` field in the payload:
   - `whatsapp_business_account` → route by `phone_number_id` → enqueue `run_lead_capture`
   - `page` → check `field` for `leadgen`; Lead Ads → enqueue `run_lead_ad_capture`; DMs → enqueue `run_lead_capture`
   - `instagram` → route by `ig_account_id` → enqueue `run_lead_capture`
3. For unroutable events (no matching `ChannelConnection` found), writes an `IntakeEventLog` row with `status='unroutable'` and `tenant_id=NULL`, then returns `{"status": "unknown_connection"}`.
4. Always returns HTTP 200 to Meta immediately — actual processing happens in the ARQ worker.

Webhook verification (Meta handshake):

```
GET /channels/webhook?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...
```

Returns the challenge as plain text if the verify token matches `META_WEBHOOK_VERIFY_TOKEN`.

---

## Source adapters (`normaliser.py`)

Every adapter converts a raw payload into an immutable `NormalisedChannelEvent`:

```python
class NormalisedChannelEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_id: UUID          # internal, auto-generated
    tenant_id: UUID
    channel_connection_id: UUID | None
    source: LeadSource      # enum: WHATSAPP | FACEBOOK | INSTAGRAM | FACEBOOK_LEAD_ADS | FILE_UPLOAD
    platform_event_id: str  # unique per source (message ID / file row hash / leadgen-{id})
    full_name: str | None
    phone: str | None
    email: str | None
    location: str | None
    raw_text: str | None    # message text, only for DM channels
    raw_event_json: dict    # full original payload, always stored
    extra_fields: dict      # non-standard fields from file rows or Lead Ads forms
    received_at: datetime
```

Key behaviours per adapter:

**`normalise_whatsapp_message`** — extracts `wamid.XXXX` as `platform_event_id`, prepends `+` to the MSISDN from `message["from"]` to make it E.164.

**`normalise_file_row`** — maps headers case-insensitively against `STANDARD_FIELD_MAP`; any unrecognised non-empty header lands in `extra_fields` (never discarded). `platform_event_id` is `"file-" + sha256(tenant_id + row_content)[:40]` — re-uploading the same file is idempotent.

**`normalise_instagram_dm`** — uses `messaging["message"]["mid"]` as `platform_event_id`. Phone is always `None` — Instagram DMs do not expose phone numbers.

**`normalise_facebook_dm`** — uses `messaging["message"]["mid"]` as `platform_event_id`. Phone is always `None`.

**`normalise_lead_ad_form`** — flattens `field_data` list into a dict, assembles `full_name` from `first_name` + `last_name` if `full_name` not present, lowercases email, assembles location from city/state/zip/country fields. `platform_event_id` is `"leadgen-{leadgen_id}"`.

---

## Two-stage filter (`two_stage_filter.py`)

Applied to all message-based channels (WhatsApp, Facebook DM, Instagram DM). Skipped for file uploads and Lead Ads.

**Stage 1 — rule-based (zero LLM calls):**
Discards obvious non-leads without calling Groq:
- Empty text
- Emoji-only messages
- Single noise words: `hi`, `hello`, `hey`, `ok`, `okay`, `thanks`, `k`, `yes`, `no`, `bye`, `good`, `fine`, `sure`

**Stage 2 — Groq `classify_message`:**
Calls `clients/groq_client.py:classify_message` with the message text. Returns a `FilterResult`:

```python
class FilterClassification(StrEnum):
    LEAD = "LEAD"
    NOISE = "NOISE"
    UNCLEAR = "UNCLEAR"
    EXISTING_CUSTOMER = "EXISTING_CUSTOMER"

class FilterResult(BaseModel):
    classification: FilterClassification
    extracted_fields: dict   # LLM-extracted name/phone/email/location
    confidence: float | None
```

**Calibration rule** (hardcoded): if Groq returns `confidence < 0.7` for any non-LEAD, non-UNCLEAR classification, it is coerced to `LEAD`. A missed lead costs more than processing a non-lead. `UNCLEAR` is always routed as `LEAD` in `pipeline.py`.

---

## Pipeline (`pipeline.py`)

### `run_capture_message` — message-based events

```
→ run_filter(raw_text)
    NOISE              → create_terminal_lead(pipeline_stage='insufficient_signal')
    EXISTING_CUSTOMER  → create_terminal_lead(pipeline_stage='existing_customer')
    LEAD / UNCLEAR     → merge extracted_fields into event
                       → check_pre_flight(tenant_id)
                           fail → create_terminal_lead(pipeline_stage='pre_flight_blocked')
                       → run_capture(enriched_event)
```

### `run_capture` — file upload rows and post-filter events

```
→ reserve_event_slot(platform_event_id)  ← ON CONFLICT DO NOTHING (idempotency guard)
    None (redelivery) → return (existing_lead, None)
→ find_duplicate(tenant_id, event)
    found  → add LeadTouchpoint → finalise_log(status='duplicate') → return (existing, None)
    not found → create Lead(pipeline_stage='captured')
              → add LeadTouchpoint
              → finalise_log(status='received')
              → return (lead, LeadReceived(...))
```

### `create_terminal_lead` — noise / blocked events

Creates a `Lead` row with the terminal stage and idempotency guard. Redeliveries of noise events are a no-op identical to the LEAD path.

### Pre-flight check (`pre_flight.py`)

Calls `shared.tenant_config.service.get_active_config`. Raises `PreFlightHaltError` if:
- No `ACTIVE` config exists for the tenant (`"no_active_config"`)
- The config has an empty signals list (`"empty_signals"`)

---

## Deduplication (`deduplicator.py`)

Match order for an existing `Lead` within the same tenant:

1. Phone — E.164 normalised (strips all formatting, keeps `+`)
2. Email — lowercased
3. Full name + location — both must be present and match

On a dedup hit, a `LeadTouchpoint` is added linking the new event to the existing lead. No new `Lead` row is created and no `LeadReceived` is emitted.

---

## Idempotency — `IntakeEventLog`

Every event (lead, noise, duplicate, blocked) claims a slot in `intake_event_logs` via `reserve_event_slot` before any other work. Uses `INSERT ... ON CONFLICT (platform_event_id) DO NOTHING RETURNING id`. If `id` is `None`, the event was already processed — return immediately. This makes webhook redeliveries a true no-op.

`intake_event_logs` status values:
- `pending` — slot reserved, not yet finalised
- `received` — new lead created
- `duplicate` — dedup hit, touchpoint added
- `insufficient_signal` — noise filter
- `existing_customer` — filter classification
- `pre_flight_blocked` — pre-flight failed
- `unroutable` — no `ChannelConnection` found for this webhook

---

## Database schema

### `channel_connections` (in `shared/channels/models.py`)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → tenants | indexed |
| `channel_type` | String | `whatsapp` / `facebook` / `instagram` |
| `status` | String | `active` / `expired` |
| `credentials_encrypted` | LargeBinary | AES-256-GCM blob |
| `expires_at` | DateTime(tz) | null for non-expiring tokens |
| `connection_metadata` | JSONB | `page_id`, `phone_number_id`, `ig_account_id`, etc. — GIN indexed |

### `leads`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → tenants | indexed |
| `channel_connection_id` | UUID FK → channel_connections | nullable, indexed |
| `pipeline_stage` | String | `captured` / `insufficient_signal` / `existing_customer` / `pre_flight_blocked` |
| `source_channel` | String | `WHATSAPP` / `FACEBOOK` / `INSTAGRAM` / `FACEBOOK_LEAD_ADS` / `FILE_UPLOAD` |
| `full_name` | String | nullable |
| `phone` | String | E.164 normalised, nullable |
| `email` | String | lowercased, nullable |
| `location` | String | nullable |
| `raw_event_json` | JSONB | always stored, never null |
| `extra_fields` | JSONB | non-standard fields from file rows / Lead Ads, nullable |
| `pre_flight_block_reason` | String | set when `pipeline_stage='pre_flight_blocked'` |

### `intake_event_logs`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → tenants | nullable (null for unroutable events) |
| `lead_id` | UUID FK → leads | nullable until finalised |
| `platform_event_id` | String | unique constraint — idempotency key |
| `source_channel` | String | |
| `status` | String | see values above |
| `raw_event_json` | JSONB | |

### `lead_touchpoints`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `lead_id` | UUID FK → leads | indexed |
| `platform_event_id` | String | nullable, indexed |
| `source_channel` | String | |
| `raw_event_json` | JSONB | |

### `lead_form_field_maps`

Per-tenant overrides mapping non-standard CSV/XLSX headers to canonical fields. Unique on `(tenant_id, source_field_name)`.

---

## OAuth connections

### Facebook (`oauth/facebook.py`)

**Initiate:** `GET /channels/oauth/facebook` — redirects to `https://www.facebook.com/dialog/oauth` with HMAC-signed state and scopes: `pages_show_list`, `pages_manage_metadata`, `pages_messaging`, `pages_read_engagement`, `pages_manage_ads`, `leads_retrieval`, `instagram_manage_messages`, `instagram_basic`.

**Callback:** `GET /channels/oauth/facebook/callback?code=...&state=...`

Token chain: code → short-lived user token → long-lived user token (60 days) → Page Access Token (non-expiring, from `/me/accounts`).

For each Facebook Page granted:
1. Subscribes the page to `messages,leadgen` webhook events.
2. Creates one `ChannelConnection` with `channel_type='facebook'`.
3. Checks if the page has a connected Instagram Business account. If yes, subscribes it to `messages` webhooks and creates a second `ChannelConnection` with `channel_type='instagram'` and `expires_at=None` (non-expiring because it uses the page token).

State parameter: `{"tenant_id": "...", "channel": "facebook"}`, signed with `META_APP_SECRET` using HMAC-SHA256.

### Instagram (`oauth/instagram.py`)

Separate app: "Enrichment-IG" (`META_IG_APP_ID` / `META_IG_APP_SECRET`).

**Initiate:** `GET /channels/oauth/instagram` — redirects to `https://api.instagram.com/oauth/authorize` with scopes: `instagram_business_basic`, `instagram_business_manage_messages`.

**Callback:** `GET /channels/oauth/instagram/callback?code=...&state=...`

Token chain: code → short-lived token (POST form data to `api.instagram.com`) → 60-day long-lived token (GET `graph.instagram.com/access_token`).

Fetches `ig_account_id` and `username` from `/me`. Subscribes the account to `messages` webhooks individually via `POST /{ig_account_id}/subscribed_apps` (required for Instagram Business Login API — the App Dashboard Webhooks section alone is not sufficient).

Creates one `ChannelConnection` with `channel_type='instagram'` and `expires_at` set ~60 days out.

State parameter: `{"tenant_id": "...", "channel": "instagram"}`, signed with `META_IG_APP_SECRET`.

### WhatsApp Embedded Signup (`oauth/whatsapp.py`)

Not a redirect OAuth. The frontend calls `FB.login()` with `META_EMBEDDED_SIGNUP_CONFIG_ID`, receives a code, and POSTs it to:

**Callback:** `POST /channels/embedded-signup/callback` with body `{"code": "..."}` (requires Auth0 JWT).

Flow:
1. Exchange code → BISU (Business Integration System User) non-expiring token.
2. Call `/debug_token` → extract WABA IDs from `granular_scopes`.
3. For each WABA: subscribe to webhooks, fetch phone numbers.
4. Create one `ChannelConnection` per phone number with `channel_type='whatsapp'`.

Dev test page served at: `GET /dev-tools/whatsapp-test` (dev use only, not in schema).

---

## OAuth state parameter security (`oauth/state.py`)

All OAuth redirects use a signed state to prevent CSRF:

```
state = base64url(json(payload)) + "." + hex(hmac_sha256(b64_payload, secret))
```

Verification uses `hmac.compare_digest` (constant-time). Tampered or replayed states raise `OAuthStateError` → HTTP 403.

---

## Credential encryption (`crypto.py`)

All `ChannelConnection.credentials_encrypted` values are AES-256-GCM blobs:

```
wire format: nonce (12 bytes) || ciphertext+tag (variable)
```

Key: `CHANNEL_CREDENTIALS_ENCRYPTION_KEY` — URL-safe base64-encoded 32-byte value.

Generate a fresh key:
```bash
python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

Warning: rotating this key after tenants have connected makes all stored credentials unreadable. Back up the old key before rotating.

---

## Instagram token refresh (`instagram_token_refresh.py`)

Daily ARQ cron at 02:00 UTC (`run_instagram_token_refresh`).

Logic per active Instagram connection:
- `now >= expires_at` → set `status='expired'`, skip API call.
- `expires_at - now < 7 days` → call `GET https://graph.instagram.com/refresh_access_token`, update `credentials_encrypted` with the new token and update `expires_at`.
- `expires_at - now >= 7 days` → no action.

Connections created via the Facebook OAuth flow have `expires_at=None` (non-expiring page token) and are skipped.

---

## File upload handler (`file_upload_handler.py`)

**Endpoint:** `POST /channels/inbound/file-upload` (requires Auth0 JWT, `tenant` role).

Accepted formats: `.csv`, `.xlsx`. Limits: 10 MB, 5,000 rows.

Sync path (≤ 100 rows): processes all rows immediately, returns `{"mode": "sync", "lead_ids": [...], "row_count": N}`.

Async path (> 100 rows): enqueues `run_lead_capture_batch` ARQ job, returns `{"mode": "async", "row_count": N}` immediately.

Pre-flight is checked once before processing. Rows with no name, phone, or email are written as `pre_flight_blocked` leads with reason `insufficient_identity_fields` — never silently discarded.

CSV: UTF-8 with optional BOM. XLSX: reads active sheet, first row as headers.

---

## API route summary

All routes are mounted under `/channels` prefix in `main.py`.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/channels/webhook` | None | Meta hub challenge verification |
| `POST` | `/channels/webhook` | None (HMAC) | Receive Meta webhook events |
| `POST` | `/channels/inbound/file-upload` | JWT | Upload CSV/XLSX lead file |
| `GET` | `/channels/connections/{id}/status` | JWT | Get ChannelConnection status |
| `GET` | `/channels/oauth/facebook` | JWT | Initiate Facebook OAuth |
| `GET` | `/channels/oauth/facebook/callback` | None | Facebook OAuth callback |
| `GET` | `/channels/oauth/instagram` | JWT | Initiate Instagram OAuth |
| `GET` | `/channels/oauth/instagram/callback` | None | Instagram OAuth callback |
| `POST` | `/channels/embedded-signup/callback` | JWT | WhatsApp Embedded Signup |

---

## Module public surface (`service.py`)

External code (`api/`, `workers/`) imports only from `modules/lead_ingestion/service.py`. Never import module internals from outside.

Exported names:
- Exceptions: `HmacValidationError`, `PreFlightHaltError`, `DuplicateEventError`, `FilterClientError`, `ChannelApiError`, `OAuthStateError`
- Model: `Lead`
- Crypto: `decrypt_credentials`
- File upload: `handle_file_upload`
- Normalisers: `normalise_facebook_dm`, `normalise_file_row`, `normalise_instagram_dm`, `normalise_lead_ad_form`, `normalise_whatsapp_message`
- Pipeline: `run_capture`, `run_capture_message`, `check_pre_flight`
- Webhook: `validate_signature`
- Repository lookups: `get_whatsapp_connection_by_phone_number_id`, `get_connection_by_page_or_ig_account_id`, `get_channel_connection`, `log_unroutable_event`
- OAuth: `build_facebook_auth_url`, `exchange_facebook_code`, `build_instagram_auth_url`, `exchange_instagram_code`, `exchange_whatsapp_signup_code`

---

## Environment variables required for lead ingestion

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL async connection string |
| `REDIS_URL` | Redis connection string for ARQ jobs |
| `OPENROUTER_API_KEY` | Stage 2 LLM filter (`deepseek/deepseek-v4-flash` via OpenRouter) |
| `META_APP_ID` | `1494580908551596` — Facebook / WhatsApp app |
| `META_APP_SECRET` | Facebook app secret (from Meta Developer Portal) |
| `META_WEBHOOK_VERIFY_TOKEN` | Shared verify token registered in Meta Webhooks panel |
| `META_GRAPH_API_VERSION` | `v21.0` |
| `META_EMBEDDED_SIGNUP_CONFIG_ID` | `1311452217634130` — WhatsApp Embedded Signup config |
| `META_IG_APP_ID` | `1510242517263430` — Instagram Business Login app |
| `META_IG_APP_SECRET` | Instagram app secret (from Meta Developer Portal, "Enrichment-IG" app) |
| `BASE_URL` | Public HTTPS URL of this server (ngrok locally, real domain in prod) |
| `CHANNEL_CREDENTIALS_ENCRYPTION_KEY` | AES-256 key, URL-safe base64 32 bytes |

Auth0 variables (`AUTH0_DOMAIN`, `AUTH0_AUDIENCE`, `AUTH0_SPA_CLIENT_ID`) are required for authenticated endpoints but are not lead-ingestion specific.

---

## Security invariants

- **HMAC validation happens before JSON parsing.** The webhook endpoint reads the raw body, validates `X-Hub-Signature-256`, and only then calls `json.loads`. Never reversed.
- **HMAC comparison is constant-time.** Both `validate_signature` and `verify_state` use `hmac.compare_digest`. Never replace with `==`.
- **Credentials are never stored in plaintext.** All OAuth tokens are AES-256-GCM encrypted before writing to `channel_connections.credentials_encrypted`.
- **Calibration: uncertainty escalates to LEAD.** `confidence < 0.7` and `UNCLEAR` both route as `LEAD`. A missed lead costs more than a false positive.
- **Idempotency is enforced at the DB level.** `ON CONFLICT (platform_event_id) DO NOTHING` in `intake_event_logs` — not in application logic.

---

## Sprint delivery summary

| Sprint | Branch | What shipped |
|---|---|---|
| 1 — Foundation | `epic4/1-foundation` | `ChannelConnection` model, `shared/channels`, base schemas, initial migration |
| 2 — Core pipeline + file upload | `epic4/2-core-pipeline-file-upload` | `pipeline.py`, `normaliser.py` (file row), `deduplicator.py`, `pre_flight.py`, `intake_logger.py`, `file_upload_handler.py`, `POST /channels/inbound/file-upload` |
| 3 — WhatsApp DM end-to-end | `epic4/3-whatsapp-webhook-llm-filter` | `webhook_receiver.py`, `two_stage_filter.py`, WhatsApp adapter, `run_capture_message`, `GET/POST /channels/webhook` |
| 4 — Meta OAuth | `epic4/4-meta-oauth-lead-ads` | `crypto.py`, `oauth/state.py`, `oauth/facebook.py`, `oauth/instagram.py`, `oauth/whatsapp.py`, `instagram_token_refresh.py`, Facebook DM + Instagram DM adapters, Lead Ads adapter, all OAuth endpoints |
| 5 — Router + final wiring | `epic4/5-router-final-wiring` | All routes mounted, all ARQ jobs registered in `worker.py`, `make ci` fully green (302/302 tests) |
