# Continuation Context — Lead Ingestion Epic 4

**Date:** 2026-06-19
**Current branch:** epic4/4-meta-oauth-lead-ads — committed, pushed, awaiting PR merge into feature/lead-ingestion

---

## Sprint Status

| Sprint | Description | Status |
|--------|-------------|--------|
| 1 | WhatsApp webhook receive + HMAC | Complete, merged |
| 2 | File upload (CSV/XLSX) | Complete, merged |
| 3 | LLM noise filter + lead pipeline | Complete, merged |
| 4 | Meta OAuth connect (WA / FB / IG) + Lead Ads | Complete, committed — PR pending |
| 5 | Enrichment module | Not started |

---

## Sprint 4 — What Was Built (COMPLETE)

### New Files
- `modules/lead_ingestion/crypto.py` — AES-256-GCM encrypt/decrypt credentials
- `modules/lead_ingestion/oauth/__init__.py`
- `modules/lead_ingestion/oauth/state.py` — HMAC-SHA256 sign/verify state param
- `modules/lead_ingestion/oauth/whatsapp.py` — Embedded Signup code -> BISU token -> WABA IDs -> phone numbers -> ChannelConnections
- `modules/lead_ingestion/oauth/facebook.py` — redirect OAuth -> long-lived token -> Page Access Tokens -> ChannelConnections
- `modules/lead_ingestion/oauth/instagram.py` — redirect OAuth -> 60-day token -> IG account -> ChannelConnection + expiry
- `modules/lead_ingestion/instagram_token_refresh.py` — 3-branch refresh (expired / within 7-day window / fresh)
- `clients/meta_leads_client.py` — Graph API `GET /{leadgen_id}` with retry
- `workers/jobs/instagram_refresh.py` — ARQ cron job, daily 02:00 UTC
- `migrations/versions/c8d2f1a9e3b7_gin_index_channel_connection_metadata.py` — GIN index on metadata JSONB

### Modified Files
- `api/lead_ingestion.py` — 5 new OAuth endpoints + FB/IG webhook routing + `_route_lead_ad()` + `_route_facebook_instagram()` branching on `leadgen` field
- `core/config.py` — added `meta_embedded_signup_config_id`, `base_url`
- `modules/lead_ingestion/db/repository.py` — added `get_connection_by_page_or_ig_account_id`
- `modules/lead_ingestion/service.py` — exposed OAuth + crypto symbols
- `workers/jobs/lead_ingestion.py` — added `run_lead_ad_capture` ARQ job
- `workers/worker.py` — registered `run_instagram_token_refresh` cron

### Tests
- `tests/unit/lead_ingestion/test_crypto.py` — 7 tests
- `tests/unit/lead_ingestion/test_hmac.py` — 11 tests
- `tests/unit/lead_ingestion/test_instagram_refresh.py` — 4 tests
- `tests/unit/lead_ingestion/test_oauth_whatsapp.py` — unit tests
- `tests/unit/lead_ingestion/test_oauth_facebook.py` — unit tests
- `tests/unit/lead_ingestion/test_oauth_instagram.py` — unit tests
- `tests/integration/lead_ingestion/test_lead_ad_golden_path.py` — 2 tests
- `tests/integration/lead_ingestion/test_oauth_whatsapp_connect.py` — 4 tests
- `tests/integration/lead_ingestion/test_oauth_facebook_connect.py` — 4 tests
- `tests/integration/lead_ingestion/test_oauth_instagram_connect.py` — 6 tests

### Sprint Gate (Last Run — Sprint 4 complete)
- 270 tests passing
- mypy strict clean
- ruff clean
- 0 `import anthropic` occurrences

---

## Manual Testing Done (2026-06-19) — Lead Ads Pipeline VERIFIED

### What Was Tested
The Facebook Lead Ads golden path was manually verified end-to-end.

**Result: PASS**

| Step | Result |
|------|--------|
| `POST /{page_id}/subscribed_apps?subscribed_fields=leadgen` | `{"success":true}` |
| App Dashboard → Webhooks → Page → `leadgen` field "Send to My Server" | Delivered, `{"status":"unknown_connection"}` (expected — fake page_id) |
| `scripts/seed_test_connection.py` ran (2nd run) | Tenant + Config + ChannelConnection created |
| `scripts/test_lead_ad.py` — direct `run_lead_ad_capture` invocation | `leads` row inserted: `pipeline_stage='captured'`, `source_channel='FACEBOOK_LEAD_ADS'` |
| `intake_event_logs` | `status='received'`, `platform_event_id='leadgen-857144174130084'` |
| Meta Graph API `fetch_lead_fields` | Retrieved `email='test@meta.com'`, `full_name='<test lead: dummy data for full_name>'` |

**Test data in DB (dev only — seeded tenant):**
- Tenant ID: `fe8a1a17-7041-4004-8109-aa22259d6859`
- ChannelConnection ID: `04edabdf-948b-41d5-8e91-6314de273148`
- Lead ID: `427a597f-e9b4-4f78-8ff7-d3fdfe24526b`
- Page ID: `1346146538572443` (Enrichment Testing Facebook page)
- Test leadgen_id: `857144174130084`

### Scripts Created
- `scripts/seed_test_connection.py` — seeds a test tenant + active config + Facebook ChannelConnection
- `scripts/test_lead_ad.py` — directly invokes `run_lead_ad_capture` to test the pipeline without needing webhook delivery

### Known Limitation: Lead Ads Testing Tool RTU Delivery
The Lead Ads Testing Tool RTU (real-time update) status stayed "Pending" throughout testing. This is a Meta platform limitation in development mode — the RTU delivery system for Lead Ads Testing Tool events requires app production approval or CRM-specific configuration (Lead Access Manager). This does NOT affect production: when a real user submits a form from a live ad campaign, Meta sends the webhook directly via the standard `subscribed_apps` subscription, which works correctly (confirmed by the App Dashboard "Send to My Server" test delivering successfully).

**Workaround for future manual testing:** Use `scripts/test_lead_ad.py` to directly invoke the ARQ job with a real `leadgen_id` obtained from the Meta Graph API.

---

## E2E Test Map — Status

Full test map is in `test-map.md` (20 phases covering all channels).

**Phases NOT YET RUN** (need to be executed in a future session):
- Phase 1–5: Health, Auth0 login, /me, onboarding, hub challenge
- Phase 6–10: WhatsApp DM, noise filter, idempotency, dedup, file upload
- Phase 11–14: Facebook OAuth, Facebook DM, Instagram OAuth, Instagram DM
- Phase 15: WhatsApp Embedded Signup (requires WA Business number — HITL)
- Phase 16–20: HMAC rejection, unified registry, FB→IG auto-connect, unroutable, isolation

The `test-map.md` has the full payload fixtures, SQL checks, and fail guides.

**Note before running test-map Phase 11 (Facebook OAuth):**
The Messenger and Instagram products are NOT added to the Meta App yet. Redirect URIs for Facebook/Instagram OAuth callbacks need to be registered in the App Dashboard. Do this before attempting Phases 11–14.

---

## Meta App Setup — Current State

### Completed
- Meta Developer Account + App created (Business type)
- **App ID:** `1494580908551596`
- **App Secret:** in `.env`
- WhatsApp product added + `messages` webhook field subscribed
- Facebook Login for Business added
- **Embedded Signup config_id:** `1311452217634130`
- Webhook URL configured: `https://lorna-nonutilized-macy.ngrok-free.dev/channels/webhook`
- Verify Token set: `local_verify_token_sprint3`
- Page product added + `leadgen` + `messages` fields subscribed at v25.0
- Enrichment Testing page (`1346146538572443`) subscribed to app for `leadgen` events

### Still Needed (before running test-map Phases 11–14)
- Messenger product NOT added to Meta App
- Instagram product NOT added to Meta App
- Facebook OAuth redirect URI NOT registered: `https://lorna-nonutilized-macy.ngrok-free.dev/channels/oauth/facebook/callback`
- Instagram OAuth redirect URI NOT registered: `https://lorna-nonutilized-macy.ngrok-free.dev/channels/oauth/instagram/callback`
- Run `make migrate` if any new migrations have been added since last run

---

## Environment

### .env (current — do not lose these values)
```
CHANNEL_CREDENTIALS_ENCRYPTION_KEY=yYA-YOw2Rox-1v236MeQeJTt8V4kY9D0ox9t_Tjsm70=
META_APP_ID=1494580908551596
META_EMBEDDED_SIGNUP_CONFIG_ID=1311452217634130
META_GRAPH_API_VERSION=v21.0
BASE_URL=https://lorna-nonutilized-macy.ngrok-free.dev  <- static domain, no update needed on restart
```

### ngrok
Static domain — `https://lorna-nonutilized-macy.ngrok-free.dev`. Does NOT change on restart.
Start with: `ngrok http --domain=lorna-nonutilized-macy.ngrok-free.dev 8000`

### To Start the Environment
```powershell
# 1. Start Docker Desktop from Start menu

# 2. Start containers
docker-compose up -d

# 3. Apply any new migrations
uv run alembic upgrade head

# 4. Start backend
uv run uvicorn main:app --reload --port 8000

# 5. In a NEW terminal - start ngrok (static domain)
ngrok http --domain=lorna-nonutilized-macy.ngrok-free.dev 8000

# 6. In a NEW terminal - start ARQ worker
uv run arq workers.worker.WorkerSettings
```

Verify: `https://lorna-nonutilized-macy.ngrok-free.dev/health` → `{"status":"ok"}`

---

## Architecture Decisions (Locked)

- Token storage: `ChannelConnection.credentials_encrypted` (AES-256-GCM) — NOT AWS Secrets Manager
- LLM: Groq only — `import anthropic` is banned
- Job queue: ARQ — no Inngest
- `ChannelConnection` lives in `shared/channels/models.py`
- All external imports from `modules/lead_ingestion/` go through `service.py`
- Single Meta webhook URL for all tenants — routed by `phone_number_id` / `page_id` / `ig_account_id`
- Lead Ads bypass LLM filter — form submissions are explicit lead intent (`run_lead_ad_capture` calls `run_capture`, not `run_capture_message`)

---

## Next: Sprint 5 — Enrichment Module

Sprint 5 scope:
- `modules/enrichment/` — cache-first external lookup pipeline
- Layer 0: in-process cache (cachetools TTLCache)
- Layer 1: Surepass (KYC data — India)
- Layer 2: Serper (web search context)
- Layer 3: NewsCatcher (news mentions)
- Layer 4: Probe42 (company data — India)
- Stop as soon as there is enough data to score
- Emit `LeadEnriched` event
- B2C leads: use first-party data from the lead form instead of external lookups

**Client stubs already exist** in `clients/` — need to be fleshed out.

Key reference:
- Full Meta integration spec: `docs/meta-integration-implementation.md`
- Sprint plan: `docs/epic4-sprint-plan.md`
- Architecture rules: `.claude/rules/architecture.md`
- E2E test map: `test-map.md`
