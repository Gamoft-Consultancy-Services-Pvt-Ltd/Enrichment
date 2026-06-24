"""Manual compliance test script — COMP-301, COMP-302, COMP-303.

Run inside the Docker app container:
  docker exec enrichment-app-1 uv run python scripts/manual_test_compliance.py

Tests:
  1. COMP-302 — Retention sweep anonymises aged leads (real DB, direct service call)
  2. COMP-302 — ARQ cron job enqueued so worker can pick it up and prove the wiring
  3. COMP-303 — Erasure service nulls PII on real leads
  4. COMP-303 — POST /erasure-request endpoint: routing, auth guard (401), 400 on bad type
  5. COMP-301 — Sensitive fields stripped from file-upload extra_fields
"""

import asyncio
import hashlib
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

sys.path.insert(0, "/app")

# Register all ORM models in Base.metadata (same as integration conftest)
import auth.models  # noqa: F401
import modules.lead_ingestion.db.models  # noqa: F401
import shared.channels.models  # noqa: F401
import shared.tenant.models  # noqa: F401
import shared.tenant_config.models  # noqa: F401

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


def check(label: str, condition: bool) -> None:
    print(f"  {'✓' if condition else '✗'} [{PASS if condition else FAIL}] {label}")
    if not condition:
        raise AssertionError(f"FAILED: {label}")


# ---------------------------------------------------------------------------
# Shared setup helpers
# ---------------------------------------------------------------------------


async def make_tenant(session: Any) -> Any:
    from shared.tenant import service as tenant_service
    from shared.tenant.schemas import BusinessType, TenantCreate

    return await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name=f"Compliance Test Co {uuid4().hex[:6]}",
            primary_contact_name="Admin",
            primary_contact_email=f"admin-{uuid4().hex[:6]}@comptest.example.com",
            business_type=BusinessType.B2C,
            website_url="https://comptest.example.com",  # type: ignore[arg-type]
        ),
    )


async def make_config(session: Any, tenant_id: UUID) -> None:
    from shared.tenant_config import service as config_service
    from shared.tenant_config.schemas import TenantConfigCreate

    await config_service.create_active(
        session,
        tenant_id,
        TenantConfigCreate.model_validate(
            {
                "business_profile": {"summary": "Compliance test"},
                "icp": {"summary": "Test ICP"},
                "signals": [
                    {"id": "fit_1", "dimension": "FIT", "question": "Fit?"},
                    {"id": "intent_1", "dimension": "INTENT", "question": "Intent?"},
                    {"id": "eng_1", "dimension": "ENGAGEMENT", "question": "Engaged?"},
                    {"id": "beh_1", "dimension": "BEHAVIOUR", "question": "Behaviour?"},
                    {"id": "ctx_1", "dimension": "CONTEXT", "question": "Context?"},
                ],
                "weights": {
                    "fit": 0.2,
                    "intent": 0.2,
                    "engagement": 0.2,
                    "behaviour": 0.2,
                    "context": 0.2,
                },
                "thresholds": {"hot": 80, "warm": 55},
            }
        ),
    )


# ---------------------------------------------------------------------------
# Test 1 + 2 — COMP-302 Retention sweep
# ---------------------------------------------------------------------------


async def test_retention_sweep() -> None:
    print("\n=== COMP-302: Retention sweep ===")

    from core.db import async_session_factory, engine
    from modules.lead_ingestion.db.models import IntakeEventLog, Lead, RetentionEventLog
    from modules.lead_ingestion.retention import run_retention_sweep

    async with async_session_factory() as session:
        tenant = await make_tenant(session)
        await session.commit()

        # Seed lead 1: aged > 730 days → should be anonymised
        aged_lead = Lead(
            id=uuid4(),
            tenant_id=tenant.id,
            pipeline_stage="captured",
            source_channel="file_upload",
            full_name="Old User",
            phone="+911111111111",
            email="old@example.com",
            location="Delhi",
            raw_event_json={"original": "aged_event"},
            created_at=datetime.now(UTC) - timedelta(days=731),
            updated_at=datetime.now(UTC) - timedelta(days=731),
        )
        session.add(aged_lead)

        # Seed lead 2: recent (100 days) → must NOT be touched
        recent_lead = Lead(
            id=uuid4(),
            tenant_id=tenant.id,
            pipeline_stage="captured",
            source_channel="file_upload",
            full_name="Recent User",
            phone="+912222222222",
            email="recent@example.com",
            location="Mumbai",
            raw_event_json={"original": "recent_event"},
            created_at=datetime.now(UTC) - timedelta(days=100),
            updated_at=datetime.now(UTC) - timedelta(days=100),
        )
        session.add(recent_lead)
        await session.flush()  # persist leads so FK constraint on intake_event_logs is satisfied

        # Seed IntakeEventLog for aged lead (> 180 days) → should be redacted
        aged_log = IntakeEventLog(
            id=uuid4(),
            lead_id=aged_lead.id,
            tenant_id=tenant.id,
            platform_event_id=f"manual-test-{uuid4().hex}",
            source_channel="file_upload",
            status="captured",
            raw_event_json={
                "phone": "+911111111111",
                "email": "old@example.com",
                "name": "Old User",
            },
            created_at=datetime.now(UTC) - timedelta(days=731),
        )
        session.add(aged_log)

        await session.commit()
        aged_lead_id = aged_lead.id
        recent_lead_id = recent_lead.id
        aged_log_id = aged_log.id

        # Run the sweep
        result = await run_retention_sweep(session)
        print(f"  sweep result: {result}")

        check("Aged lead count reported", result.get("leads_anonymised", 0) >= 1)
        check("Aged log count reported", result.get("event_logs_redacted", 0) >= 1)

        # Force DB round-trip
        session.expire_all()
        swept_lead = await session.get(Lead, aged_lead_id)
        safe_lead = await session.get(Lead, recent_lead_id)
        swept_log = await session.get(IntakeEventLog, aged_log_id)

        check(
            "Aged lead stage = anonymised",
            swept_lead is not None and swept_lead.pipeline_stage == "anonymised",
        )
        check("Aged lead phone is None", swept_lead is not None and swept_lead.phone is None)
        check("Aged lead email is None", swept_lead is not None and swept_lead.email is None)
        check(
            "Aged lead raw_event_json tombstoned",
            swept_lead is not None and swept_lead.raw_event_json.get("anonymised") is True,
        )
        check(
            "Recent lead untouched",
            safe_lead is not None and safe_lead.pipeline_stage == "captured",
        )
        check(
            "Recent lead phone intact", safe_lead is not None and safe_lead.phone == "+912222222222"
        )
        check(
            "Aged IntakeEventLog tombstoned",
            swept_log is not None and swept_log.raw_event_json.get("anonymised") is True,
        )

        from sqlalchemy import select

        retention_rows = (
            (
                await session.execute(
                    select(RetentionEventLog).where(RetentionEventLog.lead_id == aged_lead_id)
                )
            )
            .scalars()
            .all()
        )
        check("RetentionEventLog audit row written", len(retention_rows) == 1)
        check("RetentionEventLog action = anonymised", retention_rows[0].action == "anonymised")

    print("  [COMP-302 direct service: ALL PASS]")
    await engine.dispose()


# ---------------------------------------------------------------------------
# Test 3 — COMP-303 Erasure service (direct)
# ---------------------------------------------------------------------------


async def test_erasure_service() -> None:
    print("\n=== COMP-303: Erasure service (direct) ===")

    from sqlalchemy import select

    from core.db import async_session_factory, engine
    from modules.lead_ingestion.db.models import ErasureEventLog, Lead, LeadTouchpoint
    from modules.lead_ingestion.erasure import erase_lead_by_identity

    async with async_session_factory() as session:
        tenant = await make_tenant(session)
        await session.flush()
        tenant_id = tenant.id  # capture before any commit expires the object

        lead = Lead(
            id=uuid4(),
            tenant_id=tenant_id,
            pipeline_stage="captured",
            source_channel="file_upload",
            full_name="Erasure Test User",
            phone="+913333333333",
            email="erasure@example.com",
            location="Pune",
            raw_event_json={"name": "Erasure Test User", "phone": "+913333333333"},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(lead)

        tp = LeadTouchpoint(
            id=uuid4(),
            lead_id=lead.id,
            platform_event_id=None,
            source_channel="file_upload",
            raw_event_json={"touchpoint": "data", "phone": "+913333333333"},
            created_at=datetime.now(UTC),
        )
        session.add(tp)
        await session.commit()

        lead_id = lead.id
        tp_id = tp.id

        erased_ids, event = await erase_lead_by_identity(
            session, tenant_id, phone="+913333333333", requested_by="manual-test"
        )

        check("Returned erased_ids contains lead", lead_id in erased_ids)
        check("LeadErasureRequested event returned", event is not None)
        check(
            "Event identifier_type = phone", event is not None and event.identifier_type == "phone"
        )
        expected_hash = hashlib.sha256(b"+913333333333").hexdigest()
        check(
            "Event identifier_hash correct",
            event is not None and event.identifier_hash == expected_hash,
        )

        session.expire_all()
        erased = await session.get(Lead, lead_id)
        erased_tp = await session.get(LeadTouchpoint, tp_id)

        check("Lead stage = erased", erased is not None and erased.pipeline_stage == "erased")
        check("Lead phone is None", erased is not None and erased.phone is None)
        check("Lead email is None", erased is not None and erased.email is None)
        check("Lead full_name is None", erased is not None and erased.full_name is None)
        check(
            "Lead raw_event_json erased tombstone",
            erased is not None and erased.raw_event_json.get("erased") is True,
        )
        check(
            "Touchpoint raw_event_json erased tombstone",
            erased_tp is not None and erased_tp.raw_event_json.get("erased") is True,
        )

        rows = (
            (
                await session.execute(
                    select(ErasureEventLog).where(ErasureEventLog.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )
        check("ErasureEventLog row written", len(rows) == 1)
        check("ErasureEventLog identifier_hash = SHA-256", rows[0].identifier_hash == expected_hash)
        check(
            "ErasureEventLog never stores PII value", "+913333333333" not in rows[0].identifier_hash
        )

    print("  [COMP-303 direct service: ALL PASS]")
    await engine.dispose()


# ---------------------------------------------------------------------------
# Test 4 — COMP-303 Erasure endpoint (HTTP via ASGI + monkeypatched auth)
# ---------------------------------------------------------------------------


async def test_erasure_endpoint() -> None:
    print("\n=== COMP-303: Erasure endpoint (HTTP/ASGI) ===")

    from collections.abc import AsyncIterator

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

    import auth.token as token_module
    from auth.models import User
    from auth.schemas import Role
    from core.db import async_session_factory, engine, get_session
    from main import app
    from modules.lead_ingestion.db.models import Lead

    ns = "https://leadengine/"

    async with async_session_factory() as session:
        tenant = await make_tenant(session)
        await session.flush()
        tenant_id = tenant.id  # capture before commit

        _sub = f"auth0|ep-test-{uuid4().hex[:8]}"
        _email = f"ep-{uuid4().hex[:6]}@comptest.example.com"
        user = User(
            auth0_sub=_sub,
            email=_email,
            role=Role.TENANT,
            tenant_id=tenant_id,
        )
        session.add(user)

        lead = Lead(
            id=uuid4(),
            tenant_id=tenant_id,
            pipeline_stage="captured",
            source_channel="file_upload",
            full_name="HTTP Erase User",
            phone="+914444444444",
            email="httperase@example.com",
            location="Chennai",
            raw_event_json={"original": "endpoint_test"},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(lead)
        await session.commit()
        lead_id = lead.id

        def fake_verify(token: str) -> dict[str, Any]:
            if token == "test-token":
                return {
                    "sub": _sub,
                    f"{ns}email": _email,
                    f"{ns}role": "TENANT",
                }
            from core.exceptions import AuthenticationError

            raise AuthenticationError("bad token")

        async def _use_test_session() -> AsyncIterator[AsyncSession]:
            yield session

        with patch.object(token_module, "verify_token", fake_verify):
            app.dependency_overrides[get_session] = _use_test_session
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                # 4a — golden path: erases by phone
                resp = await ac.post(
                    "/channels/erasure-request",
                    headers={"Authorization": "Bearer test-token"},
                    json={"identifier_type": "phone", "identifier": "+914444444444"},
                )
                check("Golden path: 200 status", resp.status_code == 200)
                body = resp.json()
                check("erased_lead_count = 1", body.get("erased_lead_count") == 1)
                check("lead_id in erased_lead_ids", str(lead_id) in body.get("erased_lead_ids", []))
                check("event not None", body.get("event") is not None)
                check(
                    "event identifier_type = phone",
                    body.get("event", {}).get("identifier_type") == "phone",
                )

                # 4b — bad identifier_type → 400
                resp_bad = await ac.post(
                    "/channels/erasure-request",
                    headers={"Authorization": "Bearer test-token"},
                    json={"identifier_type": "ssn", "identifier": "123-45-6789"},
                )
                check("Bad identifier_type: 400 status", resp_bad.status_code == 400)
                check(
                    "400 detail mentions identifier_type",
                    "identifier_type" in resp_bad.json().get("detail", ""),
                )

                # 4c — no auth → 401
                resp_unauth = await ac.post(
                    "/channels/erasure-request",
                    json={"identifier_type": "phone", "identifier": "+914444444444"},
                )
                check("No auth: 401 status", resp_unauth.status_code == 401)

            app.dependency_overrides.clear()

        # Verify DB state after the endpoint call
        session.expire_all()
        erased = await session.get(Lead, lead_id)
        check(
            "DB: lead stage = erased after endpoint call",
            erased is not None and erased.pipeline_stage == "erased",
        )
        check(
            "DB: lead phone = None after endpoint call", erased is not None and erased.phone is None
        )

    print("  [COMP-303 endpoint: ALL PASS]")
    await engine.dispose()


# ---------------------------------------------------------------------------
# Test 5 — COMP-301 Data minimization: sensitive fields stripped from CSV upload
# ---------------------------------------------------------------------------


async def test_data_minimization() -> None:
    print("\n=== COMP-301: Data minimization (sensitive field strip) ===")

    import csv
    import io
    from collections.abc import AsyncIterator
    from unittest.mock import AsyncMock

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    import auth.token as token_module
    from auth.models import User
    from auth.schemas import Role
    from core.db import async_session_factory, engine, get_session
    from core.queue import get_arq_pool
    from main import app
    from modules.lead_ingestion.db.models import Lead

    ns = "https://leadengine/"

    async with async_session_factory() as session:
        tenant = await make_tenant(session)
        await session.flush()
        tenant_id = tenant.id  # capture before any commit expires the object

        await make_config(session, tenant_id)

        _min_sub = f"auth0|min-test-{uuid4().hex[:8]}"
        _min_email = f"min-{uuid4().hex[:6]}@comptest.example.com"
        user = User(
            auth0_sub=_min_sub,
            email=_min_email,
            role=Role.TENANT,
            tenant_id=tenant_id,
        )
        session.add(user)
        await session.commit()

        # CSV with an Aadhaar-format field (12 digits) in extra_fields
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["Contact Name", "Mobile", "Aadhaar Number"])
        writer.writeheader()
        writer.writerow(
            {
                "Contact Name": "PII Test User",
                "Mobile": "+915555555555",
                "Aadhaar Number": "123456789012",
            }
        )
        csv_bytes = buf.getvalue().encode()

        def fake_verify(token: str) -> dict[str, Any]:
            if token == "min-token":
                return {
                    "sub": _min_sub,
                    f"{ns}email": _min_email,
                    f"{ns}role": "TENANT",
                }
            from core.exceptions import AuthenticationError

            raise AuthenticationError("bad token")

        async def _use_test_session() -> AsyncIterator[AsyncSession]:
            yield session

        mock_pool = AsyncMock()
        mock_pool.enqueue_job = AsyncMock(return_value=None)

        with patch.object(token_module, "verify_token", fake_verify):
            app.dependency_overrides[get_session] = _use_test_session
            app.dependency_overrides[get_arq_pool] = lambda: mock_pool
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post(
                    "/channels/inbound/file-upload",
                    headers={"Authorization": "Bearer min-token"},
                    files={"file": ("leads.csv", csv_bytes, "text/csv")},
                )
                check("File upload: 200 status", resp.status_code == 200)
            app.dependency_overrides.clear()

        # Verify the Aadhaar field was stripped
        session.expire_all()
        leads = (
            (
                await session.execute(
                    select(Lead).where(Lead.tenant_id == tenant_id, Lead.phone == "+915555555555")
                )
            )
            .scalars()
            .all()
        )
        check("Lead created from CSV", len(leads) == 1)
        extra = leads[0].extra_fields or {}
        aadhaar_keys = [k for k in extra if "aadhaar" in k.lower()]
        check("Aadhaar field absent from extra_fields", len(aadhaar_keys) == 0)
        check("Aadhaar value not in extra_fields values", "123456789012" not in str(extra.values()))

    print("  [COMP-301 minimization: ALL PASS]")
    await engine.dispose()


# ---------------------------------------------------------------------------
# Test 6 — COMP-302 ARQ cron wiring: enqueue job so the live worker picks it up
# ---------------------------------------------------------------------------


async def test_arq_cron_enqueue() -> None:
    print("\n=== COMP-302: ARQ cron job enqueue (worker wiring) ===")

    from arq.connections import RedisSettings, create_pool

    from core.config import get_settings

    settings = get_settings()
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    job = await pool.enqueue_job("run_lead_retention_sweep")
    await pool.aclose()

    check("Job enqueued successfully (id not None)", job is not None)
    print(f"  Enqueued job id: {job.job_id if job else 'None'}")
    print("  → Check worker logs: docker logs enrichment-worker-1 --tail 20")
    print("  [COMP-302 ARQ enqueue: ALL PASS]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("\n" + "=" * 60)
    print("  COMPLIANCE MANUAL TEST SUITE")
    print("=" * 60)

    await test_retention_sweep()
    await test_erasure_service()
    await test_erasure_endpoint()
    await test_data_minimization()
    await test_arq_cron_enqueue()

    print("\n" + "=" * 60)
    print("  ALL COMPLIANCE TESTS PASSED")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
