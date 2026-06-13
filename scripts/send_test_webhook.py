"""Fire a signed WhatsApp webhook at the running app and show what happened in the DB.

Usage:
    # Terminal 1 — start the app
    uv run uvicorn main:app --reload

    # Terminal 2 — seed test data (once)
    uv run python scripts/seed_webhook_test.py

    # Terminal 3 — fire the webhook
    uv run python scripts/send_test_webhook.py [dm|noise]

    dm    → sends wa_dm_01.json  (real lead message, goes through Groq filter)
    noise → sends wa_noise_01.json (emoji thumb-up, Stage 1 short-circuit, no Groq)

The script signs the payload with META_APP_SECRET from .env, hits POST /channels/webhook,
then queries the DB to show the Lead that was created.

NOTE: the webhook endpoint enqueues an ARQ job.  For the Lead to appear in the DB you need
the ARQ worker running too:
    uv run arq workers.worker.WorkerSettings

If you just want to verify the endpoint returns {"status": "received"} without waiting for
the worker, that's enough to prove the HTTP layer works.
"""

import asyncio
import hashlib
import hmac
import json
import sys
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import get_settings
from modules.lead_ingestion.db.models import IntakeEventLog, Lead

_BASE = "http://localhost:8000"
_FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures" / "lead_ingestion"


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def _show_db_state(platform_event_id: str) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        log = (
            await session.execute(
                select(IntakeEventLog).where(IntakeEventLog.platform_event_id == platform_event_id)
            )
        ).scalar_one_or_none()

        if log is None:
            print("  IntakeEventLog: NOT FOUND (worker may not have run yet)")
            await engine.dispose()
            return

        print(f"  IntakeEventLog.status      = {log.status}")
        print(f"  IntakeEventLog.lead_id     = {log.lead_id}")

        if log.lead_id:
            lead = (
                await session.execute(select(Lead).where(Lead.id == log.lead_id))
            ).scalar_one_or_none()
            if lead:
                print(f"  Lead.pipeline_stage        = {lead.pipeline_stage}")
                print(f"  Lead.full_name             = {lead.full_name}")
                print(f"  Lead.phone                 = {lead.phone}")
                print(f"  Lead.source_channel        = {lead.source_channel}")

    await engine.dispose()


async def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "dm"
    fixture_file = "wa_dm_01.json" if mode == "dm" else "wa_noise_01.json"
    fixture_path = _FIXTURES / fixture_file

    settings = get_settings()
    payload_bytes = fixture_path.read_bytes()
    payload = json.loads(payload_bytes)

    # Extract platform_event_id for later DB lookup
    try:
        platform_event_id: str = payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
    except (KeyError, IndexError):
        platform_event_id = "unknown"

    signature = _sign(payload_bytes, settings.meta_app_secret)

    print(f"Fixture       : {fixture_file}")
    print(f"platform_event_id: {platform_event_id}")
    print(f"Signature     : {signature[:30]}...")
    print()

    # 1. Verify the GET webhook endpoint works
    print("=== GET /channels/webhook (hub challenge) ===")
    r = httpx.get(
        f"{_BASE}/channels/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": settings.meta_webhook_verify_token,
            "hub.challenge": "test_challenge_12345",
        },
    )
    print(f"  Status  : {r.status_code}  (expected 200)")
    print(f"  Body    : {r.text!r}  (expected 'test_challenge_12345')")
    print()

    # 2. Fire the signed POST webhook
    print("=== POST /channels/webhook (signed message) ===")
    r = httpx.post(
        f"{_BASE}/channels/webhook",
        content=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature,
        },
    )
    print(f"  Status  : {r.status_code}  (expected 200)")
    print(f"  Body    : {r.json()}")
    print()

    if r.status_code == 200 and r.json().get("status") == "received":
        print("Webhook accepted. Waiting 2s for ARQ worker to process...")
        await asyncio.sleep(2)
        print()
        print("=== DB state after worker run ===")
        await _show_db_state(platform_event_id)
    elif r.json().get("status") == "unknown_connection":
        print("No ChannelConnection found. Did you run seed_webhook_test.py?")
    else:
        print("Unexpected response — check app logs.")


asyncio.run(main())
