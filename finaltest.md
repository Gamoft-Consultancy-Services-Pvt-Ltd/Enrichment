# E2E Test Map — Lead Ingestion Engine (Epic 4 Complete — 71 Phases)

> Rule: if a test fails, fix it before moving on.  
> Tester: Claude via Playwright MCP + Postgres MCP.

---

## Prerequisites — All Must Be Running Before Any Test

Run each command in a **separate terminal** from `C:\Users\anish\Desktop\LEAD INGESTION\Enrichment`.

| # | What | Command |
|---|------|---------|
| 1 | Docker (Postgres + Redis) | `docker-compose up -d` |
| 2 | Migrations applied | `uv run alembic upgrade head` |
| 3 | FastAPI server | `uv run uvicorn main:app --reload --port 8000` |
| 4 | ARQ worker | `uv run arq workers.worker.WorkerSettings` |
| 5 | ngrok tunnel | `ngrok http --domain=lorna-nonutilized-macy.ngrok-free.dev 8000` |

Verify all are up: `https://lorna-nonutilized-macy.ngrok-free.dev/health` → `{"status":"ok"}`

---

## JWT Capture Strategy

After Auth0 login via Swagger:
1. Click **Try it out → Execute** on `GET /me`
2. Use `browser_network_requests` to find the `/me` request
3. Extract `Authorization: Bearer eyJ…` header value
4. Store the full `Bearer <token>` string for all subsequent `fetch()` calls

---

## HMAC Signing (all webhook POST tests)

```
app_secret = "8a7ad9d320e984b58ecd57000a7eb799"
signature  = "sha256=" + hmac_sha256_hex(key=app_secret, msg=raw_body_bytes)
header     = X-Hub-Signature-256: <signature>
```

SubtleCrypto version (runs in Playwright `browser_evaluate`):
```javascript
async function hmacSign(secret, bodyString) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    {name: "HMAC", hash: "SHA-256"}, false, ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(bodyString));
  return "sha256=" + Array.from(new Uint8Array(sig))
    .map(b => b.toString(16).padStart(2, '0')).join('');
}
```

---

## SECTION A — Infrastructure

---

### Phase 1 — Health Check

**Tool:** Playwright  
**Action:** Navigate to `http://localhost:8000/health`  
**Pass:** HTTP 200, body `{"status":"ok"}`  
**Fail:** Server not started — check Terminal 3 logs

---

### Phase 2 — Auth0 Login + JWT Capture

**Tool:** Playwright  
**Steps:**
1. Navigate to `http://localhost:8000/docs`
2. Click **Authorize** (top right) → OAuth dialog
3. Click the green **Authorize** button → Auth0 login page opens
4. Email: `anishekh@leadengine.dev` | Password: `Asha@1234` → Continue
5. Auth0 redirects back → Swagger shows **Authorized** → Close dialog
6. Expand `GET /me` → **Try it out** → **Execute**
7. Run `browser_network_requests` → find request to `/me` → copy `Authorization` header

**Pass:** Swagger shows "Authorized", `/me` returns 200  
**Fail:** Check `.env` values — `AUTH0_DOMAIN`, `AUTH0_AUDIENCE`, `AUTH0_SPA_CLIENT_ID`. Restart server (no `--reload`) after any `.env` change.

---

### Phase 3 — GET /me + User Row in DB

**Tool:** Playwright (Swagger) + Postgres MCP  
**Pass conditions:**
- Response: `role: "PLATFORM_ADMIN"`, `tenant_id: null`, `email: "anishekh@leadengine.dev"`
- DB:
```sql
SELECT id, auth0_sub, email, role, tenant_id FROM users ORDER BY created_at DESC LIMIT 1;
-- Expect: role='PLATFORM_ADMIN', tenant_id=NULL
```

**Sub-checks:**
```sql
-- 3.1: email in DB matches JWT claim exactly (case-sensitive)
SELECT email FROM users WHERE email = 'anishekh@leadengine.dev';
-- Expect: 1 row

-- 3.2: created_at and updated_at both set (not NULL)
SELECT created_at, updated_at FROM users ORDER BY created_at DESC LIMIT 1;
-- Expect: both non-NULL timestamps
```

**Fail:** Auth0 Action not wired. Check Triggers → post-login → `https://leadengine/role` claim.

---

### Phase 4 — Tenant Onboarding

**Tool:** Playwright (Swagger) + Postgres MCP  
**Steps:**
1. Expand `POST /onboarding` → **Try it out** → paste body:
```json
{
  "company_name": "Lead Engine Test Co",
  "primary_contact_name": "Anishekh",
  "primary_contact_email": "anishekh@leadengine.dev",
  "business_type": "B2B",
  "website_url": "https://stripe.com",
  "timezone": "Asia/Kolkata",
  "language_preference": "en"
}
```
2. Execute → copy `tenant_id` from response (needed in every subsequent phase)
3. Poll `GET /me` every 10s; wait up to 3 minutes for `onboarding_status = "COMPLETE"`

**DB checks:**
```sql
SELECT id, company_name, onboarding_status, status FROM tenants ORDER BY created_at DESC LIMIT 1;
-- Expect: onboarding_status='COMPLETE', status='ACTIVE'

SELECT id, version, status, jsonb_array_length(signals) AS signal_count
FROM tenant_configs WHERE status = 'ACTIVE' ORDER BY created_at DESC LIMIT 1;
-- Expect: status='ACTIVE', signal_count > 0
```

**Sub-checks:**
```sql
-- 4.1: website_url stored correctly
SELECT website_url FROM tenants WHERE id = '<tenant_id>';
-- Expect: 'https://stripe.com'

-- 4.2: exactly ONE active config
SELECT COUNT(*) FROM tenant_configs WHERE tenant_id = '<tenant_id>' AND status = 'ACTIVE';
-- Expect: 1

-- 4.3: config has weights and thresholds
SELECT jsonb_array_length(signals) AS signals,
  (scoring_weights IS NOT NULL) AS has_weights,
  (scoring_thresholds IS NOT NULL) AS has_thresholds
FROM tenant_configs WHERE tenant_id = '<tenant_id>' AND status = 'ACTIVE';
-- Expect: signals > 0, has_weights=true, has_thresholds=true

-- 4.4: no stuck RUNNING/FAILED status
SELECT onboarding_status FROM tenants WHERE id = '<tenant_id>';
-- Expect: 'COMPLETE'
```

**Fail:** Check ARQ worker logs. `FAILED` → check Groq API key and `https://stripe.com` is reachable.

---

### Phase 5 — Meta Webhook Hub Challenge

**Tool:** Playwright  
**Action:** Navigate to:
```
http://localhost:8000/channels/webhook?hub.mode=subscribe&hub.verify_token=local_verify_token_sprint3&hub.challenge=test_challenge_abc123
```
**Pass:** Response body = `test_challenge_abc123`, HTTP 200

**Sub-check 5.1 — Wrong verify token must be rejected:**
```
http://localhost:8000/channels/webhook?hub.mode=subscribe&hub.verify_token=WRONG_TOKEN&hub.challenge=test_challenge_abc123
```
**Pass:** HTTP 403 or 400 — challenge NOT echoed back  
**Fail:** If challenge is echoed with wrong token — any attacker can subscribe to the webhook.

---

## SECTION B — WhatsApp DM Channel

---

### Phase 6 — WhatsApp DM Golden Path

**Tool:** Playwright (JS fetch) + Postgres MCP

**Setup — insert ChannelConnection:**
```sql
INSERT INTO channel_connections (id, tenant_id, channel_type, status, metadata, created_at, updated_at)
VALUES (
  gen_random_uuid(),
  '<tenant_id from Phase 4>',
  'whatsapp',
  'active',
  '{"phone_number_id": "987654321"}',
  now(), now()
)
RETURNING id;
-- Store this as <wa_connection_id>
```

**Payload:**
```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "123456789",
    "changes": [{
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "919876543210", "phone_number_id": "987654321"},
        "contacts": [{"profile": {"name": "Priya Mehta"}, "wa_id": "919876543210"}],
        "messages": [{
          "from": "919876543210",
          "id": "wamid.sprint3golden001",
          "timestamp": "1700000000",
          "text": {"body": "Hi I want to learn more about your product pricing and features"},
          "type": "text"
        }]
      },
      "field": "messages"
    }]
  }]
}
```

**Send webhook** (Playwright `browser_evaluate`):
```javascript
const payload = { /* wa_dm_01.json contents above */ };
const body = JSON.stringify(payload);
const sig = await hmacSign("8a7ad9d320e984b58ecd57000a7eb799", body);
const res = await fetch("http://localhost:8000/channels/webhook", {
  method: "POST", body,
  headers: {"Content-Type": "application/json", "X-Hub-Signature-256": sig}
});
return {status: res.status, data: await res.json()};
```

Wait 3s for ARQ worker.

**DB checks:**
```sql
SELECT id, pipeline_stage, source_channel, full_name, phone, email
FROM leads WHERE tenant_id = '<tenant_id>' ORDER BY created_at DESC LIMIT 1;
-- Expect: pipeline_stage='captured', phone='+919876543210', full_name='Priya Mehta', source_channel='whatsapp'

SELECT id, platform_event_id, status FROM intake_event_logs
WHERE platform_event_id = 'wamid.sprint3golden001';
-- Expect: 1 row, status='received'
```

**Sub-checks:**
```sql
-- 6.1: lead_touchpoints row created
SELECT lt.id, lt.platform_event_id FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.phone = '+919876543210' AND l.tenant_id = '<tenant_id>';
-- Expect: 1 row with platform_event_id='wamid.sprint3golden001'

-- 6.2: lead's channel_connection_id points to the ChannelConnection we inserted
SELECT channel_connection_id FROM leads
WHERE phone = '+919876543210' AND tenant_id = '<tenant_id>';
-- Expect: <wa_connection_id>

-- 6.3: intake_event_log not stuck in processing
SELECT status FROM intake_event_logs WHERE platform_event_id = 'wamid.sprint3golden001';
-- Expect: 'received' (not 'processing' or 'failed')
```

**Fail:** Check Groq API key (Stage 2 LLM filter). Check ChannelConnection INSERT ran. Check ARQ worker logs.

---

### Phase 7 — Noise Filter Stage 1: Emoji Short-Circuit

**Tool:** Playwright (JS fetch) + Postgres MCP

**Payload:**
```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "123456789",
    "changes": [{
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "919999999999", "phone_number_id": "987654321"},
        "contacts": [{"profile": {"name": "Unknown"}, "wa_id": "919999999999"}],
        "messages": [{
          "from": "919999999999",
          "id": "wamid.sprint3noise001",
          "timestamp": "1700000001",
          "text": {"body": "👍"},
          "type": "text"
        }]
      },
      "field": "messages"
    }]
  }]
}
```

Same HMAC + fetch as Phase 6. Wait 3s.

**DB checks:**
```sql
SELECT pipeline_stage FROM leads WHERE tenant_id = '<tenant_id>'
ORDER BY created_at DESC LIMIT 1;
-- Expect: pipeline_stage='insufficient_signal'

SELECT status FROM intake_event_logs WHERE platform_event_id = 'wamid.sprint3noise001';
-- Expect: status='insufficient_signal'
```

**Sub-checks:**
```sql
-- 7.1: no touchpoint for noise
SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id WHERE l.phone = '+919999999999';
-- Expect: 0

-- 7.2: captured count unchanged (still 1 from Phase 6)
SELECT COUNT(*) FROM leads WHERE tenant_id = '<tenant_id>' AND pipeline_stage = 'captured';
-- Expect: 1
```

---

### Phase 7A — Noise Filter Stage 1: Single Noise Word

**What it tests:** Single-word noise ("Hi") short-circuits without calling Groq.

**Payload:** Same structure as Phase 7 but:
- `"id": "wamid.noise_word_001"`, `"from": "919988887777"`, `"body": "Hi"`

Wait 3s.

**DB checks:**
```sql
SELECT pipeline_stage FROM leads
WHERE id = (SELECT lead_id FROM intake_event_logs
            WHERE platform_event_id = 'wamid.noise_word_001');
-- Expect: 'insufficient_signal'

SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id WHERE l.phone = '+919988887777';
-- Expect: 0

SELECT COUNT(*) FROM leads WHERE tenant_id = '<tenant_id>' AND pipeline_stage = 'captured';
-- Expect: still 1
```

**Fail:** Check `two_stage_filter.py` Stage 1 noise word list includes "hi".

---

### Phase 7B — Noise Filter Stage 2: Groq EXISTING_CUSTOMER

**What it tests:** A message clearly from an existing customer routes to `pipeline_stage='existing_customer'`.

**Payload:** Same structure but:
- `"id": "wamid.existing_cust_001"`, `"from": "919977776666"`
- `"body": "I already subscribed to your plan last month, I need support for my existing account."`

Wait 5s (Groq call takes longer than Stage 1 short-circuit).

**DB checks:**
```sql
SELECT pipeline_stage FROM leads
WHERE id = (SELECT lead_id FROM intake_event_logs
            WHERE platform_event_id = 'wamid.existing_cust_001');
-- Expect: 'existing_customer'

SELECT status FROM intake_event_logs WHERE platform_event_id = 'wamid.existing_cust_001';
-- Expect: 'existing_customer'

SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id WHERE l.phone = '+919977776666';
-- Expect: 0
```

**Fail:** Groq classified as LEAD — check Stage 2 prompt. Groq unavailable — check `GROQ_API_KEY` in `.env`.

---

### Phase 8 — WhatsApp DM Idempotency (Re-delivery)

**Steps:** Resend exact Phase 6 payload (same `platform_event_id = "wamid.sprint3golden001"`).

**DB checks:**
```sql
SELECT COUNT(*) FROM intake_event_logs WHERE platform_event_id = 'wamid.sprint3golden001';
-- Expect: 1 (ON CONFLICT DO NOTHING)

SELECT COUNT(*) FROM leads WHERE tenant_id = '<tenant_id>' AND phone = '+919876543210';
-- Expect: 1 (no duplicate lead)
```

**Sub-checks:**
```sql
-- 8.1: touchpoints count unchanged
SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.phone = '+919876543210' AND l.tenant_id = '<tenant_id>';
-- Expect: still 1

-- 8.2: status unchanged
SELECT status FROM intake_event_logs WHERE platform_event_id = 'wamid.sprint3golden001';
-- Expect: still 'received'
```

---

### Phase 9 — WhatsApp DM Deduplication (New Event, Same Identity)

**Steps:** Send Phase 6 payload but change `"id"` to `"wamid.dedup_test_001"` (same phone `919876543210`).

**DB checks:**
```sql
SELECT COUNT(*) FROM leads WHERE phone = '+919876543210' AND tenant_id = '<tenant_id>';
-- Expect: 1

SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id WHERE l.phone = '+919876543210';
-- Expect: 2 (touchpoint added to existing lead)

SELECT status FROM intake_event_logs WHERE platform_event_id = 'wamid.dedup_test_001';
-- Expect: 'duplicate'
```

**Sub-checks:**
```sql
-- 9.1: new touchpoint has correct platform_event_id
SELECT lt.platform_event_id FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.phone = '+919876543210' AND lt.platform_event_id = 'wamid.dedup_test_001';
-- Expect: 1 row

-- 9.2: touchpoint has raw_event_json
SELECT raw_event_json IS NOT NULL AS has_raw FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.phone = '+919876543210' AND lt.platform_event_id = 'wamid.dedup_test_001';
-- Expect: true
```

---

### Phase 9A — WhatsApp DM Pre-Flight Block (No Active Config)

**What it tests:** A WA DM arrives when tenant has no active `tenant_config` → `pre_flight_blocked`.

**Setup:**
```sql
UPDATE tenant_configs SET status = 'ARCHIVED' WHERE tenant_id = '<tenant_id>' AND status = 'ACTIVE';
```

**Payload:** New sender, genuine-looking lead message:
- `"id": "wamid.preflight_block_001"`, `"from": "919966665555"`
- `"body": "I want to buy your premium plan please"`

Wait 5s.

**DB checks:**
```sql
SELECT pipeline_stage, pre_flight_block_reason FROM leads
WHERE id = (SELECT lead_id FROM intake_event_logs
            WHERE platform_event_id = 'wamid.preflight_block_001');
-- Expect: pipeline_stage='pre_flight_blocked', pre_flight_block_reason IS NOT NULL
```

**Teardown:**
```sql
UPDATE tenant_configs SET status = 'ACTIVE'
WHERE tenant_id = '<tenant_id>' AND status = 'ARCHIVED'
  AND version = (SELECT MAX(version) FROM tenant_configs WHERE tenant_id = '<tenant_id>');
```

**Fail:** Check `pipeline.py` pre-flight check is executed in `run_capture_message` before `run_capture`.

---

## SECTION C — File Upload

---

### Phase 10 — CSV Upload Golden Path

**Tool:** Playwright (Swagger file upload) + Postgres MCP

**Test file:** `tests/fixtures/lead_ingestion/csv_01.csv`
- Row 1: Alice Sharma, phone=`+919876543210`, email=`alice@example.com`
- Row 2: Bob Kumar, email=`bob@example.com`, no phone
- Row 3: no name, no phone, no email

**Steps:**
1. In Swagger, expand `POST /channels/inbound/file-upload`
2. **Try it out** → choose file → **Execute** (JWT active from Phase 2)

**DB checks:**
```sql
SELECT full_name, phone, email, pipeline_stage FROM leads
WHERE tenant_id = '<tenant_id>' AND source_channel = 'file_upload'
ORDER BY created_at DESC LIMIT 5;
```
Expect: Alice=captured, Bob=captured, anonymous=pre_flight_blocked

**Sub-checks:**
```sql
-- 10.1: Alice deduped — still 1 lead for +919876543210 across all channels
SELECT COUNT(*) FROM leads WHERE phone = '+919876543210' AND tenant_id = '<tenant_id>';
-- Expect: 1

-- 10.2: that lead has ≥2 touchpoints (WhatsApp + file_upload)
SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.phone = '+919876543210' AND l.tenant_id = '<tenant_id>';
-- Expect: ≥ 2

-- 10.3: Bob has email but phone IS NULL
SELECT phone, email FROM leads WHERE email = 'bob@example.com' AND tenant_id = '<tenant_id>';
-- Expect: phone=NULL, email='bob@example.com'

-- 10.4: Row 3 block reason
SELECT pipeline_stage, pre_flight_block_reason FROM leads
WHERE tenant_id = '<tenant_id>' AND pipeline_stage = 'pre_flight_blocked'
ORDER BY created_at DESC LIMIT 1;
-- Expect: pre_flight_block_reason='insufficient_identity_fields'

-- 10.5: intake_event_logs has entries for all 3 rows
SELECT COUNT(*) FROM intake_event_logs
WHERE tenant_id = '<tenant_id>' AND source_channel = 'file_upload'
ORDER BY created_at DESC LIMIT 1;
-- Expect: ≥ 3
```

---

### Phase 10A — XLSX Upload Format Parity

**What it tests:** XLSX files are accepted and processed identically to CSV.

**Test file:** `tests/fixtures/lead_ingestion/xlsx_01.xlsx` — same 3 rows as `csv_01.csv`.

**Steps:** Same as Phase 10 but upload `xlsx_01.xlsx`.

**Pass:** Same DB outcomes as Phase 10. Alice deduped (now ≥3 touchpoints). Bob still email-only. Row 3 blocked.

---

### Phase 10B — Wrong File Type → 422

**Steps:** Upload `tests/fixtures/lead_ingestion/sample.docx` via Swagger.

**Pass:** HTTP 422, error mentions unsupported file type.

```sql
SELECT COUNT(*) FROM leads WHERE tenant_id = '<tenant_id>'
  AND created_at > now() - interval '10 seconds';
-- Expect: 0
```

---

### Phase 10C — Unauthenticated Upload → 401

**Steps:** In Playwright `browser_evaluate`, POST to `/channels/inbound/file-upload` with no Authorization header (use raw fetch, not Swagger).

**Pass:** HTTP 401. No DB rows created.

---

### Phase 10D — Upload With No Active TenantConfig → 400

**Setup:**
```sql
UPDATE tenant_configs SET status = 'ARCHIVED' WHERE tenant_id = '<tenant_id>' AND status = 'ACTIVE';
```

**Steps:** Upload `csv_01.csv` via Swagger (authenticated as Tenant A).

**Pass:** HTTP 400, body `{"detail":"no_active_config"}` or equivalent.

**Teardown:**
```sql
UPDATE tenant_configs SET status = 'ACTIVE'
WHERE tenant_id = '<tenant_id>' AND status = 'ARCHIVED'
  AND version = (SELECT MAX(version) FROM tenant_configs WHERE tenant_id = '<tenant_id>');
```

---

### Phase 10E — Large File → Async Mode

**Test file:** Generate `tests/fixtures/lead_ingestion/csv_large.csv` — 150 rows (each with a unique email, above the 100-row sync threshold but below the 5000-row limit).

**Steps:** Upload via Swagger.

**Pass:** HTTP 200, response body `{"mode": "async", "row_count": 150}`. After ~5s, ARQ processes batch:

```sql
SELECT COUNT(*) FROM leads WHERE tenant_id = '<tenant_id>'
  AND source_channel = 'file_upload' AND created_at > now() - interval '60 seconds';
-- Expect: > 100 rows processed
```

> **Note:** Response is HTTP 200, not 202. Body contains `row_count`, not `arq_job_id`.

**Fail:** Check `file_upload_handler.py` sync/async row threshold (_SYNC_THRESHOLD = 100) and that ARQ worker is running (Terminal 4).

---

## SECTION D — Facebook OAuth & Messenger DMs

---

### Phase 11 — Facebook OAuth Connect

**Tool:** Playwright

**Steps:**
1. In Swagger, expand `GET /channels/oauth/facebook` → **Try it out** → **Execute**
2. Grab the redirect URL from the response → navigate to it in browser
3. Facebook login → approve permissions (select pages to grant)
4. Callback lands at `/channels/oauth/facebook/callback` → response: `{"status":"connected","page_count":N}`

**DB check:**
```sql
SELECT id, channel_type, status, metadata FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'facebook';
-- Expect: ≥1 row, status='active', metadata contains page_id
```

**Sub-checks:**
```sql
-- 11.1: expires_at IS NULL — page tokens never expire
SELECT expires_at FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'facebook';
-- Expect: NULL

-- 11.2: credentials_encrypted NOT NULL
SELECT credentials_encrypted IS NOT NULL AS has_creds FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'facebook';
-- Expect: true

-- 11.3: metadata has page_id AND page_name
SELECT metadata->>'page_id' AS page_id, metadata->>'page_name' AS page_name
FROM channel_connections WHERE tenant_id = '<tenant_id>' AND channel_type = 'facebook';
-- Expect: both non-NULL and non-empty
```

**Fail:** Check Meta App has Messenger product added. Check redirect URI `https://lorna-nonutilized-macy.ngrok-free.dev/channels/oauth/facebook/callback` is registered.

---

### Phase 11A — Facebook OAuth Tampered State → Rejected

**What it tests:** A modified `state` param in the OAuth callback triggers HMAC rejection. No ChannelConnection created.

**Steps:**
1. Call `GET /channels/oauth/facebook` in Swagger → copy the redirect URL.
2. In Playwright, navigate directly to the callback URL with a tampered state:
   ```
   http://localhost:8000/channels/oauth/facebook/callback?state=TAMPERED_VALUE_XXXX&code=fake_code
   ```

**Pass:** HTTP 400 or 403, body contains `oauth_state_error` or `invalid_state`.

```sql
SELECT COUNT(*) FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND created_at > now() - interval '10 seconds';
-- Expect: 0 (no connection created from tampered request)
```

---

### Phase 12 — Facebook DM Golden Path

**Tool:** Playwright (JS fetch) + Postgres MCP

**Prerequisite:** Phase 11 complete.

**Get page_id:**
```sql
SELECT metadata->>'page_id' AS page_id FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'facebook' LIMIT 1;
```

**Payload** (substitute `<page_id>`):
```json
{
  "object": "page",
  "entry": [{
    "id": "<page_id>",
    "messaging": [{
      "sender": {"id": "4567890123"},
      "recipient": {"id": "<page_id>"},
      "timestamp": 1700000100,
      "message": {
        "mid": "m_sprint4_fb_dm_001",
        "text": "Hi I want to know more about your product features and pricing"
      }
    }]
  }]
}
```

Send with HMAC. Wait 3s.

**DB checks:**
```sql
SELECT id, pipeline_stage, source_channel, full_name FROM leads
WHERE tenant_id = '<tenant_id>' AND source_channel = 'facebook'
ORDER BY created_at DESC LIMIT 1;
-- Expect: pipeline_stage='captured', source_channel='facebook'

SELECT platform_event_id, status FROM intake_event_logs
WHERE platform_event_id = 'm_sprint4_fb_dm_001';
-- Expect: 1 row, status='received'
```

**Sub-checks:**
```sql
-- 12.1: touchpoint created
SELECT lt.platform_event_id FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id WHERE lt.platform_event_id = 'm_sprint4_fb_dm_001';
-- Expect: 1 row

-- 12.2: phone and email NULL — Facebook sender is identified by PSID only
SELECT phone, email FROM leads
WHERE tenant_id = '<tenant_id>' AND source_channel = 'facebook' ORDER BY created_at DESC LIMIT 1;
-- Expect: phone=NULL, email=NULL
```

---

### Phase 12A — Facebook DM Noise → insufficient_signal

**Prerequisite:** Phase 11 complete.

**Payload:** Same as Phase 12 but `"mid": "m_sprint4_fb_noise_001"`, `"sender": {"id": "9999000001"}`, `"text": "👍"`.

Wait 3s.

**DB checks:**
```sql
SELECT pipeline_stage FROM leads
WHERE id = (SELECT lead_id FROM intake_event_logs
            WHERE platform_event_id = 'm_sprint4_fb_noise_001');
-- Expect: 'insufficient_signal'

SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id WHERE lt.platform_event_id = 'm_sprint4_fb_noise_001';
-- Expect: 0
```

---

### Phase 12B — Facebook DM Idempotency

**Steps:** Resend exact Phase 12 payload (`"mid": "m_sprint4_fb_dm_001"` unchanged).

**DB checks:**
```sql
SELECT COUNT(*) FROM intake_event_logs WHERE platform_event_id = 'm_sprint4_fb_dm_001';
-- Expect: 1

SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id WHERE lt.platform_event_id = 'm_sprint4_fb_dm_001';
-- Expect: still 1
```

---

### Phase 12C — Facebook DM Dedup (Same PSID, New Event)

**Steps:** Send Phase 12 payload with `"mid": "m_sprint4_fb_dm_dedup_001"` but same `sender.id` (`4567890123`).

**DB checks:**
```sql
SELECT COUNT(*) FROM leads
WHERE tenant_id = '<tenant_id>' AND source_channel = 'facebook';
-- Expect: 1

SELECT status FROM intake_event_logs WHERE platform_event_id = 'm_sprint4_fb_dm_dedup_001';
-- Expect: 'duplicate'

SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id WHERE lt.platform_event_id = 'm_sprint4_fb_dm_dedup_001';
-- Expect: 1
```

---

## SECTION E — Instagram OAuth & DMs

---

### Phase 13 — Instagram OAuth Connect

**Tool:** Playwright

1. In Swagger, expand `GET /channels/oauth/instagram` → **Try it out** → **Execute**
2. Grab the redirect URL → navigate to it in browser
3. Instagram login → approve permissions
4. Callback lands at `/channels/oauth/instagram/callback` → response: `{"status":"connected"}`

**DB check:**
```sql
SELECT id, channel_type, status, metadata, expires_at FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram';
-- Expect: 1 row, status='active', expires_at ~60 days from now, metadata has ig_account_id
```

**Sub-checks:**
```sql
-- 13.1: expires_at is ~60 days from now
SELECT
  expires_at,
  expires_at > now() AS not_expired,
  expires_at < now() + interval '61 days' AS within_60_days
FROM channel_connections WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram';
-- Expect: not_expired=true, within_60_days=true

-- 13.2: credentials_encrypted NOT NULL
SELECT credentials_encrypted IS NOT NULL AS has_creds FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram';
-- Expect: true

-- 13.3: metadata has ig_account_id AND username
SELECT metadata->>'ig_account_id' AS ig_id, metadata->>'username' AS uname
FROM channel_connections WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram';
-- Expect: both non-NULL and non-empty
```

**Fail:** Check Instagram product added to Meta App. Check redirect URI registered. Check `instagram_business_basic` and `instagram_manage_messages` permissions approved.

---

### Phase 13A — Instagram OAuth Tampered State → Rejected

**Steps:** Navigate directly to:
```
http://localhost:8000/channels/oauth/instagram/callback?state=TAMPERED_VALUE_XXXX&code=fake_code
```

**Pass:** HTTP 400 or 403, body contains `oauth_state_error`. No ChannelConnection created.

```sql
SELECT COUNT(*) FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram'
  AND created_at > now() - interval '10 seconds';
-- Expect: 0
```

---

### Phase 14 — Instagram DM Golden Path

**Tool:** Playwright (JS fetch) + Postgres MCP

**Prerequisite:** Phase 13 complete.

**Get ig_account_id:**
```sql
SELECT metadata->>'ig_account_id' AS ig_account_id FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram' LIMIT 1;
```

**Payload** (substitute `<ig_account_id>`):
```json
{
  "object": "instagram",
  "entry": [{
    "id": "<ig_account_id>",
    "messaging": [{
      "sender": {"id": "7890123456"},
      "recipient": {"id": "<ig_account_id>"},
      "timestamp": 1700000200,
      "message": {
        "mid": "m_sprint4_ig_dm_001",
        "text": "Hello I am interested in your services and would like to learn more"
      }
    }]
  }]
}
```

Send with HMAC. Wait 3s.

**DB checks:**
```sql
SELECT id, pipeline_stage, source_channel FROM leads
WHERE tenant_id = '<tenant_id>' AND source_channel = 'instagram'
ORDER BY created_at DESC LIMIT 1;
-- Expect: pipeline_stage='captured', source_channel='instagram'

SELECT platform_event_id, status FROM intake_event_logs
WHERE platform_event_id = 'm_sprint4_ig_dm_001';
-- Expect: 1 row, status='received'
```

**Sub-checks:**
```sql
-- 14.1: touchpoint created
SELECT lt.platform_event_id FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id WHERE lt.platform_event_id = 'm_sprint4_ig_dm_001';
-- Expect: 1 row

-- 14.2: phone NULL — Instagram sender is IGSID, not phone
SELECT phone FROM leads
WHERE tenant_id = '<tenant_id>' AND source_channel = 'instagram' ORDER BY created_at DESC LIMIT 1;
-- Expect: NULL
```

---

### Phase 14A — Instagram DM Noise → insufficient_signal

**Payload:** Same as Phase 14 but `"mid": "m_sprint4_ig_noise_001"`, `"sender": {"id": "8880000001"}`, `"text": "👍"`.

Wait 3s.

**DB checks:**
```sql
SELECT pipeline_stage FROM leads
WHERE id = (SELECT lead_id FROM intake_event_logs
            WHERE platform_event_id = 'm_sprint4_ig_noise_001');
-- Expect: 'insufficient_signal'

SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id WHERE lt.platform_event_id = 'm_sprint4_ig_noise_001';
-- Expect: 0
```

---

### Phase 14B — Instagram DM Idempotency

**Steps:** Resend exact Phase 14 payload (`"mid": "m_sprint4_ig_dm_001"` unchanged).

**DB checks:**
```sql
SELECT COUNT(*) FROM intake_event_logs WHERE platform_event_id = 'm_sprint4_ig_dm_001';
-- Expect: 1

SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id WHERE lt.platform_event_id = 'm_sprint4_ig_dm_001';
-- Expect: still 1
```

---

## SECTION F — Facebook Lead Ads

**Context:** Leadgen webhooks bypass the LLM noise filter — form submissions are explicit lead intent. Flow: `POST /channels/webhook` with `field: "leadgen"` → `_route_lead_ad()` → enqueue `run_lead_ad_capture` → ARQ fetches fields from Graph API → `normalise_lead_ad_form()` → `run_capture()`.

**Seeded test data (dev DB from 2026-06-19 manual test):**
- Page ID: `1346146538572443`
- Test leadgen_id: `857144174130084`
- ChannelConnection ID: `04edabdf-948b-41d5-8e91-6314de273148`

---

### Phase 15A — Lead Ads: Verify ChannelConnection Active

**Tool:** Postgres MCP

```sql
SELECT id, channel_type, status, metadata->>'page_id' AS page_id
FROM channel_connections WHERE id = '04edabdf-948b-41d5-8e91-6314de273148';
-- Expect: status='active', page_id='1346146538572443'
```

If the row is missing (DB was wiped), re-seed:
```bash
uv run python scripts/seed_test_connection.py
```

---

### Phase 15B — Lead Ads Golden Path: Webhook → Capture

**Tool:** Playwright (JS fetch) + Postgres MCP

**Payload:**
```json
{
  "object": "page",
  "entry": [{
    "id": "1346146538572443",
    "changes": [{
      "value": {
        "form_id": "123456789",
        "leadgen_id": "857144174130084",
        "page_id": "1346146538572443",
        "ad_id": "111",
        "created_time": 1700000300
      },
      "field": "leadgen"
    }]
  }]
}
```

Send with HMAC. Wait 5s for ARQ (Graph API call adds latency).

**DB checks:**
```sql
SELECT id, pipeline_stage, source_channel, full_name, email
FROM leads WHERE tenant_id = '<tenant_id>' AND source_channel = 'FACEBOOK_LEAD_ADS'
ORDER BY created_at DESC LIMIT 1;
-- Expect: pipeline_stage='captured', source_channel='FACEBOOK_LEAD_ADS', email non-NULL

SELECT platform_event_id, status FROM intake_event_logs
WHERE platform_event_id = 'leadgen-857144174130084';
-- Expect: 1 row, status='received'
```

**Sub-checks:**
```sql
-- 15B.1: Lead captured without filter (never 'insufficient_signal')
SELECT pipeline_stage FROM leads WHERE source_channel = 'FACEBOOK_LEAD_ADS'
ORDER BY created_at DESC LIMIT 1;
-- Expect: 'captured'

-- 15B.2: touchpoint created
SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.source_channel = 'FACEBOOK_LEAD_ADS' AND lt.platform_event_id = 'leadgen-857144174130084';
-- Expect: 1

-- 15B.3: email and/or full_name populated from Graph API
SELECT full_name, email FROM leads WHERE source_channel = 'FACEBOOK_LEAD_ADS'
ORDER BY created_at DESC LIMIT 1;
-- Expect: at least one of full_name or email non-NULL
```

**Fail:** Check `META_APP_ID` + `META_APP_SECRET` in `.env`. Check ARQ worker picked up `run_lead_ad_capture`. Check ChannelConnection `credentials_encrypted` is not NULL.

---

### Phase 15C — Lead Ads Idempotency (Re-deliver Same leadgen_id)

**Steps:** Resend exact Phase 15B payload (same `leadgen_id: 857144174130084`). Wait 5s.

**DB checks:**
```sql
SELECT COUNT(*) FROM intake_event_logs WHERE platform_event_id = 'leadgen-857144174130084';
-- Expect: 1 (no duplicate)

SELECT COUNT(*) FROM leads
WHERE source_channel = 'FACEBOOK_LEAD_ADS' AND tenant_id = '<tenant_id>';
-- Expect: 1 (no duplicate lead)

SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id WHERE lt.platform_event_id = 'leadgen-857144174130084';
-- Expect: still 1
```

---

## SECTION G — WhatsApp Embedded Signup

---

### Phase 16 — WhatsApp Embedded Signup

> **STATUS: BLOCKED** — Requires real WhatsApp Business account number (HITL). Skip until available.

**Tool:** Playwright (dev test page served by FastAPI)

**Steps:**
1. Navigate to `https://lorna-nonutilized-macy.ngrok-free.dev/dev-tools/whatsapp-test`
2. Paste JWT (from Phase 2) into the textarea
3. Click **Connect WhatsApp Business Account** → Meta popup opens
4. Log into Facebook → complete WhatsApp Business number connection
5. Code auto-POSTs to `https://lorna-nonutilized-macy.ngrok-free.dev/channels/embedded-signup/callback`

**DB check:**
```sql
SELECT id, channel_type, status, metadata FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'whatsapp' ORDER BY created_at DESC LIMIT 5;
-- Expect: row(s) with channel_type='whatsapp', metadata has phone_number_id
```

**Sub-checks:**
```sql
-- 16.1: expires_at IS NULL — BISU tokens managed by Meta
SELECT expires_at FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'whatsapp';
-- Expect: NULL

-- 16.2: credentials_encrypted NOT NULL
SELECT credentials_encrypted IS NOT NULL AS has_creds FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'whatsapp';
-- Expect: true

-- 16.3: phone_number_id in metadata (required for routing)
SELECT metadata->>'phone_number_id' AS phone_number_id FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'whatsapp';
-- Expect: non-NULL, non-empty
```

---

## SECTION H — Security Gates

---

### Phase 17 — HMAC Rejection: Bad Signature (WhatsApp)

**Tool:** Playwright (JS fetch)

```javascript
const body = JSON.stringify({
  "object": "whatsapp_business_account",
  "entry": [{"id": "123456789", "changes": [{"value": {
    "messaging_product": "whatsapp",
    "metadata": {"phone_number_id": "987654321"},
    "messages": [{"from": "919876543210", "id": "wamid.hmactest001",
                  "type": "text", "text": {"body": "test"}}]
  }, "field": "messages"}]}]
});
const res = await fetch("http://localhost:8000/channels/webhook", {
  method: "POST", body,
  headers: {
    "Content-Type": "application/json",
    "X-Hub-Signature-256": "sha256=baddeadbeef000000000000000000000000000000000000000000000000000000"
  }
});
return {status: res.status, data: await res.json()};
```

**Pass:** HTTP 403, `{"detail":"invalid_signature"}`

```sql
SELECT COUNT(*) FROM intake_event_logs WHERE platform_event_id = 'wamid.hmactest001';
-- Expect: 0
```

---

### Phase 17A — HMAC Rejection: Missing Signature Header

```javascript
const body = JSON.stringify({
  "object": "whatsapp_business_account",
  "entry": [{"id": "123456789", "changes": [{"value": {
    "messaging_product": "whatsapp",
    "metadata": {"phone_number_id": "987654321"},
    "messages": [{"from": "919876543210", "id": "wamid.hmactest002",
                  "type": "text", "text": {"body": "test"}}]
  }, "field": "messages"}]}]
});
const res = await fetch("http://localhost:8000/channels/webhook", {
  method: "POST", body,
  headers: {"Content-Type": "application/json"}
  // No X-Hub-Signature-256
});
return {status: res.status};
```

**Pass:** HTTP 403. No `intake_event_logs` row for `wamid.hmactest002`.

---

### Phase 17B — HMAC Rejection: Facebook DM Payload

```javascript
const body = JSON.stringify({
  "object": "page",
  "entry": [{"id": "1346146538572443", "messaging": [{
    "sender": {"id": "9991234567"},
    "recipient": {"id": "1346146538572443"},
    "timestamp": 1700000999,
    "message": {"mid": "m_hmactest_fb_001", "text": "test"}
  }]}]
});
const res = await fetch("http://localhost:8000/channels/webhook", {
  method: "POST", body,
  headers: {"Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=baddeadbeef000000000000000000000000000000000000000000000000000000"}
});
return {status: res.status};
// Expect: 403
```

```sql
SELECT COUNT(*) FROM intake_event_logs WHERE platform_event_id = 'm_hmactest_fb_001';
-- Expect: 0
```

---

### Phase 17C — HMAC Rejection: Instagram DM Payload

```javascript
const body = JSON.stringify({
  "object": "instagram",
  "entry": [{"id": "7890000000", "messaging": [{
    "sender": {"id": "8880000002"},
    "recipient": {"id": "7890000000"},
    "timestamp": 1700001000,
    "message": {"mid": "m_hmactest_ig_001", "text": "test"}
  }]}]
});
const res = await fetch("http://localhost:8000/channels/webhook", {
  method: "POST", body,
  headers: {"Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=baddeadbeef000000000000000000000000000000000000000000000000000000"}
});
return {status: res.status};
// Expect: 403
```

```sql
SELECT COUNT(*) FROM intake_event_logs WHERE platform_event_id = 'm_hmactest_ig_001';
-- Expect: 0
```

**Fail (any HMAC phase):** If HTTP 200 — `validate_signature()` in `webhook_receiver.py` is broken. Fix immediately — blocks production.

---

## SECTION I — Unroutable Webhooks

---

### Phase 18 — Unroutable: Unknown phone_number_id (WhatsApp)

**Tool:** Playwright (JS fetch) + Postgres MCP

```javascript
const payload = {
  "object": "whatsapp_business_account",
  "entry": [{"id": "000000000", "changes": [{"value": {
    "messaging_product": "whatsapp",
    "metadata": {"phone_number_id": "DOES_NOT_EXIST_999"},
    "messages": [{"from": "911111111111", "id": "wamid.unroutable001",
                  "type": "text", "text": {"body": "I want to buy your product"}}]
  }, "field": "messages"}]}]
};
const body = JSON.stringify(payload);
const sig = await hmacSign("8a7ad9d320e984b58ecd57000a7eb799", body);
const res = await fetch("http://localhost:8000/channels/webhook", {
  method: "POST", body,
  headers: {"Content-Type": "application/json", "X-Hub-Signature-256": sig}
});
return {status: res.status, data: await res.json()};
```

Wait 3s.

**Pass:** HTTP 200 (webhook always ACKs — never 4xx/5xx to Meta)

```sql
SELECT COUNT(*) FROM leads WHERE created_at > now() - interval '30 seconds' AND tenant_id IS NOT NULL;
-- Expect: 0

SELECT status FROM intake_event_logs WHERE platform_event_id = 'wamid.unroutable001';
-- Expect: 'unroutable'
```

---

### Phase 18A — Unroutable: Unknown page_id (Facebook)

```javascript
const payload = {
  "object": "page",
  "entry": [{"id": "PAGE_DOES_NOT_EXIST_888",
    "messaging": [{"sender": {"id": "9991111111"},
      "recipient": {"id": "PAGE_DOES_NOT_EXIST_888"},
      "timestamp": 1700001000,
      "message": {"mid": "m_unroutable_fb_001", "text": "I want to buy"}}]}]
};
```

Send with valid HMAC. Wait 3s.

**Pass:** HTTP 200.

```sql
SELECT status FROM intake_event_logs WHERE platform_event_id = 'm_unroutable_fb_001';
-- Expect: 'unroutable'

SELECT COUNT(*) FROM leads WHERE created_at > now() - interval '30 seconds';
-- Expect: 0
```

---

### Phase 18B — Unroutable: Unknown ig_account_id (Instagram)

```javascript
const payload = {
  "object": "instagram",
  "entry": [{"id": "IG_DOES_NOT_EXIST_777",
    "messaging": [{"sender": {"id": "8882222222"},
      "recipient": {"id": "IG_DOES_NOT_EXIST_777"},
      "timestamp": 1700001001,
      "message": {"mid": "m_unroutable_ig_001", "text": "Interested in services"}}]}]
};
```

Send with valid HMAC. Wait 3s.

**Pass:** HTTP 200.

```sql
SELECT status FROM intake_event_logs WHERE platform_event_id = 'm_unroutable_ig_001';
-- Expect: 'unroutable'
```

---

## SECTION J — Cross-Channel Identity

---

### Phase 19 — Unified Lead Registry (Cross-Channel Identity Merge)

**Tool:** Postgres MCP  
**Prerequisite:** Phases 6 and 10 complete.

No new webhook. Verifies dedup across WhatsApp DM (Phase 6) + file upload (Phase 10) produced ONE lead.

```sql
-- 19.1: exactly ONE lead for this phone across all channels
SELECT COUNT(*) FROM leads
WHERE phone = '+919876543210' AND tenant_id = '<tenant_id>';
-- Expect: 1

-- 19.2: that lead has touchpoints from both channels
SELECT lt.platform_event_id, l.source_channel AS original_channel
FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.phone = '+919876543210' AND l.tenant_id = '<tenant_id>'
ORDER BY lt.created_at;
-- Expect: ≥2 rows — one from 'whatsapp', one from 'file_upload'

-- 19.3: source_channel reflects FIRST channel (whatsapp, not overwritten)
SELECT source_channel FROM leads
WHERE phone = '+919876543210' AND tenant_id = '<tenant_id>';
-- Expect: 'whatsapp'
```

---

### Phase 19A — Facebook OAuth: Instagram Auto-Connection

**Tool:** Postgres MCP  
**Prerequisite:** Phase 11 complete AND the connected Facebook Page has a linked Instagram Business account.

```sql
-- 19A.1: Instagram ChannelConnection auto-created alongside Facebook one
SELECT channel_type, status, expires_at
FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram'
  AND created_at >= (
    SELECT created_at FROM channel_connections
    WHERE tenant_id = '<tenant_id>' AND channel_type = 'facebook'
    ORDER BY created_at DESC LIMIT 1
  );
-- Expect: 1 row, status='active'

-- 19A.2: expires_at IS NULL (non-expiring page token, not 60-day IG Business Login token)
SELECT expires_at FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram' AND expires_at IS NULL;
-- Expect: 1 row

-- 19A.3: ig_account_id in metadata
SELECT metadata->>'ig_account_id' AS ig_id FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram';
-- Expect: non-NULL, non-empty
```

> **Note:** Skip if the connected Facebook Page has no linked Instagram Business account — correct behaviour.

---

## SECTION K — Multi-Tenant Isolation

---

### Phase 20 — Multi-Tenant Webhook Routing Isolation

**Tool:** Playwright (Swagger) + Postgres MCP

**Setup:**
```sql
INSERT INTO channel_connections (id, tenant_id, channel_type, status, metadata, created_at, updated_at)
VALUES (
  gen_random_uuid(),
  gen_random_uuid(),
  'whatsapp',
  'active',
  '{"phone_number_id": "TENANT2_PHONE_999"}',
  now(), now()
)
RETURNING id, tenant_id;
-- Store: <fake_tenant_id>
```

Send webhook targeting Tenant 2's phone_number_id:
```javascript
const payload = {
  "object": "whatsapp_business_account",
  "entry": [{"id": "000000002", "changes": [{"value": {
    "messaging_product": "whatsapp",
    "metadata": {"phone_number_id": "TENANT2_PHONE_999"},
    "messages": [{"from": "912222222222", "id": "wamid.isolation001",
                  "type": "text", "text": {"body": "I want pricing information"}}]
  }, "field": "messages"}]}]
};
```

Wait 3s.

**DB checks:**
```sql
-- 20.1: no lead under Tenant 1's account
SELECT COUNT(*) FROM leads
WHERE tenant_id = '<tenant_id from Phase 4>' AND phone = '+912222222222';
-- Expect: 0

-- 20.2: if lead created, it's under fake_tenant_id only
SELECT tenant_id FROM leads WHERE phone = '+912222222222';
-- Expect: <fake_tenant_id> only
```

**Cleanup:**
```sql
DELETE FROM channel_connections WHERE metadata->>'phone_number_id' = 'TENANT2_PHONE_999';
```

**Fail:** If lead appears under Tenant 1 — routing uses shared lookup without tenant scoping. Critical isolation bug.

---

### Phase 20A — Multi-Tenant File Upload JWT Scoping

**What it tests:** File upload authenticated as Tenant A cannot create leads visible to Tenant B.

**Setup:**
```sql
INSERT INTO tenants (id, company_name, status, onboarding_status, created_at, updated_at)
VALUES (gen_random_uuid(), 'Fake Tenant B', 'ACTIVE', 'COMPLETE', now(), now())
RETURNING id;
-- Store: <tenant_b_id>
```

**Steps:** Upload `csv_01.csv` via Swagger using **Tenant A's JWT** (from Phase 2).

**DB checks:**
```sql
-- 20A.1: no leads under Tenant B
SELECT COUNT(*) FROM leads WHERE tenant_id = '<tenant_b_id>';
-- Expect: 0

-- 20A.2: rows under Tenant A
SELECT COUNT(*) FROM leads WHERE tenant_id = '<tenant_id>'
  AND source_channel = 'file_upload' AND created_at > now() - interval '30 seconds';
-- Expect: > 0
```

**Cleanup:**
```sql
DELETE FROM tenants WHERE id = '<tenant_b_id>';
```

---

## SECTION L — Connection Status API

---

### Phase 21 — GET /connections/{id}/status — Valid Connection

**What it tests:** The connection status endpoint returns the correct shape for an existing connection owned by the current tenant.

**Prerequisite:** Phase 6 complete (ChannelConnection `<wa_connection_id>` exists).

**Steps:** In Swagger, expand `GET /channels/connections/{connection_id}/status` → **Try it out** → enter `<wa_connection_id>` → **Execute**.

**Pass:** HTTP 200, body:
```json
{
  "connection_id": "<wa_connection_id>",
  "channel_type": "whatsapp",
  "status": "active",
  "metadata": {"phone_number_id": "987654321"},
  "expires_at": null
}
```

All fields non-null except `expires_at` (WhatsApp connections never expire).

---

### Phase 21A — GET /connections/{id}/status — Unknown UUID → 404

**Steps:** In Playwright `browser_evaluate`:
```javascript
const res = await fetch(
  "http://localhost:8000/channels/connections/00000000-0000-0000-0000-000000000000/status",
  {headers: {"Authorization": "<bearer_token>"}}
);
return {status: res.status, data: await res.json()};
```

**Pass:** HTTP 404, `{"detail":"connection_not_found"}`.

---

### Phase 21B — GET /connections/{id}/status — Wrong Tenant → 403

**What it tests:** A tenant user cannot read another tenant's connection.

**Setup:**
```sql
-- Insert a connection belonging to a random tenant_id (not our tenant)
INSERT INTO channel_connections (id, tenant_id, channel_type, status, metadata, created_at, updated_at)
VALUES (
  gen_random_uuid(),
  gen_random_uuid(),
  'whatsapp', 'active', '{"phone_number_id": "XTENANT_PHONE"}', now(), now()
)
RETURNING id;
-- Store: <other_tenant_connection_id>
```

**Steps:** Call `GET /channels/connections/<other_tenant_connection_id>/status` using **our JWT** (platform_admin has no tenant_id, so skip to Phase 21C for tenant isolation test).

> **Note:** This phase is most effective if tested with a real tenant-scoped JWT. Platform admin (`tenant_id=NULL`) bypasses the 403 guard. If only platform_admin JWT is available, mark this phase as deferred and document it.

**Pass:** HTTP 403 if called with a tenant-scoped JWT that does not own the connection.

**Cleanup:**
```sql
DELETE FROM channel_connections WHERE metadata->>'phone_number_id' = 'XTENANT_PHONE';
```

---

### Phase 21C — GET /connections/{id}/status — Platform Admin Sees Any Connection

**What it tests:** Platform admin (tenant_id = NULL) can read any tenant's connection.

**Steps:** Call `GET /channels/connections/<wa_connection_id>/status` using the platform admin JWT.

**Pass:** HTTP 200 — not 403. Returns the connection data regardless of which tenant owns it.

---

## SECTION M — Invalid & Malformed Payloads

---

### Phase 22 — Unknown webhook object type → Ignored (Not Crashed)

**What it tests:** A valid HMAC-signed webhook with an object type we don't handle returns HTTP 200 `{"status":"ignored"}` — not a crash.

```javascript
const payload = {
  "object": "some_future_meta_product",
  "entry": [{"id": "111", "changes": [{"value": {"data": "xyz"}, "field": "unknown"}]}]
};
const body = JSON.stringify(payload);
const sig = await hmacSign("8a7ad9d320e984b58ecd57000a7eb799", body);
const res = await fetch("http://localhost:8000/channels/webhook", {
  method: "POST", body,
  headers: {"Content-Type": "application/json", "X-Hub-Signature-256": sig}
});
return {status: res.status, data: await res.json()};
```

**Pass:** HTTP 200, body `{"status":"ignored"}`. No crash. No DB rows created.

---

### Phase 22A — WhatsApp Payload Missing `messages` Key (Status Update / Read Receipt)

**What it tests:** Meta sends read receipt / delivery status webhooks that have `statuses` instead of `messages`. The handler must ACK (HTTP 200) without crashing. No lead should be created.

> **Why this matters:** `normalise_whatsapp_message()` directly accesses `value["messages"][0]`. If the ARQ worker calls it on a status-only payload, it crashes with KeyError. This test validates the guard exists (or finds the bug).

```javascript
const payload = {
  "object": "whatsapp_business_account",
  "entry": [{"id": "123456789", "changes": [{"value": {
    "messaging_product": "whatsapp",
    "metadata": {"display_phone_number": "919876543210", "phone_number_id": "987654321"},
    "statuses": [{
      "id": "wamid.statusupdate001",
      "status": "read",
      "timestamp": "1700001500",
      "recipient_id": "919876543210"
    }]
  }, "field": "messages"}]}]
};
const body = JSON.stringify(payload);
const sig = await hmacSign("8a7ad9d320e984b58ecd57000a7eb799", body);
const res = await fetch("http://localhost:8000/channels/webhook", {
  method: "POST", body,
  headers: {"Content-Type": "application/json", "X-Hub-Signature-256": sig}
});
return {status: res.status, data: await res.json()};
```

Wait 3s.

**Pass:** HTTP 200. No new lead rows. No ARQ worker error log for `wamid.statusupdate001`.

```sql
SELECT COUNT(*) FROM leads WHERE created_at > now() - interval '15 seconds';
-- Expect: 0

SELECT COUNT(*) FROM intake_event_logs WHERE platform_event_id = 'wamid.statusupdate001';
-- Expect: 0
```

**Fail:** If worker crashes with KeyError on `value["messages"][0]` — add a guard in the worker before calling `normalise_whatsapp_message`. The check should be: if `"messages"` not in value → skip/ignore.

---

### Phase 22B — WhatsApp Payload with Malformed Entry (Missing Nested Keys)

**What it tests:** Severely malformed payload (missing `changes` key entirely) must not crash the server — it should return HTTP 200 with `{"status":"ignored"}`.

```javascript
const payload = {
  "object": "whatsapp_business_account",
  "entry": [{"id": "badentry_no_changes"}]
};
const body = JSON.stringify(payload);
const sig = await hmacSign("8a7ad9d320e984b58ecd57000a7eb799", body);
const res = await fetch("http://localhost:8000/channels/webhook", {
  method: "POST", body,
  headers: {"Content-Type": "application/json", "X-Hub-Signature-256": sig}
});
return {status: res.status, data: await res.json()};
```

**Pass:** HTTP 200, body `{"status":"ignored"}`. No 500 error.

---

### Phase 22C — Empty entry Array → Ignored

**What it tests:** Webhook with an empty `entry` array (valid HMAC) does not crash.

```javascript
const payload = {"object": "whatsapp_business_account", "entry": []};
const body = JSON.stringify(payload);
const sig = await hmacSign("8a7ad9d320e984b58ecd57000a7eb799", body);
const res = await fetch("http://localhost:8000/channels/webhook", {
  method: "POST", body,
  headers: {"Content-Type": "application/json", "X-Hub-Signature-256": sig}
});
return {status: res.status, data: await res.json()};
```

**Pass:** HTTP 200, body `{"status":"ignored"}`. No 500.

---

## SECTION N — Data Normalisation

---

### Phase 23 — WhatsApp Phone Stored as E.164

**What it tests:** Meta sends phone numbers as `919876543210` (no leading +). The normaliser prepends `+`. Verify the stored value is `+919876543210` not `919876543210`.

**Prerequisite:** Phase 6 complete (lead with phone `919876543210` in payload).

```sql
SELECT phone FROM leads
WHERE tenant_id = '<tenant_id>' AND source_channel = 'whatsapp'
ORDER BY created_at ASC LIMIT 1;
-- Expect: '+919876543210' (not '919876543210')
```

**Sub-check:**
```sql
-- Dedup works: searching by '+919876543210' finds the WhatsApp lead
SELECT COUNT(*) FROM leads WHERE phone = '+919876543210' AND tenant_id = '<tenant_id>';
-- Expect: 1
```

**Fail:** `normalise_whatsapp_message()` in `normaliser.py` — check the `+` prepend logic.

---

### Phase 23A — CSV Email Stored Lowercase

**What it tests:** CSV row with `Email: Alice@Example.COM` is stored as `alice@example.com`.

**Test file:** Create `tests/fixtures/lead_ingestion/csv_email_case.csv`:
```
full_name,email
Uppercase Tester,UPPERCASE@EXAMPLE.COM
```

**Steps:** Upload via Swagger.

```sql
SELECT email FROM leads
WHERE tenant_id = '<tenant_id>' AND full_name = 'Uppercase Tester';
-- Expect: 'uppercase@example.com' (lowercased)
```

**Fail:** `pipeline.py` run_capture — email lowercasing missing. Check `event.email.lower()`.

---

### Phase 23B — CSV Phone Normalised (Strips Spaces and Dashes)

**What it tests:** Phone `+91 98765-43210` in CSV is stored as `+919876543210` (deduplicator normalises before storing and matching).

**Test file:** Create `tests/fixtures/lead_ingestion/csv_phone_format.csv`:
```
full_name,phone,email
Format Tester,+91 98765-43210,formattester@example.com
```

**Steps:** Upload via Swagger.

```sql
SELECT phone FROM leads
WHERE tenant_id = '<tenant_id>' AND email = 'formattester@example.com';
-- Expect: '+919876543210' (spaces and dashes stripped)
```

**Sub-check:** Confirm this phone deduplicates with a WA message from `919876543210`:
```sql
-- Send a WA message from 919876543210 (same number, different event) and verify it deduplicates
-- against the CSV lead above, resulting in 1 lead + 2 touchpoints.
SELECT COUNT(*) FROM leads WHERE phone = '+919876543210' AND email = 'formattester@example.com';
-- Expect: 1
```

---

## SECTION O — Auth Guards

---

### Phase 24 — GET /me with No Authorization Header → 401

**What it tests:** Protected endpoints reject unauthenticated requests.

```javascript
const res = await fetch("http://localhost:8000/me");
return {status: res.status};
```

**Pass:** HTTP 401. No user lookup attempted.

---

### Phase 24A — GET /me with Malformed JWT → 401

```javascript
const res = await fetch("http://localhost:8000/me", {
  headers: {"Authorization": "Bearer not.a.real.jwt"}
});
return {status: res.status};
```

**Pass:** HTTP 401.

---

### Phase 24B — POST /channels/embedded-signup/callback with No JWT → 401

```javascript
const res = await fetch("http://localhost:8000/channels/embedded-signup/callback", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({"code": "some_code"})
});
return {status: res.status};
```

**Pass:** HTTP 401. No DB rows created.

```sql
SELECT COUNT(*) FROM channel_connections WHERE created_at > now() - interval '10 seconds';
-- Expect: 0
```

---

## SECTION P — Non-Text Message Types

---

### Phase 25 — WhatsApp Audio Message → insufficient_signal (Not a Crash)

**What it tests:** A WhatsApp audio message has no text body. The normaliser sets `raw_text=None`. `run_filter("")` catches it as Stage 1 noise → `insufficient_signal`. No crash.

```javascript
const payload = {
  "object": "whatsapp_business_account",
  "entry": [{"id": "123456789", "changes": [{"value": {
    "messaging_product": "whatsapp",
    "metadata": {"display_phone_number": "919812312312", "phone_number_id": "987654321"},
    "contacts": [{"profile": {"name": "Audio Sender"}, "wa_id": "919812312312"}],
    "messages": [{
      "from": "919812312312",
      "id": "wamid.audio_msg_001",
      "timestamp": "1700002000",
      "type": "audio",
      "audio": {"id": "audio_id_123", "mime_type": "audio/ogg; codecs=opus"}
    }]
  }, "field": "messages"}]}]
};
const body = JSON.stringify(payload);
const sig = await hmacSign("8a7ad9d320e984b58ecd57000a7eb799", body);
const res = await fetch("http://localhost:8000/channels/webhook", {
  method: "POST", body,
  headers: {"Content-Type": "application/json", "X-Hub-Signature-256": sig}
});
return {status: res.status, data: await res.json()};
```

Wait 3s.

**Pass:** HTTP 200. Lead created with `pipeline_stage='insufficient_signal'` (no text → Stage 1 noise). No crash.

```sql
SELECT pipeline_stage FROM leads
WHERE id = (SELECT lead_id FROM intake_event_logs WHERE platform_event_id = 'wamid.audio_msg_001');
-- Expect: 'insufficient_signal'
```

---

### Phase 25A — WhatsApp Image Message → insufficient_signal

Same as Phase 25 but with `"type": "image"` and an `"image"` block instead of `"audio"`.
- `"id": "wamid.image_msg_001"`, `"from": "919833333333"`

**Pass:** HTTP 200. `pipeline_stage='insufficient_signal'`. No crash.

```sql
SELECT pipeline_stage FROM leads
WHERE id = (SELECT lead_id FROM intake_event_logs WHERE platform_event_id = 'wamid.image_msg_001');
-- Expect: 'insufficient_signal'
```

---

## SECTION Q — OAuth Error Flows

---

### Phase 26 — Facebook OAuth Callback with error param (User Denied) → 400

**What it tests:** When the user denies Facebook permissions, Meta redirects to the callback with `?error=access_denied`. The server must return HTTP 400 — not crash.

**Steps:** In Playwright, navigate to:
```
http://localhost:8000/channels/oauth/facebook/callback?error=access_denied&error_code=200&error_description=Permissions+error&error_reason=user_denied&state=anything
```

**Pass:** HTTP 400, body contains `"Facebook OAuth error"`.

```sql
SELECT COUNT(*) FROM channel_connections WHERE created_at > now() - interval '10 seconds';
-- Expect: 0 (no connection created on user-denied)
```

---

### Phase 26A — Instagram OAuth Callback with error param → 400

**Steps:** Navigate to:
```
http://localhost:8000/channels/oauth/instagram/callback?error=access_denied&state=anything
```

**Pass:** HTTP 400. No ChannelConnection created.

---

### Phase 26B — Embedded Signup Callback with Invalid Code → 502

**What it tests:** Posting a syntactically valid but semantically invalid code to the embedded signup endpoint returns HTTP 502 with a descriptive error — not a 500 crash.

```javascript
const res = await fetch("http://localhost:8000/channels/embedded-signup/callback", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": "<bearer_token>"
  },
  body: JSON.stringify({"code": "INVALID_CODE_THAT_META_WILL_REJECT"})
});
return {status: res.status, data: await res.json()};
```

**Pass:** HTTP 502, body contains `"WhatsApp connection failed"`. Not HTTP 500.

```sql
SELECT COUNT(*) FROM channel_connections WHERE created_at > now() - interval '10 seconds';
-- Expect: 0
```

---

## SECTION R — File Upload Edge Cases

---

### Phase 27 — File Exceeds 10 MB → 413

**What it tests:** The 10 MB hard limit is enforced before any parsing.

**Steps:** In Playwright `browser_evaluate`, construct a POST with a body > 10MB:
```javascript
// Generate 11MB of CSV data
const header = "full_name,email\n";
const row = "Test User,test@example.com\n";
const bigBody = header + row.repeat(Math.ceil(11 * 1024 * 1024 / row.length));
const formData = new FormData();
formData.append("file", new Blob([bigBody], {type: "text/csv"}), "big.csv");
const res = await fetch("http://localhost:8000/channels/inbound/file-upload", {
  method: "POST",
  headers: {"Authorization": "<bearer_token>"},
  body: formData
});
return {status: res.status, data: await res.json()};
```

**Pass:** HTTP 413, body `{"detail":"File exceeds 10 MB limit"}`. No DB rows created.

> **Note:** HTTP status is 413 (not 422) — as implemented in `file_upload_handler.py`.

---

### Phase 27A — CSV Exceeds 5000 Rows → 422

**What it tests:** Files with more than 5000 data rows are rejected regardless of file size.

**Steps:** Generate a CSV with 5001 rows and upload via Swagger.

**Pass:** HTTP 422, body contains `"maximum is 5000"`.

```sql
SELECT COUNT(*) FROM leads WHERE created_at > now() - interval '10 seconds';
-- Expect: 0
```

---

### Phase 27B — CSV with Only Header Row (No Data) → 200, Zero Leads

**What it tests:** An empty CSV (header only, no data rows) is accepted gracefully without error.

**Test file:** `tests/fixtures/lead_ingestion/csv_header_only.csv`:
```
full_name,phone,email
```

**Steps:** Upload via Swagger.

**Pass:** HTTP 200, body `{"mode":"sync","lead_ids":[],"row_count":0}`. No DB rows created, no crash.

---

### Phase 27C — CSV with BOM (Byte Order Mark) → Parsed Correctly

**What it tests:** UTF-8 BOM (`\xef\xbb\xbf`) at the start of the file is stripped — the first column header isn't corrupted.

**Test file:** `tests/fixtures/lead_ingestion/csv_bom.csv` — BOM-prefixed UTF-8 CSV with 1 valid row.

**Steps:** Upload via Swagger.

**Pass:** HTTP 200. Lead created with correct `full_name`, `email`, `phone` — not a garbled header key.

```sql
SELECT full_name FROM leads
WHERE tenant_id = '<tenant_id>' AND source_channel = 'file_upload'
ORDER BY created_at DESC LIMIT 1;
-- Expect: correct name string, not garbage
```

---

## SECTION S — Calibration Rule

---

### Phase 28 — Stage 2: Low-Confidence Non-LEAD → Coerced to LEAD

**What it tests:** When Groq returns a non-LEAD classification (e.g., NOISE or EXISTING_CUSTOMER) with `confidence < 0.7`, the calibration rule escalates it to LEAD. A missed lead costs more than a false positive.

> **Important:** This test depends on Groq's actual response. Use a genuinely ambiguous message and verify the pipeline_stage is `'captured'` (not `'insufficient_signal'`).

**Payload:**
- `"id": "wamid.calibration_001"`, `"from": "919855551234"`
- `"body": "Hey, I was using your product a while back. Wanted to check if there's anything new worth trying for my business."`

This message is ambiguous — it could be an existing customer or a re-engaged lead. At confidence < 0.7 the calibration rule forces LEAD.

Wait 5s.

```sql
SELECT pipeline_stage FROM leads
WHERE id = (SELECT lead_id FROM intake_event_logs
            WHERE platform_event_id = 'wamid.calibration_001');
-- Expect: 'captured' (not 'existing_customer' — calibration overrode low-confidence classification)
```

**Fail:** Check `two_stage_filter.py` — the `confidence < 0.7` branch must coerce to LEAD, not pass through the original classification. If the Groq response is high-confidence (≥ 0.7), this test may not be exercisable in this run; document the confidence value returned.

---

## SECTION T — Hub Challenge Edge Cases

---

### Phase 29 — Hub Challenge with Missing hub.challenge → 403

**What it tests:** All three hub params are required. Missing `hub.challenge` must fail.

**Steps:** Navigate to:
```
http://localhost:8000/channels/webhook?hub.mode=subscribe&hub.verify_token=local_verify_token_sprint3
```
(No `hub.challenge` param)

**Pass:** HTTP 403. Response body is NOT the challenge.

---

### Phase 29A — Hub Challenge with hub.mode != "subscribe" → 403

**What it tests:** Only `hub.mode=subscribe` is accepted.

**Steps:** Navigate to:
```
http://localhost:8000/channels/webhook?hub.mode=unsubscribe&hub.verify_token=local_verify_token_sprint3&hub.challenge=abc
```

**Pass:** HTTP 403. Challenge NOT echoed.

---

## Pass/Fail Summary Table

| Phase | Section | Description | Pass Condition |
|-------|---------|-------------|----------------|
| 1 | A | Health check | HTTP 200 `{"status":"ok"}` |
| 2 | A | Auth0 login + JWT capture | Swagger Authorized, token captured |
| 3 | A | GET /me | role=PLATFORM_ADMIN, email exact match, timestamps set |
| 4 | A | Onboarding | status=COMPLETE, 1 active config, has weights+thresholds |
| 5 | A | Hub challenge | Challenge echoed; wrong token → 403 |
| 6 | B | WA DM golden path | Captured, touchpoint, channel_connection_id set |
| 7 | B | Stage 1 noise - emoji | insufficient_signal, no touchpoint, captured count same |
| 7A | B | Stage 1 noise - single word ("Hi") | insufficient_signal, captured count same |
| 7B | B | Stage 2 noise - existing customer | existing_customer, no touchpoint |
| 8 | B | WA DM idempotency | intake_event_logs count=1, touchpoints count=1 |
| 9 | B | WA DM dedup | leads count=1, touchpoint added, status=duplicate |
| 9A | B | WA DM pre-flight block | pre_flight_blocked, block reason set |
| 10 | C | CSV upload golden path | 2 captured + 1 blocked; Alice deduped; Bob phone=NULL |
| 10A | C | XLSX upload format parity | Same outcomes as Phase 10 |
| 10B | C | Wrong file type (.docx) | HTTP 422, no DB rows |
| 10C | C | Unauthenticated upload | HTTP 401, no DB rows |
| 10D | C | Upload with no active config | HTTP 400 no_active_config |
| 10E | C | Large file async mode (150 rows) | HTTP 200 `{"mode":"async","row_count":150}`, rows processed |
| 11 | D | Facebook OAuth connect | ChannelConnection active, expires_at=NULL, creds encrypted |
| 11A | D | Facebook OAuth tampered state | HTTP 400/403, no ChannelConnection |
| 12 | D | Facebook DM golden path | Lead captured, touchpoint, phone=NULL |
| 12A | D | Facebook DM noise | insufficient_signal, no touchpoint |
| 12B | D | Facebook DM idempotency | intake_event_logs count=1, touchpoints count=1 |
| 12C | D | Facebook DM dedup | leads count=1, status=duplicate, touchpoint added |
| 13 | E | Instagram OAuth connect | ChannelConnection active, expires_at ~60 days, username set |
| 13A | E | Instagram OAuth tampered state | HTTP 400/403, no ChannelConnection |
| 14 | E | Instagram DM golden path | Lead captured, touchpoint, phone=NULL |
| 14A | E | Instagram DM noise | insufficient_signal, no touchpoint |
| 14B | E | Instagram DM idempotency | intake_event_logs count=1, touchpoints count=1 |
| 15A | F | Lead Ads - verify ChannelConnection | status=active, page_id in metadata |
| 15B | F | Lead Ads golden path | Captured, source=FACEBOOK_LEAD_ADS, no filter, email set |
| 15C | F | Lead Ads idempotency | intake_event_logs count=1, no duplicate lead |
| 16 | G | WA Embedded Signup | **BLOCKED** — requires real WA Business number (HITL) |
| 17 | H | HMAC rejection - bad signature | HTTP 403, no intake_event_log |
| 17A | H | HMAC rejection - missing header | HTTP 403, no intake_event_log |
| 17B | H | HMAC rejection - Facebook | HTTP 403, no intake_event_log |
| 17C | H | HMAC rejection - Instagram | HTTP 403, no intake_event_log |
| 18 | I | Unroutable - unknown WA phone_number_id | HTTP 200 ACK, no lead, status=unroutable |
| 18A | I | Unroutable - unknown Facebook page_id | HTTP 200 ACK, no lead, status=unroutable |
| 18B | I | Unroutable - unknown Instagram ig_account_id | HTTP 200 ACK, no lead, status=unroutable |
| 19 | J | Unified lead registry | 1 lead for +919876543210, ≥2 touchpoints cross-channel |
| 19A | J | Facebook OAuth → Instagram auto-connect | Instagram ChannelConnection auto-created, expires_at=NULL |
| 20 | K | Multi-tenant webhook isolation | Lead under correct tenant only |
| 20A | K | Multi-tenant file upload isolation | All rows under JWT's tenant, Tenant B count=0 |
| 21 | L | Connection status - valid | HTTP 200, correct shape |
| 21A | L | Connection status - unknown UUID | HTTP 404 |
| 21B | L | Connection status - wrong tenant | HTTP 403 |
| 21C | L | Connection status - platform admin bypass | HTTP 200 for any tenant's connection |
| 22 | M | Unknown webhook object type | HTTP 200 `{"status":"ignored"}`, no crash |
| 22A | M | WA status update (read receipt, no messages key) | HTTP 200, no lead, no ARQ crash |
| 22B | M | WA payload missing changes key | HTTP 200 `{"status":"ignored"}`, no 500 |
| 22C | M | Empty entry array | HTTP 200 `{"status":"ignored"}`, no 500 |
| 23 | N | WA phone stored as E.164 | DB phone = '+919876543210' (not '919876543210') |
| 23A | N | CSV email stored lowercase | DB email = 'uppercase@example.com' |
| 23B | N | CSV phone normalised (spaces/dashes stripped) | DB phone = '+919876543210' |
| 24 | O | GET /me no auth header | HTTP 401 |
| 24A | O | GET /me malformed JWT | HTTP 401 |
| 24B | O | Embedded signup no JWT | HTTP 401, no DB rows |
| 25 | P | WA audio message → insufficient_signal | pipeline_stage='insufficient_signal', no crash |
| 25A | P | WA image message → insufficient_signal | pipeline_stage='insufficient_signal', no crash |
| 26 | Q | Facebook OAuth user denied (error param) | HTTP 400, no ChannelConnection |
| 26A | Q | Instagram OAuth user denied (error param) | HTTP 400, no ChannelConnection |
| 26B | Q | Embedded signup invalid code | HTTP 502, no ChannelConnection |
| 27 | R | File > 10MB | HTTP 413, no DB rows |
| 27A | R | CSV > 5000 rows | HTTP 422, no DB rows |
| 27B | R | CSV header-only (no data rows) | HTTP 200, row_count=0, no crash |
| 27C | R | CSV with BOM | HTTP 200, name parsed correctly |
| 28 | S | Calibration: low-confidence → LEAD | pipeline_stage='captured', not 'existing_customer' |
| 29 | T | Hub challenge - missing hub.challenge | HTTP 403 |
| 29A | T | Hub challenge - wrong hub.mode | HTTP 403 |

**Total: 71 phases (Phase 16 BLOCKED)**

---

## Prerequisites Before Running Phases 11–14

These Meta App setup steps must be completed first:
1. Add **Messenger** product to Meta App (`1494580908551596`)
2. Add **Instagram** product to Meta App
3. Register Facebook OAuth redirect URI: `https://lorna-nonutilized-macy.ngrok-free.dev/channels/oauth/facebook/callback`
4. Register Instagram OAuth redirect URI: `https://lorna-nonutilized-macy.ngrok-free.dev/channels/oauth/instagram/callback`

---

## Fix Protocol

When a phase fails:
1. Read the Playwright response body and server logs
2. Identify root cause: config mismatch, code bug, or missing setup step
3. Fix at the root (update `.env` → restart server; code bug → edit + restart)
4. Re-run the failed phase from its beginning
5. Only advance to the next phase once all DB checks pass



