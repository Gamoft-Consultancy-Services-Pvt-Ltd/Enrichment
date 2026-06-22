"""Generate Facebook OAuth URL with base_url=http://localhost:8001 override.

Run:  uv run python scripts/gen_fb_oauth_url.py
Outputs the URL to navigate to in Playwright for Phase 11 E2E test.
"""
import os
import sys
import uuid
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Settings
from modules.lead_ingestion.oauth.facebook import build_facebook_auth_url

# Use the test tenant from the E2E suite
TENANT_ID = uuid.UUID("8065e2ad-bba3-46c6-b301-6b7f8c86f4fb")

# Override only base_url; all other settings come from .env
settings = Settings(base_url="https://lorna-nonutilized-macy.ngrok-free.dev")

url = build_facebook_auth_url(TENANT_ID, settings=settings)
print("Facebook OAuth URL:")
print(url)
print()
print("redirect_uri embedded in URL:")
params = parse_qs(urlparse(url).query)
print(params.get("redirect_uri", ["?"])[0])
