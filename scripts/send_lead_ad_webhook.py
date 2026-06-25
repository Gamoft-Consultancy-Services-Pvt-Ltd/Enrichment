"""Manually deliver the Lead Ads webhook to the local server.

Used when ngrok is offline and Meta cannot reach the registered webhook URL.
Run:  uv run python scripts/send_lead_ad_webhook.py

Required env vars:
  META_APP_SECRET   — Meta app secret for HMAC-SHA256 signing
  META_LEADGEN_ID   — (optional) leadgen_id; defaults to test value
  META_PAGE_ID      — (optional) page_id; defaults to test value
  META_FORM_ID      — (optional) form_id; defaults to test value
  WEBHOOK_URL       — (optional) target URL; defaults to http://localhost:8000/channels/webhook
"""

import hashlib
import hmac
import json
import os
import sys
import urllib.request

app_secret = os.environ.get("META_APP_SECRET")
if not app_secret:
    print("ERROR: META_APP_SECRET env var is required.", file=sys.stderr)
    print("Set it in .env or: export META_APP_SECRET=<your_secret>", file=sys.stderr)
    sys.exit(1)

leadgen_id = os.environ.get("META_LEADGEN_ID", "861013726626370")
page_id = os.environ.get("META_PAGE_ID", "1346146538572443")
form_id = os.environ.get("META_FORM_ID", "1538411674599251")
webhook_url = os.environ.get("WEBHOOK_URL", "http://localhost:8000/channels/webhook")

payload = {
    "object": "page",
    "entry": [
        {
            "id": page_id,
            "time": 1750582000,
            "changes": [
                {
                    "field": "leadgen",
                    "value": {
                        "leadgen_id": leadgen_id,
                        "page_id": page_id,
                        "form_id": form_id,
                        "ad_id": "0",
                    },
                }
            ],
        }
    ],
}

body = json.dumps(payload, separators=(",", ":")).encode()
sig = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()

print(f"Sending leadgen_id={leadgen_id} to {webhook_url}")
print(f"Signature: {sig}")

req = urllib.request.Request(
    webhook_url,
    data=body,
    headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
    method="POST",
)
with urllib.request.urlopen(req) as resp:
    print(f"HTTP {resp.status}: {resp.read().decode()}")
