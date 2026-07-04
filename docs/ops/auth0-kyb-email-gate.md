# Auth0 KYB Email Gate (runbook)

**Status:** Configured in the Auth0 tenant (2026-06-22).
**Purpose:** First half of the PAN-KYB anti-abuse conjunction — enforce
**business-domain email** at login. The second half (PAN identity match) lives in
the app (`modules/tenant_onboarding/kyb.py`). See
`docs/superpowers/specs/2026-06-19-pan-kyb-design.md`.

This is **Auth0 config, not application code.** The app only verifies Auth0-minted
JWTs (`auth/token.py`), so the login method and the email gate are tenant settings.
Nothing here is covered by the pytest suite by design.

## 1. Passwordless email login

- **Authentication → Passwordless → Email**: enabled.
- **Method:** **OTP code** (6-digit), not magic link.
  - Rationale: magic link is **not supported on New Universal Login** (it needs the
    older Classic experience). The OTP code proves mailbox control just as well, so
    we stay on New Universal Login and use the code. Switching to Classic just to get
    a clickable link is not worth losing the modern login experience.
- **Disable Sign Ups: OFF** — onboarding is self-serve; a new business email must be
  able to create its account on first login. The email gate (below) still blocks
  free-email addresses regardless of this toggle.
- **Authentication Parameters:** left blank (no custom scope/audience needed).
- Not promoted to domain level — single app, so per-app enablement is enough.

### Gotchas that actually blocked setup (do these or it won't work)

These three were the real blockers — the symptoms were misleading:

1. **Enable Identifier First.** `Authentication → Authentication Profile → Identifier
   First`. The default profile asks **email + password on one screen**, which
   silently renders a password field even with passwordless enabled. Passwordless
   email only works with Identifier First (email screen first, then the code).
   *Symptom if skipped:* login/signup keeps showing an email **and password** form.
2. **Assign the app to the connection from the connection's side.** `Authentication →
   Passwordless → Email → Applications` tab → toggle the SPA app (`mYT5…`) ON. The
   application's own Connections tab may not list passwordless connections, so this
   must be done here. *Symptom if skipped:* `auth error [invalid_request]: no
   connections enabled for the client`.
3. **Disable the database + social connections on the app.** Turn OFF
   `Username-Password-Authentication` (Database) and Google (Social), leaving only
   Email passwordless. *Symptom if skipped:* email+password form persists.

Verify against the **right** app: Swagger logs into the app whose Client ID matches
`AUTH0_SPA_CLIENT_ID` (`mYT5…`). Editing a different app's connections does nothing.
Confirm via the `client_id=…` in the Auth0 redirect URL. Test in an **incognito**
window — the old login page caches aggressively.

### Email provider caveat (pre-launch TODO — confirmed issue)

The tenant currently uses Auth0's **built-in dev/trial email provider**. It works for
testing but: it is **rate-limited**, **ignores custom email-template edits**, and
**its OTP emails land in spam** — observed in testing. The spam is because the
default provider sends from a generic shared Auth0 domain with **no SPF/DKIM/DMARC
authentication for a domain we own** and from shared-reputation IPs.

- For testing: mark the OTP email "Not spam" to train the inbox; otherwise fine.
- **Before production (required):** configure a **Custom Email Provider** (Amazon SES
  / SendGrid / Mailgun / SMTP) under **Branding → Email Provider**, sending from a
  **From-address on a domain we control**, with **SPF + DKIM (+ DMARC)** DNS records
  added. That domain authentication is what moves OTPs from spam to inbox; it also
  lifts the rate limit and makes custom template edits take effect.
- Do **not** bother customizing the passwordless email template until the custom
  provider is in place — edits silently won't apply on the default provider.

## 2. Free-email blocklist (Post-Login Action)

Enforced entirely in Auth0. Because every token is Auth0-minted and
signature-verified, a reliably-denying Action means no free-email token can exist —
so an app-side duplicate check is intentionally **omitted**.

- **Action:** `block-free-email-domains`, trigger **Login / Post Login**.
- Checks `event.user.email_verified` and the email domain against a small, stable
  **free-provider blocklist**; calls `api.access.deny(...)` on failure.

```javascript
exports.onExecutePostLogin = async (event, api) => {
  const FREE_DOMAINS = new Set([
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "icloud.com", "me.com", "aol.com", "proton.me",
    "protonmail.com", "zoho.com", "yandex.com", "gmx.com",
    "mail.com", "rediffmail.com",
  ]);

  const email = (event.user.email || "").toLowerCase();
  const domain = email.split("@")[1];

  if (!event.user.email_verified) {
    api.access.deny("Please verify your email before continuing.");
    return;
  }
  if (!domain || FREE_DOMAINS.has(domain)) {
    api.access.deny("Please sign in with your company email address.");
  }
};
```

**Deferred:** disposable-email-domain blocking (thousands of churning domains; YAGNI
v1). If added later it goes **app-side** as a testable data file, not here.

## 3. Action ordering in the post-login flow

In **Actions → Triggers → post-login**, the gate runs **before** the custom-claims
Action, so a rejected login fails fast before any claim work:

```
Start
  → block-free-email-domains        (deny free-email / unverified here)
  → set-custom-claims               (stamps email / role / tenant_id claims)
Complete
```

The custom-claims Action sets, under the `https://leadengine/` namespace
(matches `auth_claim_namespace` in `core/config.py`): `email`, `role`
(from `app_metadata.role`, default `TENANT`), and `tenant_id` (when present). The
app's `auth/` reads these from the verified JWT. That Action is essential — leave it
in place.

## 4. Verification

- Log in with a free-email address (e.g. `…@gmail.com`) → **denied**.
- Log in with a business-domain address → **passes**, token carries the custom claims.
