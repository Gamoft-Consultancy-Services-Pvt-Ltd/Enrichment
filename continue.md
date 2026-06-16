# Continuation Context — Lead Ingestion Epic 4

**Date:** 2026-06-13
**Current branch:** epic4/4-meta-oauth-lead-ads — committed, pushed, awaiting PR merge into eature/lead-ingestion

---

## Sprint Status

| Sprint | Description | Status |
|--------|-------------|--------|
| 1 | WhatsApp webhook receive + HMAC | Complete, merged |
| 2 | File upload (CSV/XLSX) | Complete, merged |
| 3 | LLM noise filter + lead pipeline | Complete, merged |
| 4 | Meta OAuth connect (WA / FB / IG) | Complete, committed — PR pending |

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
- `modules/lead_ingestion/lead_retrieval_worker.py` — Lead Ad webhook handler, Graph API fetch with retry
- `workers/jobs/instagram_refresh.py` — ARQ cron job, daily 02:00 UTC
- `migrations/versions/c8d2f1a9e3b7_gin_index_channel_connection_metadata.py` — GIN index on metadata JSONB

### Modified Files
- `api/lead_ingestion.py` — 5 new OAuth endpoints + FB/IG webhook routing
- `core/config.py` — added `meta_embedded_signup_config_id`, `base_url`
- `modules/lead_ingestion/db/repository.py` — added `get_connection_by_page_or_ig_account_id`
- `modules/lead_ingestion/service.py` — exposed OAuth + crypto symbols
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

## Meta App Setup — Current State

### Completed
- Meta Developer Account + App created (Business type)
- **App ID:** `1494580908551596`
- **App Secret:** in `.env`
- WhatsApp product added + `messages` webhook field subscribed
- Facebook Login for Business added
- **Embedded Signup config_id:** `1311452217634130`
- Webhook URL configured with ngrok
- Verify Token set: `local_verify_token_sprint3`

### Still Needed (before Sprint 5 live testing)
- Instagram product NOT added to Meta App
- Messenger product NOT added to Meta App
- Facebook/Instagram redirect URIs NOT registered in Meta App Dashboard
- Run `make migrate` to apply the GIN index migration (`c8d2f1a9e3b7`)

---

## Environment

### .env (current — do not lose these values)
`
CHANNEL_CREDENTIALS_ENCRYPTION_KEY=yYA-YOw2Rox-1v236MeQeJTt8V4kY9D0ox9t_Tjsm70=
META_APP_ID=1494580908551596
META_EMBEDDED_SIGNUP_CONFIG_ID=1311452217634130
META_GRAPH_API_VERSION=v21.0
BASE_URL=https://lorna-nonutilized-macy.ngrok-free.dev  <- update each ngrok restart
`

### To Start the Environment
`powershell
# 1. Start Docker Desktop from Start menu

# 2. Start containers
docker-compose up -d

# 3. Apply any new migrations
uv run alembic upgrade head

# 4. Start backend
uv run uvicorn main:app --reload --port 8000

# 5. In a NEW terminal - start ngrok
ngrok http 8000
# Update BASE_URL in .env with the new https URL
`

---

## Architecture Decisions (Locked)

- Token storage: `ChannelConnection.credentials_encrypted` (AES-256-GCM) — NOT AWS Secrets Manager
- LLM: Groq only — `import anthropic` is banned
- Job queue: ARQ — no Inngest
- `ChannelConnection` lives in `shared/channels/models.py`
- All external imports from `modules/lead_ingestion/` go through `service.py`
- Single Meta webhook URL for all tenants — routed by `phone_number_id` / `page_id` / `ig_account_id`

---

## Next: Sprint 5

Sprint 5 scope (to be confirmed):
- Enrichment module (`modules/enrichment/`) — cache-first external lookup pipeline
- Surepass, Serper, NewsCatcher, Probe42 clients
- `LeadEnriched` event emission

Key reference:
- Full Meta integration spec: `docs/meta-integration-implementation.md`
- Sprint plan: `docs/epic4-sprint-plan.md`
- Architecture rules: `.claude/rules/architecture.md`