# E2E Test Map — Lead Ingestion Engine (Sprints 1–4, 20 Phases)

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
| 5 | ngrok tunnel | `ngrok http 8000` |
| 6 | Dev tools server (Phase 15 only) | `python -m http.server 8001 --directory dev_tools` |

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

## Phase 1 — Health Check

**Tool:** Playwright  
**Action:** Navigate to `http://localhost:8000/health`  
**Pass:** HTTP 200, body `{"status":"ok"}`  
**Fail:** Server not started — check Terminal 3 logs

---

## Phase 2 — Auth0 Login + JWT Capture

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

## Phase 3 — GET /me + User Row in DB

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

**Fail:** Auth0 Action not wired. Check Triggers → post-login → `https://leadengine/role` claim. Check `app_metadata.role = "PLATFORM_ADMIN"` on the Auth0 user.

---

## Phase 4 — Tenant Onboarding

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
-- Tenant row
SELECT id, company_name, onboarding_status, status FROM tenants ORDER BY created_at DESC LIMIT 1;
-- Expect: onboarding_status='COMPLETE', status='ACTIVE'

-- Active config
SELECT id, version, status, jsonb_array_length(signals) AS signal_count
FROM tenant_configs WHERE status = 'ACTIVE' ORDER BY created_at DESC LIMIT 1;
-- Expect: status='ACTIVE', signal_count > 0
```

**Sub-checks:**
```sql
-- 4.1: website_url stored correctly on the tenant row
SELECT website_url FROM tenants WHERE id = '<tenant_id>';
-- Expect: 'https://stripe.com'

-- 4.2: exactly ONE active config per tenant (no duplicates)
SELECT COUNT(*) FROM tenant_configs
WHERE tenant_id = '<tenant_id>' AND status = 'ACTIVE';
-- Expect: 1

-- 4.3: config has actual weights and thresholds (pipeline built complete data)
SELECT
  jsonb_array_length(signals) AS signals,
  (scoring_weights IS NOT NULL) AS has_weights,
  (scoring_thresholds IS NOT NULL) AS has_thresholds
FROM tenant_configs WHERE tenant_id = '<tenant_id>' AND status = 'ACTIVE';
-- Expect: signals > 0, has_weights=true, has_thresholds=true

-- 4.4: status lifecycle — no stuck RUNNING or FAILED rows
SELECT onboarding_status FROM tenants WHERE id = '<tenant_id>';
-- Expect: 'COMPLETE' (not RUNNING or FAILED)
```

**Fail:** Check ARQ worker logs. `FAILED` → check Groq API key and that `https://stripe.com` is reachable via httpx.

---

## Phase 5 — Meta Webhook Hub Challenge

**Tool:** Playwright  
**Action:** Navigate to:
```
http://localhost:8000/channels/webhook?hub.mode=subscribe&hub.verify_token=local_verify_token_sprint3&hub.challenge=test_challenge_abc123
```
**Pass:** Response body = `test_challenge_abc123`, HTTP 200  
**Fail:** Check `META_WEBHOOK_VERIFY_TOKEN=local_verify_token_sprint3` in `.env`

**Sub-check:**

**5.1 — Wrong verify token must be rejected:**
```
http://localhost:8000/channels/webhook?hub.mode=subscribe&hub.verify_token=WRONG_TOKEN&hub.challenge=test_challenge_abc123
```
- **Pass:** HTTP 403 or 400 — challenge is NOT echoed back
- **Fail:** If the challenge is echoed back with a wrong token, any attacker can subscribe to the webhook

---

## Phase 6 — WhatsApp DM Golden Path

**Tool:** Playwright (JS fetch) + Postgres MCP

**Setup — insert ChannelConnection via Postgres MCP:**
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
-- Store this id as <wa_connection_id>
```

**Payload** (`tests/fixtures/lead_ingestion/wa_dm_01.json`):
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
-- 6.1: lead_touchpoints row created and linked to the lead
SELECT lt.id, lt.platform_event_id
FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.phone = '+919876543210' AND l.tenant_id = '<tenant_id>';
-- Expect: 1 row with platform_event_id='wamid.sprint3golden001'

-- 6.2: lead's channel_connection_id points to the ChannelConnection we inserted
SELECT channel_connection_id FROM leads
WHERE phone = '+919876543210' AND tenant_id = '<tenant_id>';
-- Expect: <wa_connection_id> from setup

-- 6.3: intake_event_log not stuck in processing
SELECT status FROM intake_event_logs WHERE platform_event_id = 'wamid.sprint3golden001';
-- Expect: 'received' (not 'processing' or 'failed')
```

**Fail:** Check Groq API key (Stage 2 LLM filter). Check ChannelConnection INSERT ran. Check ARQ worker logs.

---

## Phase 7 — Noise Filter (Emoji Short-Circuit)

**Tool:** Playwright (JS fetch) + Postgres MCP

**Payload** (`tests/fixtures/lead_ingestion/wa_noise_01.json`):
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
-- 7.1: no lead_touchpoints created for noise (noise does not get a touchpoint)
SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.phone = '+919999999999';
-- Expect: 0

-- 7.2: Groq was NOT called (Stage 1 short-circuit) — verify via intake_event_log metadata
-- If the log has a groq_called field or similar, check it is false/absent
-- Otherwise confirm total leads count did not increase by checking:
SELECT COUNT(*) FROM leads WHERE tenant_id = '<tenant_id>' AND pipeline_stage = 'captured';
-- Expect: same count as after Phase 6 (1) — noise did not add a captured lead
```

**Fail:** Stage 1 rules should catch emoji without calling Groq. Check `two_stage_filter.py` Stage 1 logic.

---

## Phase 8 — Idempotency (Re-delivery)

**Tool:** Playwright (JS fetch) + Postgres MCP  
**Steps:** Resend the exact same `wa_dm_01.json` payload (same `platform_event_id = "wamid.sprint3golden001"`)

**DB checks:**
```sql
SELECT COUNT(*) FROM intake_event_logs WHERE platform_event_id = 'wamid.sprint3golden001';
-- Expect: 1 (ON CONFLICT DO NOTHING, no second row)

SELECT COUNT(*) FROM leads WHERE tenant_id = '<tenant_id>' AND phone = '+919876543210';
-- Expect: 1 (no duplicate lead)
```

**Sub-checks:**
```sql
-- 8.1: touchpoints count unchanged after resend (no duplicate touchpoint)
SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.phone = '+919876543210' AND l.tenant_id = '<tenant_id>';
-- Expect: still 1 (same as after Phase 6)

-- 8.2: intake_event_log status unchanged — resend did not mutate the original row
SELECT status FROM intake_event_logs WHERE platform_event_id = 'wamid.sprint3golden001';
-- Expect: still 'received'
```

**Fail:** Check `intake_event_logs` has `UNIQUE` constraint on `platform_event_id` and ON CONFLICT handling.

---

## Phase 9 — Deduplication (New Event, Same Identity)

**Tool:** Playwright (JS fetch) + Postgres MCP  
**Steps:** Send `wa_dm_01.json` but change `platform_event_id` to `"wamid.dedup_test_001"` (same phone `919876543210`)

**DB checks:**
```sql
SELECT COUNT(*) FROM leads WHERE phone = '+919876543210' AND tenant_id = '<tenant_id>';
-- Expect: 1 (no new lead created)

SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id WHERE l.phone = '+919876543210';
-- Expect: 1 (touchpoint added to existing lead)

SELECT status FROM intake_event_logs WHERE platform_event_id = 'wamid.dedup_test_001';
-- Expect: status='duplicate'
```

**Sub-checks:**
```sql
-- 9.1: the new touchpoint carries the correct platform_event_id
SELECT lt.platform_event_id FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.phone = '+919876543210' AND lt.platform_event_id = 'wamid.dedup_test_001';
-- Expect: 1 row

-- 9.2: touchpoint has raw_event_json populated (the original message is preserved)
SELECT raw_event_json IS NOT NULL AS has_raw FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.phone = '+919876543210' AND lt.platform_event_id = 'wamid.dedup_test_001';
-- Expect: has_raw = true
```

**Fail:** Check deduplicator phone normalization (must strip `91` prefix → `+919876543210` E.164).

---

## Phase 10 — File Upload (CSV)

**Tool:** Playwright (Swagger file upload) + Postgres MCP

**Test file:** `tests/fixtures/lead_ingestion/csv_01.csv`
- Row 1: Alice Sharma, phone=`+919876543210`, email=`alice@example.com`, Budget Range, Notes
- Row 2: Bob Kumar, email=`bob@example.com`, no phone
- Row 3: no name, no phone, no email

**Steps:**
1. In Swagger, expand `POST /channels/inbound/file-upload`
2. **Try it out** → choose file → **Execute** (JWT active from Phase 2)

**DB checks:**
```sql
SELECT full_name, phone, email, pipeline_stage, extra_fields
FROM leads WHERE tenant_id = '<tenant_id>' AND source_channel = 'file_upload'
ORDER BY created_at DESC LIMIT 5;
```
**Expect:**
| full_name | pipeline_stage | reason |
|-----------|---------------|--------|
| Alice Sharma | captured | — |
| Bob Kumar | captured | phone is optional |
| (anonymous) | pre_flight_blocked | insufficient_identity_fields |

**Sub-checks:**
```sql
-- 10.1: Alice (phone +919876543210) is deduped against Phase 6's Priya — same lead record
SELECT COUNT(*) FROM leads
WHERE phone = '+919876543210' AND tenant_id = '<tenant_id>';
-- Expect: 1 (not 2 — unified lead registry, one person one record)

-- 10.2: that single lead now has ≥2 touchpoints (WhatsApp DM + file_upload)
SELECT COUNT(*) FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.phone = '+919876543210' AND l.tenant_id = '<tenant_id>';
-- Expect: ≥ 2

-- 10.3: Bob has email but phone IS NULL
SELECT phone, email FROM leads
WHERE email = 'bob@example.com' AND tenant_id = '<tenant_id>';
-- Expect: phone=NULL, email='bob@example.com'

-- 10.4: Row 3 (no identity) has correct block reason
SELECT pipeline_stage, pre_flight_block_reason FROM leads
WHERE tenant_id = '<tenant_id>' AND pipeline_stage = 'pre_flight_blocked'
ORDER BY created_at DESC LIMIT 1;
-- Expect: pre_flight_block_reason='insufficient_identity_fields'

-- 10.5: intake_event_logs has entries for all 3 rows (batch logging)
SELECT COUNT(*) FROM intake_event_logs
WHERE tenant_id = '<tenant_id>' AND source_channel = 'file_upload'
ORDER BY created_at DESC LIMIT 1;
-- Expect: ≥ 3 entries from this batch
```

**Fail:** Check `file_upload_handler.py` field mapping and `pre_flight_check` identity validation.

---

## Phase 11 — Facebook OAuth Connect

**Tool:** Playwright

**Steps:**
1. In Swagger, expand `GET /channels/oauth/facebook` → **Try it out** → **Execute**
2. Grab the redirect URL from the response → navigate to it in browser
3. Facebook login → approve permissions
4. Callback lands at `/channels/oauth/facebook/callback` → response: `{"status":"connected","page_count":N}`

**DB check:**
```sql
SELECT id, channel_type, status, metadata FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'facebook';
-- Expect: ≥1 row, status='active', metadata contains page_id
```

**Sub-checks:**
```sql
-- 11.1: expires_at IS NULL — Facebook page tokens never expire
SELECT expires_at FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'facebook';
-- Expect: NULL

-- 11.2: credentials_encrypted is NOT NULL — token was encrypted and stored
SELECT credentials_encrypted IS NOT NULL AS has_creds FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'facebook';
-- Expect: true

-- 11.3: metadata has both page_id AND page_name
SELECT metadata->>'page_id' AS page_id, metadata->>'page_name' AS page_name
FROM channel_connections WHERE tenant_id = '<tenant_id>' AND channel_type = 'facebook';
-- Expect: both non-NULL and non-empty
```

**Fail:** Check Meta App redirect URI `https://lorna-nonutilized-macy.ngrok-free.dev/channels/oauth/facebook/callback` is registered. Check app is in Development mode.

---

## Phase 12 — Facebook DM Golden Path

**Tool:** Playwright (JS fetch) + Postgres MCP

**Prerequisite:** Phase 11 complete — ChannelConnection with `channel_type='facebook'` in DB.

**Get page_id from DB:**
```sql
SELECT metadata->>'page_id' AS page_id FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'facebook' LIMIT 1;
```

**Payload** (substitute `<page_id>` from query above):
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

**Send webhook** (Playwright `browser_evaluate`):
```javascript
const payload = { /* payload above, substitute page_id */ };
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
SELECT id, pipeline_stage, source_channel, full_name
FROM leads WHERE tenant_id = '<tenant_id>' AND source_channel = 'facebook'
ORDER BY created_at DESC LIMIT 1;
-- Expect: pipeline_stage='captured', source_channel='facebook'

SELECT platform_event_id, status FROM intake_event_logs
WHERE platform_event_id = 'm_sprint4_fb_dm_001';
-- Expect: 1 row, status='received'
```

**Sub-checks:**
```sql
-- 12.1: lead_touchpoints row created for this event
SELECT lt.platform_event_id FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE lt.platform_event_id = 'm_sprint4_fb_dm_001';
-- Expect: 1 row

-- 12.2: lead has no phone and no email — Facebook PSIDs are not real contact info
SELECT phone, email FROM leads
WHERE tenant_id = '<tenant_id>' AND source_channel = 'facebook'
ORDER BY created_at DESC LIMIT 1;
-- Expect: phone=NULL, email=NULL (sender is identified by PSID only)
```

**Fail:** Check ChannelConnection exists and `page_id` in metadata matches `entry.id` in payload. Check ARQ worker logs. Check `normalise_facebook_dm()` in `normaliser.py`.

---

## Phase 13 — Instagram OAuth Connect

**Tool:** Playwright  
**Steps:** Same as Phase 11 but endpoint is `GET /channels/oauth/instagram`

1. In Swagger, expand `GET /channels/oauth/instagram` → **Try it out** → **Execute**
2. Grab the redirect URL from the response → navigate to it in browser
3. Facebook/Instagram login → approve permissions
4. Callback lands at `/channels/oauth/instagram/callback` → response: `{"status":"connected"}`

**DB check:**
```sql
SELECT id, channel_type, status, metadata, expires_at FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram';
-- Expect: 1 row, status='active', expires_at ~60 days from now, metadata contains ig_account_id
```

**Sub-checks:**
```sql
-- 13.1: expires_at is roughly 60 days from now (Instagram Business Login token)
SELECT
  expires_at,
  expires_at > now() AS not_expired,
  expires_at < now() + interval '61 days' AS within_60_days
FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram';
-- Expect: not_expired=true, within_60_days=true

-- 13.2: credentials_encrypted is NOT NULL
SELECT credentials_encrypted IS NOT NULL AS has_creds FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram';
-- Expect: true

-- 13.3: metadata has ig_account_id AND username
SELECT metadata->>'ig_account_id' AS ig_id, metadata->>'username' AS uname
FROM channel_connections WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram';
-- Expect: both non-NULL and non-empty
```

**Fail:** Check Instagram product added to Meta App. Redirect URI `https://lorna-nonutilized-macy.ngrok-free.dev/channels/oauth/instagram/callback` registered. Check `instagram_business_basic` and `instagram_manage_messages` permissions approved.

---

## Phase 14 — Instagram DM Golden Path

**Tool:** Playwright (JS fetch) + Postgres MCP

**Prerequisite:** Phase 13 complete — ChannelConnection with `channel_type='instagram'` in DB.

**Get ig_account_id from DB:**
```sql
SELECT metadata->>'ig_account_id' AS ig_account_id FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram' LIMIT 1;
```

**Payload** (substitute `<ig_account_id>` from query above):
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

**Send webhook** (Playwright `browser_evaluate`):
```javascript
const payload = { /* payload above, substitute ig_account_id */ };
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
SELECT id, pipeline_stage, source_channel
FROM leads WHERE tenant_id = '<tenant_id>' AND source_channel = 'instagram'
ORDER BY created_at DESC LIMIT 1;
-- Expect: pipeline_stage='captured', source_channel='instagram'

SELECT platform_event_id, status FROM intake_event_logs
WHERE platform_event_id = 'm_sprint4_ig_dm_001';
-- Expect: 1 row, status='received'
```

**Sub-checks:**
```sql
-- 14.1: lead_touchpoints row created for this event
SELECT lt.platform_event_id FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE lt.platform_event_id = 'm_sprint4_ig_dm_001';
-- Expect: 1 row

-- 14.2: lead has no phone — Instagram sender ID (IGSID) is not a real phone number
SELECT phone FROM leads
WHERE tenant_id = '<tenant_id>' AND source_channel = 'instagram'
ORDER BY created_at DESC LIMIT 1;
-- Expect: NULL
```

**Fail:** Check ChannelConnection exists and `ig_account_id` in metadata matches `entry.id` in payload. Check ARQ worker logs. Check `normalise_instagram_dm()` in `normaliser.py`.

---

## Phase 15 — WhatsApp Embedded Signup

**Tool:** Playwright (dev test page)

**Prerequisites:** Terminal 6 running (`python -m http.server 8001 --directory dev_tools`)

**Steps:**
1. Navigate to `http://localhost:8001/embedded_signup_test.html`
2. Paste JWT (from Phase 2) into the textarea
3. Click **Connect WhatsApp Business Account** → Meta popup opens
4. Log into Facebook → complete WhatsApp Business number connection
5. Code received → auto-POSTs to `http://localhost:8000/channels/embedded-signup/callback`
6. Page shows backend response

**DB check:**
```sql
SELECT id, channel_type, status, metadata FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'whatsapp'
ORDER BY created_at DESC LIMIT 5;
-- Expect: row(s) with channel_type='whatsapp', metadata contains phone_number_id
```

**Sub-checks:**
```sql
-- 15.1: expires_at IS NULL — WABA tokens managed by Meta, no expiry tracked here
SELECT expires_at FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'whatsapp';
-- Expect: NULL

-- 15.2: credentials_encrypted is NOT NULL
SELECT credentials_encrypted IS NOT NULL AS has_creds FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'whatsapp';
-- Expect: true

-- 15.3: metadata has phone_number_id (required for routing inbound messages)
SELECT metadata->>'phone_number_id' AS phone_number_id FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'whatsapp';
-- Expect: non-NULL, non-empty string
```

**Fail:** Requires Facebook account with WhatsApp Business number registered. Check `META_EMBEDDED_SIGNUP_CONFIG_ID=1311452217634130` in `.env`.

> **Note:** Delete `dev_tools/embedded_signup_test.html` after testing completes.

---

## Phase 16 — HMAC Rejection (Security Gate)

**Tool:** Playwright (JS fetch)

**Send a valid-looking payload with a deliberately wrong signature:**
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

**DB check (no processing should have occurred):**
```sql
SELECT COUNT(*) FROM intake_event_logs WHERE platform_event_id = 'wamid.hmactest001';
-- Expect: 0
```

**Sub-check:**

**16.1 — Missing signature header must also be rejected:**
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
  // No X-Hub-Signature-256 at all
});
return {status: res.status, data: await res.json()};
```
- **Pass:** HTTP 403, no `intake_event_logs` row for `wamid.hmactest002`

**Fail:** If you get HTTP 200 — `validate_signature()` in `webhook_receiver.py` is broken. Must use `hmac.compare_digest`, never `==`. Fix before any production webhook traffic.

---

## Phase 17 — Unified Lead Registry (Cross-Channel Identity Merge)

**Tool:** Postgres MCP  
**Prerequisite:** Phases 6 and 10 complete.

This phase does not send any new webhook. It verifies that the deduplication across two different channels (WhatsApp DM in Phase 6, CSV file upload in Phase 10) produced a single unified lead record for the same phone number.

```sql
-- 17.1: exactly ONE lead exists for this phone across all channels
SELECT COUNT(*) FROM leads
WHERE phone = '+919876543210' AND tenant_id = '<tenant_id>';
-- Expect: 1

-- 17.2: that single lead has touchpoints from both channels
SELECT lt.platform_event_id, l.source_channel AS original_channel
FROM lead_touchpoints lt
JOIN leads l ON lt.lead_id = l.id
WHERE l.phone = '+919876543210' AND l.tenant_id = '<tenant_id>'
ORDER BY lt.created_at;
-- Expect: ≥2 rows — at minimum one from 'whatsapp' (wamid.sprint3golden001)
--         and one from 'file_upload'

-- 17.3: the lead's source_channel reflects the FIRST channel (whatsapp, not overwritten)
SELECT source_channel FROM leads
WHERE phone = '+919876543210' AND tenant_id = '<tenant_id>';
-- Expect: 'whatsapp' (the channel that created the lead, not the later file_upload)
```

**Fail:** Dedup logic not normalizing phone to E.164 before comparison, or file upload creating a new lead instead of adding a touchpoint.

---

## Phase 18 — Facebook OAuth: Instagram Auto-Connection

**Tool:** Postgres MCP  
**Prerequisite:** Phase 11 complete AND the connected Facebook Page has a linked Instagram Business account.

When a tenant connects via Facebook OAuth, the system automatically creates a non-expiring Instagram ChannelConnection using the same page token. This phase verifies that auto-creation.

```sql
-- 18.1: Instagram ChannelConnection was auto-created alongside the Facebook one
SELECT channel_type, status, expires_at
FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram'
  AND created_at >= (
    SELECT created_at FROM channel_connections
    WHERE tenant_id = '<tenant_id>' AND channel_type = 'facebook'
    ORDER BY created_at DESC LIMIT 1
  );
-- Expect: 1 row, channel_type='instagram', status='active'

-- 18.2: Instagram connection expires_at IS NULL (non-expiring page token)
SELECT expires_at FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram'
  AND expires_at IS NULL;
-- Expect: 1 row with NULL expires_at

-- 18.3: Instagram connection metadata has ig_account_id
SELECT metadata->>'ig_account_id' AS ig_id FROM channel_connections
WHERE tenant_id = '<tenant_id>' AND channel_type = 'instagram';
-- Expect: non-NULL, non-empty
```

> **Note:** Skip this phase if the connected Facebook Page has no linked Instagram Business account. In that case, only the Facebook ChannelConnection will exist — which is the correct behaviour.

**Fail:** `_fetch_connected_ig_account` returned None (page has no Instagram) or the auto-creation code path in `exchange_facebook_code` did not execute. Check the Facebook OAuth callback logs.

---

## Phase 19 — Unroutable Webhook (No Matching ChannelConnection)

**Tool:** Playwright (JS fetch) + Postgres MCP

Send a webhook with a `phone_number_id` that has NO ChannelConnection in the DB. The system must not crash, must not create a lead, and must record the unroutable event.

```javascript
const payload = {
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "000000000",
    "changes": [{
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"phone_number_id": "DOES_NOT_EXIST_999"},
        "messages": [{
          "from": "911111111111",
          "id": "wamid.unroutable001",
          "type": "text",
          "text": {"body": "I want to buy your product"}
        }]
      },
      "field": "messages"
    }]
  }]
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

**Pass:** HTTP 200 (webhook always ACKs to Meta — never return non-2xx to Meta or it will retry infinitely)

```sql
-- 19.1: no lead created for the unroutable event
SELECT COUNT(*) FROM leads WHERE tenant_id IS NOT NULL
  AND created_at > now() - interval '30 seconds';
-- Expect: 0 new leads

-- 19.2: intake_event_log recorded with unroutable status
SELECT status FROM intake_event_logs WHERE platform_event_id = 'wamid.unroutable001';
-- Expect: 1 row, status = 'unroutable' (or equivalent — check the actual status string in code)
```

**Fail:** If HTTP 500 returned — webhook receiver must never crash on unknown channel. If a lead was created — routing must require a matched ChannelConnection before processing.

---

## Phase 20 — Multi-Tenant Isolation

**Tool:** Playwright (Swagger) + Postgres MCP

Verify that a webhook for Tenant A cannot create a lead in Tenant B's account.

**Setup:**
```sql
-- Insert a second ChannelConnection for a DIFFERENT (non-existent) tenant_id
-- using the same phone_number_id as Phase 6 to simulate a routing collision attempt
INSERT INTO channel_connections (id, tenant_id, channel_type, status, metadata, created_at, updated_at)
VALUES (
  gen_random_uuid(),
  gen_random_uuid(),  -- fake tenant_id that doesn't exist in tenants table
  'whatsapp',
  'active',
  '{"phone_number_id": "TENANT2_PHONE_999"}',
  now(), now()
)
RETURNING id, tenant_id;
-- Store: <fake_tenant_id>
```

Send a webhook targeting Tenant 2's `phone_number_id`:
```javascript
const payload = {
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "000000002",
    "changes": [{
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"phone_number_id": "TENANT2_PHONE_999"},
        "messages": [{
          "from": "912222222222",
          "id": "wamid.isolation001",
          "type": "text",
          "text": {"body": "I want pricing information for your services"}
        }]
      },
      "field": "messages"
    }]
  }]
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

```sql
-- 20.1: no lead created under Tenant 1's account
SELECT COUNT(*) FROM leads
WHERE tenant_id = '<tenant_id from Phase 4>' AND phone = '+912222222222';
-- Expect: 0

-- 20.2: if lead was created, it is under the fake_tenant_id only
SELECT tenant_id FROM leads WHERE phone = '+912222222222';
-- Expect: <fake_tenant_id> only (never '<real_tenant_id>')
```

**Cleanup:**
```sql
DELETE FROM channel_connections WHERE metadata->>'phone_number_id' = 'TENANT2_PHONE_999';
```

**Fail:** If a lead appears under Tenant 1 — the routing is using a shared lookup without tenant scoping. Critical isolation bug.

---

## Pass/Fail Summary

| Phase | Description | Pass Condition |
|-------|-------------|----------------|
| 1 | Health check | HTTP 200, `{"status":"ok"}` |
| 2 | Auth0 login | Swagger "Authorized", JWT captured |
| 3 | GET /me | `role='PLATFORM_ADMIN'`, user row in DB, email exact match |
| 4 | Onboarding | `onboarding_status='COMPLETE'`, 1 active config, has weights+thresholds |
| 5 | Hub challenge | Challenge echoed; wrong token → rejected |
| 6 | WhatsApp DM golden path | Lead captured, touchpoint created, channel_connection_id set |
| 7 | Noise filter | `insufficient_signal`, no touchpoint, captured count unchanged |
| 8 | Idempotency | intake_event_logs count=1, touchpoints count=1 |
| 9 | Deduplication | leads count=1, touchpoint added with raw_event_json |
| 10 | File upload | 2 captured + 1 blocked; Alice deduped against Phase 6; Bob phone=NULL |
| 11 | Facebook OAuth | ChannelConnection active, expires_at=NULL, credentials encrypted |
| 12 | Facebook DM golden path | Lead captured, touchpoint created, phone=NULL |
| 13 | Instagram OAuth | ChannelConnection active, expires_at ~60 days, username in metadata |
| 14 | Instagram DM golden path | Lead captured, touchpoint created, phone=NULL |
| 15 | WhatsApp Embedded Signup | ChannelConnection active, expires_at=NULL, phone_number_id in metadata |
| 16 | HMAC rejection | HTTP 403 on bad sig; HTTP 403 on missing header; no intake_event_log |
| 17 | Unified lead registry | 1 lead for +919876543210, ≥2 touchpoints from different channels |
| 18 | Facebook OAuth → Instagram auto-connect | Instagram ChannelConnection auto-created with expires_at=NULL |
| 19 | Unroutable webhook | HTTP 200 ACK, no lead created, intake_event_log status='unroutable' |
| 20 | Multi-tenant isolation | Lead routed to correct tenant only, Tenant 1 account unchanged |

---

## Fix Protocol

When a phase fails:
1. Read the Playwright response body and server logs
2. Identify root cause: config mismatch, code bug, or missing setup step
3. Fix at the root (update `.env` → restart server; code bug → edit + restart)
4. Re-run the failed phase from its beginning
5. Only advance to the next phase once all DB checks pass
