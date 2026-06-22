"""Generate Instagram Business Login OAuth URL for Phase 13 E2E test.

Run:  uv run python scripts/gen_ig_oauth_url.py
Outputs the URL to navigate to in Playwright.

Prerequisites:
  .env must contain:
    META_IG_APP_ID=1510242517263430
    META_IG_APP_SECRET=<value from Meta → Enrichment-IG app → Settings → Basic>
    BASE_URL=https://lorna-nonutilized-macy.ngrok-free.dev
"""
import os
import sys
import uuid
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Settings
from modules.lead_ingestion.oauth.instagram import build_instagram_auth_url

TENANT_ID = uuid.UUID("8065e2ad-bba3-46c6-b301-6b7f8c86f4fb")

settings = Settings(base_url="https://lorna-nonutilized-macy.ngrok-free.dev")
url = build_instagram_auth_url(TENANT_ID, settings=settings)
print("Instagram OAuth URL:")
print(url)
print()
print("redirect_uri embedded in URL:")
params = parse_qs(urlparse(url).query)
print(params.get("redirect_uri", ["?"])[0])
print()
print(f"client_id in URL: {params.get('client_id', ['?'])[0]}")
