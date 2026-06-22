"""One-shot script: re-encrypt FB page token and update channel_connections row.

Run:  uv run python scripts/fix_fb_credentials.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CONNECTION_ID  = "da2e7fcd-0a2a-4809-bc70-7d3eeac6ca2c"
PAGE_ID        = "1346146538572443"
PAGE_NAME      = "Enrichment Testing"
PAGE_TOKEN     = (
    "EAAVPUDwkbawBRz2bvjEgAqlZA8Y0D6433W7urZCe3y6vNg7ohmeXmpqwWpNzjbyOe"
    "XSDCutlv6aaHJKiKWxlVGtZCrbATrZARfj73XcrGsadDv6O7LWZCdjA0vCWfgxuzbP"
    "Y4auVPt5FM9ZA7jJ4z4EXuxqHTMUWJMgzxTHqyYlRo6bPMMBK4fUYsX9KnNrnVrMlJ"
    "JtZAgNvkjy0EmXJ2jSuERX2jts1t0v0NgOipsZD"
)

if __name__ == "__main__":
    import base64
    import json
    import os as _os

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    enc_key_b64 = "yYA-YOw2Rox-1v236MeQeJTt8V4kY9D0ox9t_Tjsm70="
    raw_key = base64.urlsafe_b64decode(enc_key_b64 + "==")
    assert len(raw_key) == 32, f"Key must be 32 bytes, got {len(raw_key)}"

    payload = {"page_access_token": PAGE_TOKEN, "page_id": PAGE_ID}
    plaintext = json.dumps(payload, sort_keys=True).encode()
    nonce = _os.urandom(12)
    aesgcm = AESGCM(raw_key)
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, associated_data=None)
    credentials_bytes = nonce + ciphertext_with_tag

    # Verify we can decrypt it back
    recovered_nonce = credentials_bytes[:12]
    recovered_ct    = credentials_bytes[12:]
    recovered = json.loads(aesgcm.decrypt(recovered_nonce, recovered_ct, associated_data=None))
    assert recovered["page_access_token"] == PAGE_TOKEN, "Decrypt verification failed!"
    print(f"Encryption round-trip verified. Credential length: {len(credentials_bytes)} bytes")

    # Update DB via psycopg2
    import psycopg2
    conn = psycopg2.connect(
        "postgresql://postgres:postgres@localhost:5432/leadengine"
    )
    cur = conn.cursor()
    cur.execute(
        "UPDATE channel_connections SET credentials_encrypted = %s WHERE id = %s",
        (psycopg2.Binary(credentials_bytes), CONNECTION_ID),
    )
    conn.commit()
    print(f"Updated channel_connection {CONNECTION_ID} — rows affected: {cur.rowcount}")
    cur.close()
    conn.close()
    print("Done.")
