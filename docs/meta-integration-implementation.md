---
type: analysis
question: "How exactly do you implement Facebook Page OAuth, Instagram Business Account OAuth, WhatsApp Embedded Signup, multi-tenant webhook routing, and Meta Lead Ads retrieval?"
date: 2026-05-01
tags: [meta, facebook, instagram, whatsapp, oauth, embedded-signup, webhooks, lead-ads, implementation, channel-integration, multi-tenant]
sources_consulted:
  - "[[analyses/meta-platform-api-deep-research]]"
  - "[[analyses/channel-integration-layer]]"
status: COMPLETE — updated 2026-05-03 to add Facebook Page and Instagram connection strategies
---

# Meta Integration — Deep Implementation

**Question:** Exact implementation mechanics for Facebook Page OAuth, Instagram Business Account OAuth, WhatsApp Embedded Signup, multi-tenant webhook routing, and Meta Lead Ads retrieval  
**Date:** 2026-05-01 | **Updated:** 2026-05-03  
**Prerequisite reading:** [[analyses/meta-platform-api-deep-research]] (OAuth flows, token types, payload schemas, rate limits)

> **Supersedes note:** [[analyses/channel-integration-layer]] describes Instagram connection via a linked Facebook Page (the legacy Facebook Login path) and implies per-tenant webhook delivery URLs. This document supersedes both: the current implementation uses the **Instagram Login path** (independent of Facebook Pages, `instagram_business_*` scopes); Meta delivers all webhooks to a **single app-level URL** and routing to tenants is done internally. See Section 2.10 and Section 4.8 for full context.

---

## 1. Facebook Page Connection — Full Implementation

### 1.1 What It Is

Facebook Page connection uses the standard OAuth 2.0 authorization code flow via `facebook.com/dialog/oauth`. The tenant logs into their Facebook Business account, selects which Pages to connect, and your backend receives a code that is exchanged through a three-step chain into non-expiring Page Access Tokens.

Unlike WhatsApp (which uses Facebook Login for Business + Embedded Signup), this is a redirect-based flow. Unlike Instagram (which uses a different authorize endpoint entirely), Facebook OAuth uses the Graph API token chain.

### 1.2 Required Scopes (All Advanced Access)

| Permission | Purpose |
|---|---|
| `pages_show_list` | List all Pages the tenant manages |
| `pages_manage_metadata` | Subscribe Pages to webhooks |
| `pages_messaging` | Read and send Facebook Messenger DMs |
| `pages_read_engagement` | Read page posts, comments, reactions |
| `leads_retrieval` | Retrieve lead ad form submissions |
| `ads_management` | Required for leadgen webhook subscription |

All six require App Review + Business Verification. Dependency chain: `pages_messaging` → `pages_manage_metadata` → `pages_show_list`. (source: [[analyses/meta-platform-api-deep-research]] Section 1.1)

### 1.3 OAuth Redirect

When the tenant clicks "Connect Facebook":

```
https://www.facebook.com/v21.0/dialog/oauth
  ?client_id={your_app_id}
  &redirect_uri=https://app.gamoftx.com/oauth/facebook/callback
  &scope=pages_show_list,pages_manage_metadata,pages_messaging,
         pages_read_engagement,leads_retrieval,ads_management
  &response_type=code
  &state={signed_tenant_id}
```

The `state` value uses the same HMAC-signed pattern as WhatsApp (see Section 3.7).

### 1.4 Token Chain: Code → Non-Expiring Page Token

```
facebook.com/dialog/oauth
    → code returned to redirect_uri
         ↓
Step 1 — exchange code for short-lived User token (~1-2 hours):
POST https://graph.facebook.com/v21.0/oauth/access_token
  ?client_id={app_id}
  &client_secret={app_secret}
  &redirect_uri={callback_url}
  &code={code}
→ { "access_token": "EAA...", "token_type": "bearer" }

         ↓
Step 2 — exchange for long-lived User token (~60 days):
GET https://graph.facebook.com/v21.0/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id={app_id}
  &client_secret={app_secret}
  &fb_exchange_token={short_lived_user_token}
→ { "access_token": "EAA...(long-lived)", "expires_in": 5183944 }

         ↓
Step 3 — derive Page Access Tokens (non-expiring):
GET /me/accounts?access_token={long_lived_user_token}
→ {
    "data": [
      {
        "id":           "{page_id}",
        "name":         "Acme Store",
        "category":     "Retail Company",
        "access_token": "EAA...(page token — non-expiring)",
        "tasks":        ["ANALYZE", "ADVERTISE", "MODERATE", "CREATE_CONTENT"]
      }
    ]
  }
```

Page Access Tokens derived from a long-lived User token do **not expire under normal conditions**. Store only the Page Access Token. The intermediate User tokens are not needed after this step — do not persist them. (source: [[analyses/meta-platform-api-deep-research]] Section 1.1)

### 1.5 Page Selection and Multi-Page Tenants

Present the tenant with a checklist of all Pages returned by `/me/accounts`. A tenant may manage multiple Pages (brand page, regional page, product page). For each Page the tenant selects:

1. Create one `channel_connection` record: `channel_type = "facebook"`, `metadata->>'page_id' = {page_id}`
2. Store Page token at `{tenant_id}/facebook/{page_id}/page_access_token` in Secrets Manager
3. Call `POST /{page_id}/subscribed_apps` (Section 1.6)

### 1.6 Webhook Subscription per Page

After storing the token, activate event delivery for each selected Page:

```
POST /{page_id}/subscribed_apps
   ?subscribed_fields=messages,leadgen
   &access_token={page_access_token}
```

`messages` delivers Messenger DMs. `leadgen` delivers lead ad form submission events (IDs only — actual form data requires the Lead Retrieval API; see Section 5).

**Prerequisite:** the app-level webhook URL and verify token must already be configured in App Dashboard → Webhooks before this per-page call can succeed.

### 1.7 Token Storage

```
{tenant_id}/facebook/{page_id}/page_access_token
```

No scheduled refresh job is needed for Facebook Page tokens — they are non-expiring. Token failure is detected reactively: when any API call returns HTTP 401 with error code `190` (invalid token) or `102` (session invalidated), update `channel_connection.status = "revoked"` and create an `alert_incident`. See Section 4.11 for the monitoring query.

### 1.8 Follow-Up Profile Call for Messenger DM Senders

Facebook Messenger webhook payloads contain only the sender's PSID — no name, no profile picture. Before entering the Person Resolver (Step 9 in [[analyses/global-data-collection-architecture]]), call:

```
GET /{psid}?fields=name,first_name,last_name,profile_pic
   &access_token={page_access_token}
```

Field availability depends on the user's Facebook privacy settings — any field may be null. Proceed without them if null; do not block the pipeline. The returned name seeds the Apollo.io person search at Step 9.

### 1.9 Token Invalidation Conditions

| Condition | Signal |
|---|---|
| User changes their Facebook password | HTTP 401 → error code 190 |
| User removes your app from their account | HTTP 401 → error code 190 |
| User loses admin role on the Page | HTTP 401 → error code 190 |
| Meta security event | HTTP 401 → error code 102 |
| Proactive notification (before API call fails) | Meta POSTs to your `deauthorize_callback_url` (configure in App Dashboard → Settings → Advanced) |

On detection: update `channel_connection.status = "revoked"`, create `alert_incident`, notify tenant admin. Tenant must reconnect via OAuth.

### 1.10 Error Cases

| Scenario | Behaviour |
|---|---|
| Tenant has no Pages | `/me/accounts` returns empty `data`; show: "No Facebook Pages found — create a Business Page first" |
| Tenant cancels mid-flow | No code returned to callback; no record created; show retry prompt |
| Page already connected | Check for existing active `channel_connection` before creating; show: "This Page is already connected" |
| `subscribed_apps` call fails after token storage | Retry once; if still failing, mark `channel_connection.status = "error"`, alert ops |
| Token exchange fails at any step | Log error, do not create `channel_connection`; show tenant: "Connection failed — please try again" |

---

## 2. Instagram Business Account Connection — Full Implementation

### 2.1 What It Is

Instagram connection uses a completely separate OAuth flow from Facebook. The authorize endpoint is `api.instagram.com/oauth/authorize`, not `facebook.com/dialog/oauth`. **No linked Facebook Page is required.** This is the **Instagram API with Instagram Login** path — the only valid path since January 27, 2025, when the old Facebook Login scope names (`business_basic`, `business_manage_messages`, etc.) were deprecated and apps using them can no longer call Instagram endpoints. (source: [[analyses/meta-platform-api-deep-research]] Section 1.2)

### 2.2 Required Scopes

| Permission | Purpose |
|---|---|
| `instagram_business_basic` | Baseline access; required for any Instagram API call |
| `instagram_business_manage_messages` | Read and send Instagram DMs |

Both require App Review + Business Verification. (source: [[analyses/meta-platform-api-deep-research]] Section 1.2)

### 2.3 OAuth Redirect

When the tenant clicks "Connect Instagram":

```
https://api.instagram.com/oauth/authorize
  ?client_id={your_app_id}
  &redirect_uri=https://app.gamoftx.com/oauth/instagram/callback
  &scope=instagram_business_basic,instagram_business_manage_messages
  &response_type=code
  &state={signed_tenant_id}
```

This requires a **separate callback endpoint** from Facebook — `/oauth/instagram/callback`, not `/oauth/facebook/callback`. The token exchange calls also use different base URLs (see Section 2.4).

### 2.4 Token Chain: Code → Long-Lived Token (60-Day TTL)

```
api.instagram.com/oauth/authorize
    → code returned to redirect_uri
         ↓
Step 1 — exchange code for short-lived Instagram User token (~1 hour):
POST https://api.instagram.com/oauth/access_token
  Content-Type: application/x-www-form-urlencoded
  Body: client_id={app_id}
        &client_secret={app_secret}
        &grant_type=authorization_code
        &redirect_uri={callback_url}
        &code={code}
→ { "access_token": "IGQ...", "token_type": "bearer", "user_id": 123456789 }

         ↓
Step 2 — exchange for long-lived token (60 days):
GET https://graph.instagram.com/access_token
  ?grant_type=ig_exchange_token
  &client_id={app_id}
  &client_secret={app_secret}
  &access_token={short_lived_token}
→ {
    "access_token": "IGQ...(long-lived)",
    "token_type": "bearer",
    "expires_in": 5183944   ← seconds; store this
  }
```

**Critical difference from Facebook:** This token expires in 60 days. There is no non-expiring equivalent for the Instagram Login path. A background refresh job is mandatory. (source: [[analyses/meta-platform-api-deep-research]] Section 1.2)

### 2.5 Get Instagram Account ID

Immediately after obtaining the long-lived token:

```
GET https://graph.instagram.com/v25.0/me
   ?fields=id,username
   &access_token={long_lived_token}
→ { "id": "17841406455399901", "username": "acme_brand" }
```

Store:
- `ig_account_id = "17841406455399901"` → `channel_connection.metadata->>'ig_account_id'` (your routing key for incoming webhook events)
- Token → `{tenant_id}/instagram/{ig_account_id}/access_token` in Secrets Manager
- `expires_at = NOW() + seconds(expires_in)` → `channel_connection.metadata->>'expires_at'` (for refresh scheduling)

### 2.6 Token Refresh (60-Day Cycle)

Background job runs daily and checks all active Instagram connections:

```python
def refresh_instagram_tokens(db, secrets_manager):
    connections = db.query(
        "SELECT connection_id, tenant_id, metadata "
        "FROM channel_connection "
        "WHERE channel_type = 'instagram' AND status = 'active'"
    )
    for conn in connections:
        expires_at = parse_datetime(conn.metadata['expires_at'])
        seconds_remaining = (expires_at - datetime.utcnow()).total_seconds()

        if seconds_remaining < 0:
            # already expired — cannot recover server-side
            db.update_status(conn.connection_id, 'expired')
            create_alert_incident(conn.tenant_id, 'instagram_token_expired')
            notify_tenant(conn.tenant_id, "Your Instagram connection expired. Please reconnect.")
            continue

        if seconds_remaining < 604800:  # 7 days remaining
            token = secrets_manager.get(
                f"{conn.tenant_id}/instagram/{conn.metadata['ig_account_id']}/access_token"
            )
            resp = requests.get(
                "https://graph.instagram.com/refresh_access_token",
                params={"grant_type": "ig_refresh_token", "access_token": token}
            ).json()
            secrets_manager.put(
                f"{conn.tenant_id}/instagram/{conn.metadata['ig_account_id']}/access_token",
                resp["access_token"]
            )
            new_expires_at = datetime.utcnow() + timedelta(seconds=resp["expires_in"])
            db.update_metadata(conn.connection_id, {"expires_at": new_expires_at.isoformat()})
```

The refresh call resets the 60-day window from the call time. It can only be called while the token is **still valid** — after expiry, no server-side recovery is possible; the tenant must re-complete the OAuth flow.

### 2.7 Webhook Configuration

Instagram webhooks are configured once at the **App Dashboard level** — not per Instagram account (unlike Facebook where you call `POST /{page_id}/subscribed_apps` for each Page separately). In App Dashboard → Webhooks → Add subscription → Select object `instagram` → subscribe to field `messages`.

This single App Dashboard subscription covers all connected Instagram accounts. **No per-account API call is needed at tenant connection time.**

Webhook payload object type: `"instagram"`. The routing key is `entry[].id` (the Instagram Account ID = `ig_account_id`). See Section 4.4.

### 2.8 Follow-Up Profile Call for Instagram DM Senders

Instagram webhook DM payloads contain only the sender's IGSID — no username, no name, no follower count. Before entering the Person Resolver (Step 9 in [[analyses/global-data-collection-architecture]]), call:

```
GET https://graph.instagram.com/v25.0/{IGSID}
   ?fields=name,username,profile_pic,follower_count,
           is_verified_user,is_user_follow_business,is_business_follow_user
   &access_token={instagram_user_access_token}
```

Available fields: (source: [[analyses/meta-platform-api-deep-research]] Section 2.3)

| Field | Type | Notes |
|---|---|---|
| `name` | string | Display name; may be null |
| `username` | string | Instagram handle |
| `profile_pic` | URL | Expires within days; may be null |
| `follower_count` | integer | |
| `is_verified_user` | boolean | Blue checkmark status |
| `is_user_follow_business` | boolean | Does the sender follow your account? |
| `is_business_follow_user` | boolean | Does your account follow the sender? |

**Consent requirement:** This call is only valid after the user has initiated contact with the business. Cannot be called proactively on unknown users.

**Known limitation — username via IGSID:** Querying `?fields=username` on an IGSID can fail in some API versions. Fallback path:
```
GET /{ig_account_id}/conversations
   ?user_id={igsid}
   &fields=participants
   &access_token={instagram_user_access_token}
```
Returns thread participants including username. Use as fallback only. (source: [[analyses/meta-platform-api-deep-research]] Section 2.3)

### 2.9 Error Cases

| Scenario | Behaviour |
|---|---|
| Token expires before refresh job runs | Mark `expired`, create `alert_incident`, notify tenant to re-authenticate |
| Refresh endpoint returns error (token already expired) | Mark `expired`; no server-side recovery; tenant must redo OAuth |
| Tenant cancels mid-flow | No code returned; no record created; show retry prompt |
| App Dashboard webhook subscription not configured | Instagram DM events never arrive; detected via stale `last_event_at` monitor (Section 4.11) |
| Multiple Instagram accounts for one tenant | Each account requires a separate OAuth flow; one `channel_connection` record per account |

### 2.10 Supersedes Note

[[analyses/channel-integration-layer]] describes two patterns superseded by this section:

1. **Instagram via linked Facebook Page:** That document shows Instagram connected through a Facebook Page using the Facebook Login path. The current implementation uses the Instagram Login path (`api.instagram.com/oauth/authorize`, `instagram_business_*` scopes) with no Page dependency. The old scope names were deprecated January 27, 2025.

2. **Per-tenant webhook URLs:** That document implies delivery to `POST /webhooks/{platform}/{tenant_id}`. Meta delivers to a single app-level URL configured in the App Dashboard. All tenant routing is internal — see Section 4 for the full routing implementation.

---

## 3. WhatsApp Embedded Signup — Full Implementation

### 3.1 What It Is

WhatsApp does not use a standard OAuth redirect. Meta built a separate product called **Facebook Login for Business** specifically for SaaS platforms onboarding other businesses' WhatsApp accounts. Add it in the App Dashboard → Add Product → Facebook Login for Business. It is distinct from standard Facebook Login and must be configured separately.

### 3.2 Frontend — JavaScript SDK Setup

Load the Meta JS SDK on your settings page and launch the popup when the tenant clicks "Connect WhatsApp":

```html
<script>
  window.fbAsyncInit = function() {
    FB.init({
      appId: '{your_app_id}',
      autoLogAppEvents: true,
      xfbml: true,
      version: 'v21.0'
    });
  };
</script>
<script async defer src="https://connect.facebook.net/en_US/sdk.js"></script>
```

```javascript
function launchWhatsAppSignup(tenantId) {
  FB.login(function(response) {
    if (response.authResponse) {
      const code = response.authResponse.code;
      exchangeCodeForToken(code, tenantId);
    } else {
      // tenant cancelled mid-flow — no record created, show retry prompt
    }
  }, {
    config_id: '{your_configuration_id}',   // created once in App Dashboard under Facebook Login for Business
    response_type: 'code',
    override_default_response_type: true,
    extras: {
      setup: {},
      featureType: '',
      sessionInfoVersion: '3',
      state: signState(tenantId),           // HMAC-signed tenant_id — see Section 3.7
    }
  });
}
```

The `config_id` is a configuration created once in the App Dashboard that pre-selects `whatsapp_business_management` and `whatsapp_business_messaging` scopes so the tenant does not have to choose them manually.

### 3.3 What the Tenant Sees (Popup Flow)

1. Log in to their Facebook Business account (skipped if already logged in)
2. Select or create a Meta Business Portfolio
3. Select or create a WhatsApp Business Account (WABA)
4. Select or register a phone number (OTP verification if registering a new number)
5. Confirm permissions your app is requesting
6. Popup closes; `FB.login` callback fires with the `code`

If the tenant already has a WABA, they select it in step 3 — they do not re-register it.

### 3.4 Backend — Code Exchange

Your backend receives the `code` from the frontend and exchanges it:

```
POST https://graph.facebook.com/v21.0/oauth/access_token
  ?client_id={app_id}
  &client_secret={app_secret}
  &code={code}
```

Response:
```json
{
  "access_token": "EAAB...",
  "token_type": "bearer"
}
```

This is the **Business Integration System User access token**. It is non-expiring. Store it in Secrets Manager at:
```
{tenant_id}/whatsapp/{phone_number_id}/access_token
```

### 3.5 Fetching WABA ID and Phone Number IDs

After the code exchange, use the `granular_scopes` field from a debug token call to find which WABA IDs granted permissions:

```
GET /debug_token?input_token={access_token}&access_token={app_id}|{app_secret}
```

Then enumerate phone numbers for each WABA:

```
GET /{waba_id}/phone_numbers
   ?access_token={access_token}
```

Response gives an array of phone number objects, each with:
- `id` — the `phone_number_id` (your routing key)
- `display_phone_number` — human-readable number
- `verified_name` — the WhatsApp Business display name
- `quality_rating` — GREEN / YELLOW / RED

**One WABA, multiple phone numbers:** Supported. Create one `channel_connection` record per `phone_number_id`, all sharing the same `tenant_id` and `waba_id`.

### 3.6 Webhook Registration on the WABA

After storing the token, subscribe your app to receive events for this WABA:

```
POST /{waba_id}/subscribed_apps
   ?access_token={access_token}
```

No body required. This tells Meta to start delivering `messages` webhook events for this WABA to your app's configured webhook URL. Do this once per WABA at connection time.

### 3.7 The `state` Parameter and Multi-Tenant Security

Pass a signed value (HMAC-signed `tenant_id`) as `state` in the Embedded Signup extras. When the flow completes, your callback receives it back. This tells your backend which tenant completed the signup, so you know which `channel_connection` records to create. The same signing pattern is used for Facebook (Section 1.3) and Instagram (Section 2.3) OAuth.

```python
import hmac, hashlib, base64

def sign_state(tenant_id: str, secret: str) -> str:
    sig = hmac.new(secret.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{tenant_id}:{sig}".encode()).decode()

def verify_state(state: str, secret: str) -> str:
    decoded = base64.urlsafe_b64decode(state).decode()
    tenant_id, sig = decoded.rsplit(':', 1)
    expected = hmac.new(secret.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("Invalid state signature")
    return tenant_id
```

### 3.8 Error Cases

| Scenario | Behaviour |
|---|---|
| Tenant cancels mid-flow | `response.authResponse` is null; no code returned; no record created; show retry prompt |
| Tenant already has a WABA | They select it in the flow; you receive a token for that existing WABA |
| Phone number registered to another WABA | Meta blocks it in the flow; tenant must use a different number |
| Business Verification not complete | Token is issued; API calls succeed; but messaging permission errors occur until verified |
| Token exchange fails | Log error, alert tenant, do not create `channel_connection`; they must retry |

---

## 4. Webhook Routing Logic — Multi-Tenant Implementation

### 4.1 The Core Problem

All tenants share one Meta App. Meta delivers all webhook events — from every tenant's Facebook Page, Instagram account, and WhatsApp number — to one endpoint URL. Routing must identify the correct tenant from the payload within the same request cycle.

### 4.2 Response-First Architecture

Always return HTTP 200 immediately after HMAC validation. Never do synchronous downstream work inside the webhook handler. If your endpoint is slow or throws an error, Meta retries — causing duplicates.

```
Webhook POST arrives
       ↓
Validate HMAC-SHA256 → if invalid: return 403, done
       ↓
Enqueue raw payload + timestamp to internal job queue
       ↓
Return HTTP 200 immediately
       ↓
(async) Worker picks up job → route → normalise → write → Pipeline 1
```

Meta retries failed deliveries for up to 24 hours with exponential backoff. After 24 hours, the event is dropped. Your polling fallback catches anything dropped.

### 4.3 HMAC-SHA256 Validation

Meta signs every POST with your **app secret** (one secret for the whole app, not per-connection). The signature is in `X-Hub-Signature-256` as `sha256={hex_digest}`.

```python
import hmac, hashlib

def validate_meta_signature(
    raw_body: bytes,
    signature_header: str,
    app_secret: str
) -> bool:
    expected = hmac.new(
        app_secret.encode('utf-8'),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    received = signature_header.removeprefix('sha256=')
    return hmac.compare_digest(expected, received)
```

**Critical:** validate on the **raw request body bytes** before any JSON parsing. If your framework auto-parses and you re-serialize for validation, byte representation may differ and validation will fail spuriously.

### 4.4 Facebook and Instagram Routing

First, read `payload["object"]` to identify the surface:
- `"page"` → Facebook Messenger event; `entry[].id` is the **Page ID**
- `"instagram"` → Instagram DM or comment event; `entry[].id` is the **Instagram Account ID**
- `"whatsapp_business_account"` → WhatsApp event; routed via Section 4.5

Then look up the tenant:

```sql
SELECT tenant_id, connection_id, channel_type
FROM channel_connection
WHERE (metadata->>'page_id' = $1 OR metadata->>'ig_account_id' = $1)
  AND status = 'active';
```

The `OR` condition is wrapped in parentheses so that `AND status = 'active'` applies to both branches.

Add a GIN index on the `metadata` JSONB column. At scale, this lookup is sub-millisecond.

### 4.5 WhatsApp Routing

WhatsApp payloads contain `entry[].changes[].value.metadata.phone_number_id`. This is your routing key:

```sql
SELECT tenant_id, connection_id
FROM channel_connection
WHERE metadata->>'phone_number_id' = $1
  AND channel_type = 'whatsapp'
  AND status = 'active';
```

One tenant can have multiple phone numbers (multiple `channel_connection` rows). The `phone_number_id` uniquely identifies which one received the message.

### 4.6 Distinguishing Message Types Inside the Payload

After routing to a tenant, the worker must determine what kind of event arrived:

**Facebook/Instagram — check `messaging` vs `changes` key:**
```python
for entry in payload['entry']:
    if 'messaging' in entry:
        # Messenger DM (object: "page") or Instagram DM (object: "instagram")
        for event in entry['messaging']:
            if 'message' in event:
                handle_dm(event, channel_type)
            elif 'postback' in event:
                handle_postback(event)
    elif 'changes' in entry:
        for change in entry['changes']:
            if change['field'] == 'leadgen':
                handle_leadgen(change['value'])
            elif change['field'] == 'comments':
                handle_comment(change['value'])
```

**WhatsApp — check for `messages` vs `statuses` inside `value`:**
```python
value = entry['changes'][0]['value']
if 'messages' in value:
    handle_inbound_message(value)
elif 'statuses' in value:
    handle_status_update(value)   # delivery/read receipts — not leads, ignore at MVP
```

### 4.7 Webhook Deduplication

The same event can arrive via webhook and polling fallback. Make the DB write idempotent using a unique constraint on `platform_event_id`:

```sql
INSERT INTO event (platform_event_id, tenant_id, lead_id, channel_type, raw_payload, created_at)
VALUES ($1, $2, $3, $4, $5, NOW())
ON CONFLICT (platform_event_id) DO NOTHING;
```

The second write (whichever arrives second — webhook or poller) silently no-ops. No separate dedup check required.

### 4.8 App-Level vs Per-Resource Subscription — All Required

| Level | What it is | How | When |
|---|---|---|---|
| App-level (all surfaces) | Webhook URL + verify token | App Dashboard → Webhooks → Edit | Once, manually, before launch |
| Page-level (Facebook) | Activate event delivery for a specific Page | `POST /{page_id}/subscribed_apps?subscribed_fields=messages,leadgen&access_token={page_token}` | At each tenant Page connection |
| App-level (Instagram) | Subscribe to Instagram object | App Dashboard → Webhooks → instagram → messages field | Once, manually; covers all accounts |
| WABA-level (WhatsApp) | Subscribe WABA to messages field | `POST /{waba_id}/subscribed_apps?access_token={token}` | At each tenant WABA connection |

App-level configuration must exist before any per-resource subscription call succeeds. Instagram requires no per-account API call — the single App Dashboard subscription covers all connected Instagram accounts. Facebook and WhatsApp require a per-resource API call at tenant connection time.

### 4.9 Challenge Verification

The GET challenge request fires **once at setup time** — when you first register the webhook URL in the App Dashboard. Your endpoint must respond with the `hub.challenge` value as plain text with HTTP 200. No JSON wrapper, no quotes.

```python
@app.get('/webhooks/meta')
def webhook_verify(hub_mode, hub_challenge, hub_verify_token):
    if hub_mode == 'subscribe' and hub_verify_token == VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type='text/plain', status_code=200)
    return Response(status_code=403)
```

### 4.10 Multiple Pages / Accounts Per Tenant

A tenant may manage multiple Facebook Pages or Instagram accounts. One `channel_connection` record per Page/account, all with the same `tenant_id`.

- **Facebook:** The OAuth flow returns all Pages via `/me/accounts`; present the tenant with a checklist of which to connect
- **Instagram:** The OAuth flow returns one Instagram account per login; for multiple Instagram accounts, the tenant must complete the OAuth flow once per account

### 4.11 Token Revocation Detection

When a tenant removes your app from their Facebook account, the Page-level webhook subscription is automatically removed. The primary signal is silence — no events where events are expected.

```sql
SELECT tenant_id, connection_id, channel_type, last_event_at
FROM channel_connection
WHERE status = 'active'
  AND last_event_at < NOW() - INTERVAL '48 hours'
  AND channel_type IN ('facebook', 'instagram', 'whatsapp');
```

Alert on any active connection with no events in 48 hours. Could be revocation, token expiry, or webhook misconfiguration.

---

## 5. Meta Lead Ads Retrieval — Full Implementation

### 5.1 The Complete Sequence

```
1. Tenant creates a Lead Ad on Facebook or Instagram
2. User sees the ad, fills the form, submits
3. Meta fires leadgen webhook within seconds:

   POST /webhooks/meta
   {
     "object": "page",
     "entry": [{
       "id": "<PAGE_ID>",
       "changes": [{
         "field": "leadgen",
         "value": {
           "leadgen_id":   "123456789",
           "page_id":      "<PAGE_ID>",
           "form_id":      "987654321",
           "adgroup_id":   "111111111",
           "ad_id":        "222222222",
           "created_time": 1716000000
         }
       }]
     }]
   }

4. Return HTTP 200 immediately; enqueue {leadgen_id, page_id, form_id, ad_id, tenant_id}

5. Worker calls Lead Retrieval API:
   GET /{leadgen_id}
      ?fields=id,created_time,ad_id,form_id,field_data,custom_disclaimer_responses
      &access_token={page_access_token}

6. Response:
   {
     "id": "123456789",
     "created_time": "2026-05-01T10:00:00+0000",
     "ad_id": "222222222",
     "form_id": "987654321",
     "field_data": [
       { "name": "email",                      "values": ["priya@example.com"] },
       { "name": "phone_number",               "values": ["+919876543210"] },
       { "name": "full_name",                  "values": ["Priya Sharma"] },
       { "name": "company_name",               "values": ["Acme Pvt Ltd"] },
       { "name": "job_title",                  "values": ["Founder"] },
       { "name": "which_city_are_you_from_0",  "values": ["Mumbai"] }
     ],
     "custom_disclaimer_responses": [
       { "checkbox_key": "gdpr_consent_0", "is_checked": true }
     ]
   }

7. Look up form field map for form_id → normalise field_data
8. Build NormalisedChannelEvent → write lead + event → trigger Pipeline 1
   (Lead Ads skip Message Parser — see Section 5.8 and [[analyses/global-data-collection-architecture]] Section 14)
```

### 5.2 Form Definition Pre-Fetch

The `field_data[].name` for custom questions is an internal key (e.g., `which_city_are_you_from_0`) — not the human-readable label. Reliable normalisation requires knowing the mapping.

**When to fetch:** At connection time, enumerate all lead forms for the tenant's Page:

```
GET /{page_id}/leadgen_forms
   ?access_token={page_access_token}
   &fields=id,name,questions,status
```

Each question in the response:
```json
{
  "type":  "CUSTOM",
  "key":   "which_city_are_you_from_0",
  "label": "Which city are you from?"
}
```

Standard built-in fields have typed `type` values: `EMAIL`, `PHONE`, `FULL_NAME`, `COMPANY_NAME`, `JOB_TITLE`, `CITY`, `STATE`, `COUNTRY`, `ZIP_CODE`, `DATE_OF_BIRTH`, `GENDER`.

**Store the mapping** in a `lead_form_field_map` table:

```sql
CREATE TABLE lead_form_field_map (
  form_id     TEXT NOT NULL,
  field_key   TEXT NOT NULL,
  field_label TEXT,
  field_type  TEXT,          -- EMAIL | PHONE | FULL_NAME | CUSTOM | ...
  PRIMARY KEY (form_id, field_key)
);
```

Refresh whenever the tenant adds or modifies a lead form. Also refresh when a `leadgen` webhook arrives with a `form_id` not yet in the map.

### 5.3 Normalisation Logic

```python
STANDARD_FIELD_MAP = {
    'email':        'email',
    'phone_number': 'phone',
    'full_name':    'full_name',
    'first_name':   'first_name',
    'last_name':    'last_name',
    'company_name': 'company',
    'job_title':    'job_title',
    'city':         'city',
    'state':        'state',
    'country':      'country',
    'zip_code':     'zip_code',
}

def normalise_lead_ad(field_data: list, form_id: str, db) -> dict:
    normalised = {}
    extra = {}

    form_map = db.fetch_form_map(form_id)  # {key: {label, type}}

    for field in field_data:
        key = field['name']
        value = field['values'][0] if len(field['values']) == 1 else field['values']

        if key in STANDARD_FIELD_MAP:
            normalised[STANDARD_FIELD_MAP[key]] = value
        elif key in form_map:
            extra[form_map[key]['label']] = value   # use human label as extra key
        else:
            extra[key] = value                       # fallback: raw key

    return {'mapped': normalised, 'extra_fields': extra}
```

### 5.4 Multi-Select Custom Questions

If a custom question allows multiple selections, `values` is a multi-element array:

```json
{ "name": "interested_products_0", "values": ["Product A", "Product C"] }
```

Store as a JSON array in `extra_fields`. Do not flatten to a comma-separated string — that breaks downstream parsing.

### 5.5 Custom Disclaimer Responses

Responses to optional custom disclaimer checkboxes do NOT appear in `field_data`. Include `custom_disclaimer_responses` explicitly in your retrieval call fields parameter. Store the result on the lead record for compliance purposes (e.g., GDPR consent tracking).

### 5.6 Race Condition — Fetching Too Early

If you call `GET /{leadgen_id}` within milliseconds of the webhook, the lead record may not yet be readable on Meta's side. Meta returns a generic error with code 100.

Handle with one delayed retry:

```python
async def fetch_lead_with_retry(leadgen_id, token, delay_seconds=3):
    try:
        return fetch_lead(leadgen_id, token)
    except MetaApiError as e:
        if e.code == 100:
            await asyncio.sleep(delay_seconds)
            return fetch_lead(leadgen_id, token)
        raise
```

### 5.7 Polling Fallback for Lead Ads

If a webhook is missed, poll leads directly from a form using `last_event_at` as the cursor:

```
GET /{form_id}/leads
   ?access_token={page_access_token}
   &fields=id,created_time,field_data,custom_disclaimer_responses
   &filtering=[{"field":"time_created","operator":"GREATER_THAN","value":{unix_timestamp}}]
```

Paginate using the `next` cursor in `paging.cursors`. Continue until no `next` cursor is returned.

Enumerate all active forms to poll:
```
GET /{page_id}/leadgen_forms
   ?access_token={page_access_token}
   &fields=id,status
```
Filter for `status: ACTIVE`. Use `platform_event_id` dedup (Section 4.7) to ensure leads already written via webhook are not double-processed.

### 5.8 Lead Ads Pipeline Entry Point

Lead Ads enter [[analyses/global-data-collection-architecture]] Pipeline 1 differently from DM messages. Because `field_data` already provides structured `name`, `email`, `phone`, `company`, and `job_title`, the pipeline skips:
- **Step 0 (Pre-Filter Gate)** — no raw message to filter
- **Step 1 (Message Parser)** — no unstructured message to parse
- **Step 2 (Free Signal Scorer)** — B2B/B2C determined directly from form fields presence

Lead Ads enter at **Step 3 (Jurisdiction Classifier)** with pre-populated fields. See [[analyses/global-data-collection-architecture]] Section 14 for the full two-path entry diagram.

### 5.9 Attribution Data

The `adgroup_id` and `ad_id` fields in the webhook payload (and on the lead retrieval response) identify exactly which ad campaign and ad set generated the lead. Store these on the `event` record. This enables:
- Per-campaign lead quality reporting for the tenant
- Signal scoring: leads from high-intent campaigns get a higher intent signal weight

---

## 6. What Needs to Be Built — Implementation Checklist

| Component | Purpose | Notes |
|---|---|---|
| Facebook OAuth redirect (`/oauth/facebook`) | Initiate Facebook Page connection | Include all 6 scopes |
| Facebook callback handler (`/oauth/facebook/callback`) | Code → short-lived → long-lived → Page token chain | Discard user tokens after page tokens derived |
| Page selection UI | Tenant picks which Pages to connect | One `channel_connection` per Page selected |
| `POST /{page_id}/subscribed_apps` call | Activate Facebook/Instagram webhooks per Page | Called at connection time for each selected Page |
| Facebook token invalidation handler | React to 401 + error codes 190/102 | Mark revoked, alert tenant |
| `deauthorize_callback_url` endpoint | Proactive revocation notification from Meta | Configure in App Dashboard → Settings → Advanced |
| Instagram OAuth redirect (`/oauth/instagram`) | Initiate Instagram account connection | Separate endpoint from Facebook; `api.instagram.com/oauth/authorize` |
| Instagram callback handler (`/oauth/instagram/callback`) | Code → short-lived → long-lived Instagram token | Store `expires_at` for refresh scheduling |
| Instagram account ID fetch | Get `ig_account_id` after token exchange | `GET /me?fields=id,username` on `graph.instagram.com` |
| Instagram token refresh job | Refresh 60-day Instagram tokens before expiry | Daily check; refresh when `expires_at - NOW() < 7 days` |
| Instagram App Dashboard webhook subscription | Configure `instagram` object → `messages` field | Done once manually; covers all accounts |
| Facebook Login for Business config | WhatsApp Embedded Signup prerequisite | Done once in App Dashboard |
| Embedded Signup JS widget | Launch WhatsApp connection popup | Pass HMAC-signed `state={tenant_id}` |
| `/embedded-signup/callback` | WhatsApp code → WABA token | Fetch phone_number_ids, register WABA webhook |
| `POST /{waba_id}/subscribed_apps` call | Activate WhatsApp webhooks per WABA | Called at connection time |
| `sign_state` / `verify_state` utility | Shared across all three OAuth flows | Used by Facebook, Instagram, and WhatsApp callbacks |
| Webhook endpoint (GET) | Challenge verification | Respond with `hub.challenge` as plain text |
| Webhook endpoint (POST) | HMAC validation → enqueue → HTTP 200 | Raw bytes validation before JSON parse |
| Webhook router | `object` type → `entry[].id` / `phone_number_id` → tenant lookup | GIN index on `channel_connection.metadata` |
| Webhook worker | Event type dispatch → normalise → write → Pipeline 1 | Async; handles DM + leadgen + comment |
| Facebook PSID profile call | Fetch sender name before Person Resolver | `GET /{psid}?fields=name,first_name,last_name,profile_pic` |
| Instagram IGSID profile call | Fetch sender profile before Person Resolver | `GET https://graph.instagram.com/v25.0/{IGSID}?fields=...` |
| Lead Retrieval API call | Fetch actual form field values | Include `custom_disclaimer_responses` in fields param |
| Race condition retry | Handle `code 100` on immediate lead retrieval | One retry after 3 seconds |
| Form definition pre-fetcher | Build `lead_form_field_map` per tenant per form | Run at connection time + on unknown `form_id` |
| `lead_form_field_map` table | Map `field_key` → `field_label` + `field_type` | Enables reliable custom field normalisation |
| Polling fallback job | Catch missed webhooks per channel per tenant | Uses `last_event_at` as cursor |
| Stale connection monitor | Detect token revocation or misconfiguration via silence | Alert on active connections with no events > 48h |

---

## Caveats and Gaps

- **Facebook Login for Business is a separate App configuration** from standard Facebook Login. If your app was built with standard Facebook Login, the WhatsApp Embedded Signup flow requires adding Facebook Login for Business as a distinct product in the App Dashboard.
- **Instagram and Facebook use different OAuth base URLs.** Instagram: `api.instagram.com/oauth/authorize` and `graph.instagram.com` for token exchange. Facebook: `facebook.com/dialog/oauth` and `graph.facebook.com`. Mixing them causes silent failures.
- **Instagram tokens expire; Facebook Page tokens do not.** A refresh job is mandatory for Instagram. It is not needed for Facebook. Building both under the same "token refresh" system will cause confusion — keep them separate.
- **Instagram requires no per-account webhook subscription call**, but Facebook requires one per Page. This asymmetry is easy to miss — the missing Page subscription causes Facebook events to never arrive, while missing the App Dashboard Instagram subscription causes Instagram events to never arrive.
- **WhatsApp sender profile picture is not available** via the official Cloud API. There is no workaround. Identity enrichment for WhatsApp leads relies on the phone number via Truecaller. See [[analyses/lead-enrichment-architecture]].
- **Custom lead form field names cannot be predicted** without the form definition pre-fetch. Do not attempt to normalise custom fields by guessing the `key` value.
- **Meta App Review must be complete** before any of this works for real tenant accounts. During development, all flows work for accounts with a developer/admin role on the Meta App. See [[analyses/meta-platform-api-deep-research]] Section 1.4 for timeline. Plan a 2-week minimum buffer.
- **WhatsApp outbound messaging tiers:** New WABAs start at TIER_250 (250 unique recipients per 24h outbound). Inbound event ingestion is unlimited. Becomes relevant when auto-response or outreach features are added post-MVP.

## Follow-up Questions

- Should the form definition pre-fetcher run on a nightly schedule to catch newly created forms, or only trigger on unknown `form_id` in the webhook? Nightly sweep is safer for tenants who create forms outside business hours.
- Should `custom_disclaimer_responses` be stored as a separate boolean field on the lead record (for GDPR compliance filtering) or as part of `extra_fields` JSONB?
- What is our plan for the WhatsApp TIER_250 onboarding limitation — should we document this as a known constraint in the tenant onboarding flow for the first 30 days?
- For Facebook DM sender profile enrichment, how do we handle users with strict privacy settings who return empty/null profile fields? (Current plan: proceed without; Person Resolver uses Apollo.io as primary.)
- Should we attempt the Instagram Conversations API participants workaround for username, or rely solely on the User Profile API and accept potential failures?
