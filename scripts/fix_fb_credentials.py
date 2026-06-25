"""One-shot script: re-encrypt FB page token and update channel_connections row.

Secrets are read from environment variables (set via .env or shell export).
Run:  uv run python scripts/fix_fb_credentials.py

Required env vars:
  CHANNEL_CREDENTIALS_ENCRYPTION_KEY  — base64url-encoded 32-byte AES key
  META_PAGE_TOKEN                     — Facebook page access token
  META_PAGE_ID                        — Facebook page ID
  META_CONNECTION_ID                  — UUID of the channel_connections row to update
  DATABASE_URL                        — async DSN, e.g. postgresql+asyncpg://user:pass@host/db
"""

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_required = [
    "CHANNEL_CREDENTIALS_ENCRYPTION_KEY",
    "META_PAGE_TOKEN",
    "META_PAGE_ID",
    "META_CONNECTION_ID",
    "DATABASE_URL",
]
_missing = [v for v in _required if not os.environ.get(v)]
if _missing:
    print(f"ERROR: missing required env vars: {', '.join(_missing)}", file=sys.stderr)
    print("Set them in .env or export them before running.", file=sys.stderr)
    sys.exit(1)

enc_key_b64 = os.environ["CHANNEL_CREDENTIALS_ENCRYPTION_KEY"]
page_token = os.environ["META_PAGE_TOKEN"]
page_id = os.environ["META_PAGE_ID"]
connection_id = os.environ["META_CONNECTION_ID"]
database_url = os.environ["DATABASE_URL"]

from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

raw_key = base64.urlsafe_b64decode(enc_key_b64 + "==")
assert len(raw_key) == 32, f"Key must be 32 bytes, got {len(raw_key)}"

payload = {"page_access_token": page_token, "page_id": page_id}
plaintext = json.dumps(payload, sort_keys=True).encode()
nonce = os.urandom(12)
aesgcm = AESGCM(raw_key)
ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, associated_data=None)
credentials_bytes = nonce + ciphertext_with_tag

recovered_nonce = credentials_bytes[:12]
recovered_ct = credentials_bytes[12:]
recovered = json.loads(aesgcm.decrypt(recovered_nonce, recovered_ct, associated_data=None))
assert recovered["page_access_token"] == page_token, "Decrypt verification failed!"
print(f"Encryption round-trip verified. Credential length: {len(credentials_bytes)} bytes")

pg_url = database_url.replace("postgresql+asyncpg://", "postgresql://")

import psycopg2  # noqa: E402

conn = psycopg2.connect(pg_url)
cur = conn.cursor()
cur.execute(
    "UPDATE channel_connections SET credentials_encrypted = %s WHERE id = %s",
    (psycopg2.Binary(credentials_bytes), connection_id),
)
conn.commit()
print(f"Updated channel_connection {connection_id} — rows affected: {cur.rowcount}")
cur.close()
conn.close()
print("Done.")
