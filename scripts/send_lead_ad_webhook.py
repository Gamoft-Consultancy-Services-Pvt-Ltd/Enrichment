"""Manually deliver the Lead Ads webhook to the local server.

Used when ngrok is offline and Meta cannot reach the registered webhook URL.
Run:  uv run python scripts/send_lead_ad_webhook.py
"""

import hashlib
import hmac
import json
import urllib.request

APP_SECRET = "8a7ad9d320e984b58ecd57000a7eb799"
LEADGEN_ID = "861013726626370"
PAGE_ID = "1346146538572443"
FORM_ID = "1538411674599251"

payload = {
    "object": "page",
    "entry": [
        {
            "id": PAGE_ID,
            "time": 1750582000,
            "changes": [
                {
                    "field": "leadgen",
                    "value": {
                        "leadgen_id": LEADGEN_ID,
                        "page_id": PAGE_ID,
                        "form_id": FORM_ID,
                        "ad_id": "0",
                    },
                }
            ],
        }
    ],
}

body = json.dumps(payload, separators=(",", ":")).encode()
sig = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()

print(f"Sending leadgen_id={LEADGEN_ID} to http://localhost:8000/channels/webhook")
print(f"Signature: {sig}")

req = urllib.request.Request(
    "http://localhost:8000/channels/webhook",
    data=body,
    headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
    method="POST",
)
with urllib.request.urlopen(req) as resp:
    print(f"HTTP {resp.status}: {resp.read().decode()}")
