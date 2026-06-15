# GST-OTP KYB Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate tenant onboarding on GST-OTP verification (via Surepass) so the scoring pipeline only runs for businesses that have proven control of an active GSTIN.

**Architecture:** A two-step OTP exchange (send → verify) sits in front of the existing onboarding pipeline. The tenant row is created up front in `KYB_PENDING`; the pipeline is enqueued only on `VERIFIED`. Surepass is isolated behind `clients/surepass_client.py`, which has a config-gated mock path so the whole flow runs with no API token. Orchestration lives in `modules/tenant_onboarding/kyb.py`; pure DB transitions live in `shared/tenant/service.py`; HTTP endpoints stay thin in `api/onboarding.py`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0 (async asyncpg), Alembic, ARQ, httpx, pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-06-15-gst-otp-kyb-design.md`

---

## File map

| File | Create/Modify | Responsibility |
|---|---|---|
| `core/config.py` | Modify | Add `surepass_api_key`, `surepass_base_url`, `surepass_use_mock`. |
| `shared/tenant/schemas.py` | Modify | `KybStatus` enum; GSTIN normaliser; `gstin` on `TenantCreate`; `kyb_status`/`gstin` on `TenantRead`. |
| `shared/tenant/models.py` | Modify | New tenant columns: `gstin`, `kyb_status`, `kyb_company_data`, `kyb_txn_ref`, `kyb_attempts`, `kyb_resends`, `kyb_verified_at`. |
| `migrations/versions/<rev>_add_kyb_to_tenants.py` | Create | Alembic migration for the new columns. |
| `clients/surepass_client.py` | Modify (stub→built) | `send_gst_otp`, `verify_gst_otp`, `CompanyData`; mock + live paths. |
| `shared/tenant/service.py` | Modify | `create_tenant` persists `gstin`/`kyb_status`; new KYB transition helpers. |
| `modules/tenant_onboarding/kyb.py` | Create | Orchestration: caps, pass/fail logic; composes client + service. |
| `api/onboarding.py` | Modify | `POST /onboarding` (no enqueue) + `verify-otp` / `resend-otp` / `restart-kyb`; enqueue on VERIFIED. |
| `tests/unit/test_config.py` | Modify | Assert new settings + defaults. |
| `tests/unit/test_tenant_schemas.py` | Modify | GSTIN validation, KybStatus, TenantRead fields. |
| `tests/unit/test_tenant_model.py` | Modify | New column defaults. |
| `tests/unit/test_surepass_client.py` | Create | Mock + live path unit tests. |
| `tests/unit/test_kyb.py` | Create | Orchestration branching. |
| `tests/integration/test_tenant_service.py` | Modify | KYB transition helpers against real DB. |
| `tests/integration/test_onboarding_endpoint.py` | Modify | Updated flow + new endpoints (mock Surepass). |
| `tests/integration/test_surepass_live.py` | Create | Single live test, skipped unless `SUREPASS_API_KEY` set. |

**Build order is mock-first:** the full flow is runnable and tested via the mock path (Tasks 1–9). The live HTTP body (Task 6) is written but only exercised by the token-gated test (Task 10).

---

## Task 1: Surepass config settings

**Files:**
- Modify: `core/config.py:23-24`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config.py`:

```python
def test_surepass_settings_have_defaults() -> None:
    from tests.helpers import build_settings

    settings = build_settings()
    assert settings.surepass_api_key == ""
    assert settings.surepass_base_url == "https://kyc-api.surepass.io"
    assert settings.surepass_use_mock is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config.py::test_surepass_settings_have_defaults -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'surepass_api_key'`

- [ ] **Step 3: Write minimal implementation**

In `core/config.py`, after the `serper_api_key` line (currently line 24), add:

```python
    surepass_api_key: str = ""
    surepass_base_url: str = "https://kyc-api.surepass.io"
    surepass_use_mock: bool = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config.py::test_surepass_settings_have_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/config.py tests/unit/test_config.py
git commit -m "feat: add Surepass config settings"
```

---

## Task 2: KybStatus enum + GSTIN validation in schemas

**Files:**
- Modify: `shared/tenant/schemas.py`
- Test: `tests/unit/test_tenant_schemas.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_tenant_schemas.py`:

```python
import pytest
from pydantic import ValidationError

from shared.tenant.schemas import KybStatus, TenantCreate

_VALID_GSTIN = "29ABCDE1234F1Z5"

_BASE = {
    "company_name": "Acme",
    "primary_contact_name": "Ada",
    "primary_contact_email": "ada@acme.com",
    "business_type": "B2B",
    "website_url": "https://acme.com",
}


def test_kyb_status_values() -> None:
    assert KybStatus.PENDING == "PENDING"
    assert KybStatus.VERIFIED == "VERIFIED"
    assert KybStatus.FAILED == "FAILED"


def test_tenant_create_accepts_valid_gstin() -> None:
    tc = TenantCreate(**_BASE, gstin=_VALID_GSTIN)
    assert tc.gstin == _VALID_GSTIN


def test_tenant_create_uppercases_and_strips_gstin() -> None:
    tc = TenantCreate(**_BASE, gstin=f"  {_VALID_GSTIN.lower()}  ")
    assert tc.gstin == _VALID_GSTIN


def test_tenant_create_rejects_malformed_gstin() -> None:
    with pytest.raises(ValidationError):
        TenantCreate(**_BASE, gstin="NOTAGSTIN")


def test_tenant_create_requires_gstin() -> None:
    with pytest.raises(ValidationError):
        TenantCreate(**_BASE)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_tenant_schemas.py -k "kyb or gstin" -v`
Expected: FAIL — `ImportError: cannot import name 'KybStatus'`

- [ ] **Step 3: Write minimal implementation**

In `shared/tenant/schemas.py`, add `re` to imports and `field_validator` to the pydantic import line:

```python
import re
```
```python
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr, field_validator
```

After the `OnboardingStatus` enum (around line 40), add:

```python
class KybStatus(StrEnum):
    """Whether the tenant has proven control of its GSTIN via GST-OTP."""

    PENDING  = "PENDING"   # OTP sent, not yet verified
    VERIFIED = "VERIFIED"  # OTP confirmed; pipeline may run
    FAILED   = "FAILED"    # too many wrong attempts; tenant may restart


GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def normalize_gstin(value: str) -> str:
    """Strip, uppercase, and validate a GSTIN. Raise ValueError if malformed."""
    candidate = value.strip().upper()
    if not GSTIN_PATTERN.match(candidate):
        raise ValueError("invalid GSTIN format")
    return candidate
```

In `TenantCreate`, add the field and validator (after `website_url`):

```python
    gstin: str

    @field_validator("gstin")
    @classmethod
    def _normalize_gstin(cls, value: str) -> str:
        return normalize_gstin(value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tenant_schemas.py -k "kyb or gstin" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/tenant/schemas.py tests/unit/test_tenant_schemas.py
git commit -m "feat: add KybStatus enum and GSTIN validation to tenant schemas"
```

---

## Task 3: TenantRead exposes kyb_status and gstin

**Files:**
- Modify: `shared/tenant/schemas.py` (`TenantRead`)
- Test: `tests/unit/test_tenant_schemas.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_tenant_schemas.py`:

```python
def test_tenant_read_exposes_kyb_fields() -> None:
    from shared.tenant.schemas import TenantRead

    fields = TenantRead.model_fields
    assert "kyb_status" in fields
    assert "gstin" in fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tenant_schemas.py::test_tenant_read_exposes_kyb_fields -v`
Expected: FAIL — assertion error, `kyb_status` not in fields

- [ ] **Step 3: Write minimal implementation**

In `TenantRead`, add after `business_type`:

```python
    gstin: str
```

and after `onboarding_status`:

```python
    kyb_status: KybStatus
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_tenant_schemas.py::test_tenant_read_exposes_kyb_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add shared/tenant/schemas.py tests/unit/test_tenant_schemas.py
git commit -m "feat: expose kyb_status and gstin on TenantRead"
```

---

## Task 4: Tenant model columns

**Files:**
- Modify: `shared/tenant/models.py`
- Test: `tests/unit/test_tenant_model.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_tenant_model.py`:

```python
def test_tenant_has_kyb_columns() -> None:
    from shared.tenant.models import Tenant

    cols = Tenant.__table__.columns
    for name in (
        "gstin",
        "kyb_status",
        "kyb_company_data",
        "kyb_txn_ref",
        "kyb_attempts",
        "kyb_resends",
        "kyb_verified_at",
    ):
        assert name in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tenant_model.py::test_tenant_has_kyb_columns -v`
Expected: FAIL — `gstin` not in columns

- [ ] **Step 3: Write minimal implementation**

In `shared/tenant/models.py`, update imports:

```python
from typing import Any
```
```python
from sqlalchemy import DateTime, Integer, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
```
```python
from shared.tenant.schemas import BusinessType, KybStatus, OnboardingStatus, TenantStatus
```

Add columns to `Tenant` (after `website_url`, before `onboarding_status`):

```python
    gstin: Mapped[str] = mapped_column(String, nullable=False)
    kyb_status: Mapped[KybStatus] = mapped_column(
        String, nullable=False, default=KybStatus.PENDING
    )
    kyb_company_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    kyb_txn_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    kyb_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kyb_resends: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kyb_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_tenant_model.py::test_tenant_has_kyb_columns -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add shared/tenant/models.py tests/unit/test_tenant_model.py
git commit -m "feat: add KYB columns to Tenant model"
```

---

## Task 5: Alembic migration for KYB columns

**Files:**
- Create: `migrations/versions/<rev>_add_kyb_to_tenants.py`
- Test: manual (`make migrate`)

- [ ] **Step 1: Autogenerate the migration**

Run: `uv run alembic revision -m "add kyb to tenants"`
This creates a new file under `migrations/versions/`. Open it and fill `upgrade`/`downgrade` as below (do not rely on autogenerate for column bodies — write them explicitly). Keep the generated `revision`/`down_revision` values.

```python
def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("gstin", sa.String(), nullable=False, server_default=""),
    )
    op.alter_column("tenants", "gstin", server_default=None)
    op.add_column(
        "tenants",
        sa.Column("kyb_status", sa.String(), nullable=False, server_default="PENDING"),
    )
    op.add_column(
        "tenants",
        sa.Column("kyb_company_data", postgresql.JSONB(), nullable=True),
    )
    op.add_column("tenants", sa.Column("kyb_txn_ref", sa.String(), nullable=True))
    op.add_column(
        "tenants",
        sa.Column("kyb_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tenants",
        sa.Column("kyb_resends", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tenants",
        sa.Column("kyb_verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "kyb_verified_at")
    op.drop_column("tenants", "kyb_resends")
    op.drop_column("tenants", "kyb_attempts")
    op.drop_column("tenants", "kyb_txn_ref")
    op.drop_column("tenants", "kyb_company_data")
    op.drop_column("tenants", "kyb_status")
    op.drop_column("tenants", "gstin")
```

Add the import at the top of the file (alongside the existing `import sqlalchemy as sa`):

```python
from sqlalchemy.dialects import postgresql
```

- [ ] **Step 2: Apply and verify**

Run: `make migrate`
Expected: `Running upgrade … -> <rev>, add kyb to tenants` with no errors.

Run: `uv run alembic downgrade -1 && make migrate`
Expected: clean down then up again (confirms `downgrade` works).

- [ ] **Step 3: Commit**

```bash
git add migrations/versions/
git commit -m "feat: migration adding KYB columns to tenants"
```

---

## Task 6: Surepass client (mock + live paths)

**Files:**
- Modify: `clients/surepass_client.py` (replace empty stub)
- Test: `tests/unit/test_surepass_client.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_surepass_client.py`:

```python
"""Unit tests for clients/surepass_client — mock path + live path (mocked httpx)."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.surepass_client import send_gst_otp, verify_gst_otp
from core.exceptions import ExternalServiceError
from tests.helpers import build_settings

_GSTIN = "29ABCDE1234F1Z5"


def _patch_settings(**overrides: Any) -> Any:
    return patch(
        "clients.surepass_client.get_settings",
        return_value=build_settings(**overrides),
    )


# --- mock path (no token) ---

async def test_mock_send_returns_txn_ref() -> None:
    with _patch_settings(surepass_use_mock=True):
        txn = await send_gst_otp(_GSTIN)
    assert isinstance(txn, str)
    assert txn != ""


async def test_mock_verify_accepts_dev_otp() -> None:
    with _patch_settings(surepass_use_mock=True):
        company = await verify_gst_otp("mock-txn", "123456")
    assert company is not None
    assert company["gstin"] == _GSTIN.replace(_GSTIN, company["gstin"])
    assert company["status"] == "Active"


async def test_mock_verify_rejects_wrong_otp() -> None:
    with _patch_settings(surepass_use_mock=True):
        company = await verify_gst_otp("mock-txn", "000000")
    assert company is None


# --- live path (mocked httpx) ---

def _resp(status: int, body: dict[str, Any]) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    return r


async def test_live_send_posts_and_returns_client_id() -> None:
    body = {"data": {"client_id": "abc-123"}}
    with _patch_settings(surepass_use_mock=False, surepass_api_key="k"):
        with patch("clients.surepass_client.httpx.AsyncClient") as mock_cls:
            mock_post = AsyncMock(return_value=_resp(200, body))
            mock_cls.return_value.__aenter__.return_value.post = mock_post
            txn = await send_gst_otp(_GSTIN)
    assert txn == "abc-123"


async def test_live_verify_returns_company_on_success() -> None:
    body = {
        "data": {
            "gstin": _GSTIN,
            "legal_name": "ACME PRIVATE LIMITED",
            "trade_name": "Acme",
            "status": "Active",
            "address": "1 Road, City",
        }
    }
    with _patch_settings(surepass_use_mock=False, surepass_api_key="k"):
        with patch("clients.surepass_client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=_resp(200, body)
            )
            company = await verify_gst_otp("abc-123", "111111")
    assert company is not None
    assert company["legal_name"] == "ACME PRIVATE LIMITED"


async def test_live_verify_returns_none_on_otp_rejection() -> None:
    with _patch_settings(surepass_use_mock=False, surepass_api_key="k"):
        with patch("clients.surepass_client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=_resp(422, {"message": "invalid otp"})
            )
            company = await verify_gst_otp("abc-123", "000000")
    assert company is None


async def test_live_send_raises_on_network_failure() -> None:
    with _patch_settings(surepass_use_mock=False, surepass_api_key="k"):
        with patch("clients.surepass_client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("connection refused")
            )
            with pytest.raises(ExternalServiceError, match="Surepass"):
                await send_gst_otp(_GSTIN)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_surepass_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'send_gst_otp'`

- [ ] **Step 3: Write minimal implementation**

Replace the contents of `clients/surepass_client.py`:

```python
"""Thin async wrapper around Surepass GST-verification-with-OTP.

The only file in the project that talks to Surepass. Two calls:
`send_gst_otp` triggers an OTP to the GSTIN's GST-registered contact and returns
a transaction reference; `verify_gst_otp` submits that reference plus the OTP and
returns the verified company record (or None if the OTP was rejected).

Surepass's exact request/response contract is provisional pending API docs; when
the real contract is known, only the live branches below change. A config-gated
mock path (`surepass_use_mock`) lets the full onboarding flow run with no token.
"""

from typing import Any, TypedDict

import httpx

from core.config import get_settings
from core.exceptions import ExternalServiceError

_SEND_PATH = "/api/v1/corporate/gstin-otp"
_VERIFY_PATH = "/api/v1/corporate/gstin-otp-verify"
_DEV_OTP = "123456"


class CompanyData(TypedDict):
    """The verified GST record Surepass returns on a successful OTP."""

    gstin: str
    legal_name: str
    trade_name: str
    status: str
    address: str


def _mock_company(gstin: str) -> CompanyData:
    return CompanyData(
        gstin=gstin,
        legal_name="MOCK PRIVATE LIMITED",
        trade_name="Mock",
        status="Active",
        address="1 Mock Street, Test City",
    )


async def send_gst_otp(gstin: str) -> str:
    """Trigger a GST OTP and return the transaction reference.

    Raises ExternalServiceError on transport/API failure.
    """
    settings = get_settings()
    if settings.surepass_use_mock:
        return f"mock-txn-{gstin}"

    headers = {
        "Authorization": f"Bearer {settings.surepass_api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {"id_number": gstin}
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            response = await http.post(
                f"{settings.surepass_base_url}{_SEND_PATH}",
                headers=headers,
                json=payload,
            )
    except Exception as exc:
        raise ExternalServiceError(f"Surepass send_gst_otp failed: {exc}") from exc

    if response.status_code != 200:
        raise ExternalServiceError(f"Surepass send returned {response.status_code}")

    data: dict[str, Any] = response.json()
    client_id = data.get("data", {}).get("client_id")
    if not client_id:
        raise ExternalServiceError("Surepass send returned no client_id")
    return str(client_id)


async def verify_gst_otp(txn_ref: str, otp: str) -> CompanyData | None:
    """Submit the OTP. Return the verified company on success, None if rejected.

    Raises ExternalServiceError on transport failure (not on OTP rejection).
    """
    settings = get_settings()
    if settings.surepass_use_mock:
        if otp != _DEV_OTP:
            return None
        gstin = txn_ref.removeprefix("mock-txn-")
        return _mock_company(gstin)

    headers = {
        "Authorization": f"Bearer {settings.surepass_api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {"client_id": txn_ref, "otp": otp}
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            response = await http.post(
                f"{settings.surepass_base_url}{_VERIFY_PATH}",
                headers=headers,
                json=payload,
            )
    except Exception as exc:
        raise ExternalServiceError(f"Surepass verify_gst_otp failed: {exc}") from exc

    if response.status_code != 200:
        # Surepass returns a 4xx when the OTP is wrong/expired — a domain outcome.
        return None

    record: dict[str, Any] = response.json().get("data", {})
    return CompanyData(
        gstin=str(record.get("gstin", "")),
        legal_name=str(record.get("legal_name", "")),
        trade_name=str(record.get("trade_name", "")),
        status=str(record.get("status", "")),
        address=str(record.get("address", "")),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_surepass_client.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add clients/surepass_client.py tests/unit/test_surepass_client.py
git commit -m "feat: surepass_client with GST-OTP send/verify and mock path"
```

---

## Task 7: Tenant service — create with gstin + KYB transition helpers

**Files:**
- Modify: `shared/tenant/service.py`
- Test: `tests/integration/test_tenant_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/integration/test_tenant_service.py` (use the existing `session` fixture and whatever `TenantCreate` builder the file already uses; the snippet below builds one inline):

```python
from shared.tenant.schemas import KybStatus, TenantCreate
from shared.tenant.service import (
    bump_kyb_attempts,
    bump_kyb_resends,
    create_tenant,
    get_tenant,
    mark_kyb_failed,
    mark_kyb_verified,
    reset_kyb,
    store_kyb_txn,
)

_VALID_GSTIN = "29ABCDE1234F1Z5"


def _tenant_create(gstin: str = _VALID_GSTIN) -> TenantCreate:
    return TenantCreate(
        company_name="Acme",
        primary_contact_name="Ada",
        primary_contact_email="ada@acme.com",
        business_type="B2B",
        website_url="https://acme.com",
        gstin=gstin,
    )


async def test_create_tenant_persists_gstin_and_pending_kyb(session) -> None:
    tenant = await create_tenant(session, _tenant_create())
    assert tenant.gstin == _VALID_GSTIN
    assert tenant.kyb_status == KybStatus.PENDING
    assert tenant.kyb_attempts == 0


async def test_store_kyb_txn(session) -> None:
    tenant = await create_tenant(session, _tenant_create())
    await store_kyb_txn(session, tenant.id, "txn-1")
    refreshed = await get_tenant(session, tenant.id)
    assert refreshed.kyb_txn_ref == "txn-1"


async def test_mark_kyb_verified(session) -> None:
    tenant = await create_tenant(session, _tenant_create())
    await store_kyb_txn(session, tenant.id, "txn-1")
    company = {"gstin": _VALID_GSTIN, "legal_name": "ACME PRIVATE LIMITED"}
    await mark_kyb_verified(session, tenant.id, company)
    refreshed = await get_tenant(session, tenant.id)
    assert refreshed.kyb_status == KybStatus.VERIFIED
    assert refreshed.kyb_company_data["legal_name"] == "ACME PRIVATE LIMITED"
    assert refreshed.kyb_verified_at is not None
    assert refreshed.kyb_txn_ref is None


async def test_bump_kyb_attempts_returns_count(session) -> None:
    tenant = await create_tenant(session, _tenant_create())
    assert await bump_kyb_attempts(session, tenant.id) == 1
    assert await bump_kyb_attempts(session, tenant.id) == 2


async def test_mark_kyb_failed_clears_txn(session) -> None:
    tenant = await create_tenant(session, _tenant_create())
    await store_kyb_txn(session, tenant.id, "txn-1")
    await mark_kyb_failed(session, tenant.id)
    refreshed = await get_tenant(session, tenant.id)
    assert refreshed.kyb_status == KybStatus.FAILED
    assert refreshed.kyb_txn_ref is None


async def test_reset_kyb_clears_counters_and_sets_gstin(session) -> None:
    tenant = await create_tenant(session, _tenant_create())
    await bump_kyb_attempts(session, tenant.id)
    await bump_kyb_resends(session, tenant.id)
    await mark_kyb_failed(session, tenant.id)
    new_gstin = "27AAAAA0000A1Z5"
    await reset_kyb(session, tenant.id, new_gstin)
    refreshed = await get_tenant(session, tenant.id)
    assert refreshed.kyb_status == KybStatus.PENDING
    assert refreshed.kyb_attempts == 0
    assert refreshed.kyb_resends == 0
    assert refreshed.gstin == new_gstin
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_tenant_service.py -k kyb -v`
Expected: FAIL — `ImportError: cannot import name 'store_kyb_txn'` (and `create_tenant` rejects the new `gstin` arg path until updated)

- [ ] **Step 3: Write minimal implementation**

In `shared/tenant/service.py`, update imports:

```python
from typing import Any
```
```python
from shared.tenant.schemas import KybStatus, OnboardingStatus, TenantCreate, TenantStatus
```

In `create_tenant`, add `gstin` + `kyb_status` to the `Tenant(...)` constructor:

```python
        website_url=str(data.website_url),
        gstin=data.gstin,
        kyb_status=KybStatus.PENDING,
        timezone=data.timezone,
```

Append the KYB helpers at the end of the file:

```python
async def store_kyb_txn(session: AsyncSession, tenant_id: UUID, txn_ref: str) -> None:
    """Persist the Surepass transaction reference for the pending OTP."""
    tenant = await get_tenant(session, tenant_id)
    tenant.kyb_txn_ref = txn_ref
    await session.commit()


async def mark_kyb_verified(
    session: AsyncSession, tenant_id: UUID, company_data: dict[str, Any]
) -> None:
    """Record a successful KYB: store company data, set VERIFIED, clear the txn ref."""
    tenant = await get_tenant(session, tenant_id)
    tenant.kyb_status = KybStatus.VERIFIED
    tenant.kyb_company_data = company_data
    tenant.kyb_verified_at = datetime.now(UTC)
    tenant.kyb_txn_ref = None
    await session.commit()


async def bump_kyb_attempts(session: AsyncSession, tenant_id: UUID) -> int:
    """Increment the wrong-OTP attempt counter and return the new value."""
    tenant = await get_tenant(session, tenant_id)
    tenant.kyb_attempts += 1
    await session.commit()
    return tenant.kyb_attempts


async def bump_kyb_resends(session: AsyncSession, tenant_id: UUID) -> int:
    """Increment the OTP resend counter and return the new value."""
    tenant = await get_tenant(session, tenant_id)
    tenant.kyb_resends += 1
    await session.commit()
    return tenant.kyb_resends


async def mark_kyb_failed(session: AsyncSession, tenant_id: UUID) -> None:
    """Set KYB to FAILED and clear the txn ref."""
    tenant = await get_tenant(session, tenant_id)
    tenant.kyb_status = KybStatus.FAILED
    tenant.kyb_txn_ref = None
    await session.commit()


async def reset_kyb(
    session: AsyncSession, tenant_id: UUID, gstin: str | None = None
) -> None:
    """Reset a FAILED tenant to PENDING, clear counters, optionally swap the GSTIN."""
    tenant = await get_tenant(session, tenant_id)
    tenant.kyb_status = KybStatus.PENDING
    tenant.kyb_attempts = 0
    tenant.kyb_resends = 0
    tenant.kyb_txn_ref = None
    if gstin is not None:
        tenant.gstin = gstin
    await session.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_tenant_service.py -k kyb -v`
Expected: PASS (6 tests)

> If existing tests in this file build `TenantCreate` without `gstin`, add `gstin="29ABCDE1234F1Z5"` to those builders so they still validate.

- [ ] **Step 5: Commit**

```bash
git add shared/tenant/service.py tests/integration/test_tenant_service.py
git commit -m "feat: tenant service KYB transitions and gstin persistence"
```

---

## Task 8: KYB orchestration module

**Files:**
- Create: `modules/tenant_onboarding/kyb.py`
- Test: `tests/unit/test_kyb.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_kyb.py`:

```python
"""Unit tests for KYB orchestration — service + Surepass client are mocked."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from core.exceptions import ConflictError
from modules.tenant_onboarding import kyb
from shared.tenant.schemas import KybStatus

_GSTIN = "29ABCDE1234F1Z5"


def _tenant(**over: object) -> SimpleNamespace:
    base = dict(
        gstin=_GSTIN,
        kyb_status=KybStatus.PENDING,
        kyb_txn_ref="txn-1",
        kyb_attempts=0,
        kyb_resends=0,
    )
    base.update(over)
    return SimpleNamespace(**base)


async def test_start_verification_sends_and_stores_txn() -> None:
    session = AsyncMock()
    tid = uuid4()
    with (
        patch.object(kyb.tenant_service, "get_tenant", AsyncMock(return_value=_tenant())),
        patch.object(kyb.surepass_client, "send_gst_otp", AsyncMock(return_value="txn-9")),
        patch.object(kyb.tenant_service, "store_kyb_txn", AsyncMock()) as store,
    ):
        await kyb.start_verification(session, tid)
    store.assert_awaited_once_with(session, tid, "txn-9")


async def test_submit_otp_verified() -> None:
    session = AsyncMock()
    tid = uuid4()
    company = {"gstin": _GSTIN, "legal_name": "ACME"}
    with (
        patch.object(kyb.tenant_service, "get_tenant", AsyncMock(return_value=_tenant())),
        patch.object(kyb.surepass_client, "verify_gst_otp", AsyncMock(return_value=company)),
        patch.object(kyb.tenant_service, "mark_kyb_verified", AsyncMock()) as verified,
    ):
        result = await kyb.submit_otp(session, tid, "123456")
    assert result == KybStatus.VERIFIED
    verified.assert_awaited_once_with(session, tid, company)


async def test_submit_otp_wrong_under_cap_stays_pending() -> None:
    session = AsyncMock()
    tid = uuid4()
    with (
        patch.object(kyb.tenant_service, "get_tenant", AsyncMock(return_value=_tenant())),
        patch.object(kyb.surepass_client, "verify_gst_otp", AsyncMock(return_value=None)),
        patch.object(kyb.tenant_service, "bump_kyb_attempts", AsyncMock(return_value=1)),
        patch.object(kyb.tenant_service, "mark_kyb_failed", AsyncMock()) as failed,
    ):
        result = await kyb.submit_otp(session, tid, "000000")
    assert result == KybStatus.PENDING
    failed.assert_not_awaited()


async def test_submit_otp_wrong_at_cap_fails() -> None:
    session = AsyncMock()
    tid = uuid4()
    with (
        patch.object(kyb.tenant_service, "get_tenant", AsyncMock(return_value=_tenant())),
        patch.object(kyb.surepass_client, "verify_gst_otp", AsyncMock(return_value=None)),
        patch.object(kyb.tenant_service, "bump_kyb_attempts", AsyncMock(return_value=3)),
        patch.object(kyb.tenant_service, "mark_kyb_failed", AsyncMock()) as failed,
    ):
        result = await kyb.submit_otp(session, tid, "000000")
    assert result == KybStatus.FAILED
    failed.assert_awaited_once_with(session, tid)


async def test_submit_otp_not_pending_conflicts() -> None:
    session = AsyncMock()
    with patch.object(
        kyb.tenant_service, "get_tenant",
        AsyncMock(return_value=_tenant(kyb_status=KybStatus.VERIFIED)),
    ):
        with pytest.raises(ConflictError):
            await kyb.submit_otp(session, uuid4(), "123456")


async def test_resend_over_cap_conflicts() -> None:
    session = AsyncMock()
    with patch.object(
        kyb.tenant_service, "get_tenant",
        AsyncMock(return_value=_tenant(kyb_resends=3)),
    ):
        with pytest.raises(ConflictError):
            await kyb.resend_otp(session, uuid4())


async def test_restart_requires_failed_status() -> None:
    session = AsyncMock()
    with patch.object(
        kyb.tenant_service, "get_tenant",
        AsyncMock(return_value=_tenant(kyb_status=KybStatus.PENDING)),
    ):
        with pytest.raises(ConflictError):
            await kyb.restart_kyb(session, uuid4(), None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_kyb.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.tenant_onboarding.kyb'`

- [ ] **Step 3: Write minimal implementation**

Create `modules/tenant_onboarding/kyb.py`:

```python
"""KYB orchestration: GST-OTP send/verify/resend/restart for tenant onboarding.

Composes the Surepass client (network) with tenant service (DB state). Owns the
business rules — attempt and resend caps, and what counts as pass/fail. Does NOT
enqueue the pipeline; the api layer does that on a VERIFIED result.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from clients import surepass_client
from core.exceptions import ConflictError
from shared.tenant import service as tenant_service
from shared.tenant.schemas import KybStatus

MAX_ATTEMPTS = 3
MAX_RESENDS = 3


async def start_verification(session: AsyncSession, tenant_id: UUID) -> None:
    """Send the first OTP for a freshly created (PENDING) tenant."""
    tenant = await tenant_service.get_tenant(session, tenant_id)
    txn_ref = await surepass_client.send_gst_otp(tenant.gstin)
    await tenant_service.store_kyb_txn(session, tenant_id, txn_ref)


async def submit_otp(session: AsyncSession, tenant_id: UUID, otp: str) -> KybStatus:
    """Verify an OTP. Returns the resulting KybStatus.

    VERIFIED on success; PENDING while attempts remain; FAILED at the cap.
    """
    tenant = await tenant_service.get_tenant(session, tenant_id)
    if tenant.kyb_status is not KybStatus.PENDING:
        raise ConflictError(f"Tenant {tenant_id} KYB is not pending")
    if tenant.kyb_txn_ref is None:
        raise ConflictError(f"Tenant {tenant_id} has no active OTP")

    company = await surepass_client.verify_gst_otp(tenant.kyb_txn_ref, otp)
    if company is not None:
        await tenant_service.mark_kyb_verified(session, tenant_id, dict(company))
        return KybStatus.VERIFIED

    attempts = await tenant_service.bump_kyb_attempts(session, tenant_id)
    if attempts >= MAX_ATTEMPTS:
        await tenant_service.mark_kyb_failed(session, tenant_id)
        return KybStatus.FAILED
    return KybStatus.PENDING


async def resend_otp(session: AsyncSession, tenant_id: UUID) -> None:
    """Send a fresh OTP for the same GSTIN, capped at MAX_RESENDS."""
    tenant = await tenant_service.get_tenant(session, tenant_id)
    if tenant.kyb_status is not KybStatus.PENDING:
        raise ConflictError(f"Tenant {tenant_id} KYB is not pending")
    if tenant.kyb_resends >= MAX_RESENDS:
        raise ConflictError(f"Tenant {tenant_id} OTP resend limit reached")
    txn_ref = await surepass_client.send_gst_otp(tenant.gstin)
    await tenant_service.store_kyb_txn(session, tenant_id, txn_ref)
    await tenant_service.bump_kyb_resends(session, tenant_id)


async def restart_kyb(
    session: AsyncSession, tenant_id: UUID, gstin: str | None
) -> None:
    """Restart KYB for a FAILED tenant: reset counters, optional new GSTIN, fresh OTP."""
    tenant = await tenant_service.get_tenant(session, tenant_id)
    if tenant.kyb_status is not KybStatus.FAILED:
        raise ConflictError(f"Tenant {tenant_id} KYB is not failed")
    await tenant_service.reset_kyb(session, tenant_id, gstin)
    refreshed = await tenant_service.get_tenant(session, tenant_id)
    txn_ref = await surepass_client.send_gst_otp(refreshed.gstin)
    await tenant_service.store_kyb_txn(session, tenant_id, txn_ref)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_kyb.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add modules/tenant_onboarding/kyb.py tests/unit/test_kyb.py
git commit -m "feat: KYB orchestration with attempt/resend caps"
```

---

## Task 9: API endpoints — gate onboarding on KYB

**Files:**
- Modify: `api/onboarding.py`
- Test: `tests/integration/test_onboarding_endpoint.py`

- [ ] **Step 1: Update existing tests + add new flow tests**

In `tests/integration/test_onboarding_endpoint.py`:

(a) Add `gstin` to `_BUSINESS`:

```python
_BUSINESS = {
    "company_name": "Acme",
    "primary_contact_name": "Ada",
    "primary_contact_email": "ada@acme.com",
    "business_type": "B2B",
    "website_url": "https://acme.com",
    "gstin": "29ABCDE1234F1Z5",
    "timezone": "UTC",
    "language_preference": "en",
}
```

(b) Replace `test_onboarding_enqueues_pipeline_job` — onboarding must NOT enqueue now:

```python
async def test_onboarding_does_not_enqueue_before_verification(client: AsyncClient) -> None:
    mock_pool = app.dependency_overrides[get_arq_pool]()
    resp = await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert resp.status_code == 200
    assert resp.json()["kyb_status"] == "PENDING"
    mock_pool.enqueue_job.assert_not_awaited()
```

(c) Add the verify happy-path + retry/restart tests (mock Surepass is on by default, dev OTP `123456`):

```python
async def test_verify_otp_verifies_and_enqueues_pipeline(client: AsyncClient) -> None:
    mock_pool = app.dependency_overrides[get_arq_pool]()
    await client.post("/onboarding", headers=_auth(), json=_BUSINESS)

    resp = await client.post(
        "/onboarding/verify-otp", headers=_auth(), json={"otp": "123456"}
    )
    assert resp.status_code == 200
    assert resp.json()["kyb_status"] == "VERIFIED"
    mock_pool.enqueue_job.assert_awaited_once()
    assert mock_pool.enqueue_job.call_args.args[0] == "run_onboarding_pipeline"


async def test_wrong_otp_three_times_fails_then_restart(client: AsyncClient) -> None:
    await client.post("/onboarding", headers=_auth(), json=_BUSINESS)

    for _ in range(3):
        bad = await client.post(
            "/onboarding/verify-otp", headers=_auth(), json={"otp": "000000"}
        )
        assert bad.status_code == 200
    assert bad.json()["kyb_status"] == "FAILED"

    restart = await client.post("/onboarding/restart-kyb", headers=_auth(), json={})
    assert restart.status_code == 200
    assert restart.json()["kyb_status"] == "PENDING"

    ok = await client.post(
        "/onboarding/verify-otp", headers=_auth(), json={"otp": "123456"}
    )
    assert ok.json()["kyb_status"] == "VERIFIED"


async def test_resend_otp_returns_pending(client: AsyncClient) -> None:
    await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    resp = await client.post("/onboarding/resend-otp", headers=_auth(), json={})
    assert resp.status_code == 200
    assert resp.json()["kyb_status"] == "PENDING"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_onboarding_endpoint.py -v`
Expected: FAIL — `/onboarding/verify-otp` returns 404 (route missing); `kyb_status` missing from `/onboarding` response

- [ ] **Step 3: Write minimal implementation**

Replace `api/onboarding.py` with:

```python
"""The /onboarding endpoints — create tenant, run GST-OTP KYB, then start pipeline."""

from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.models import User
from auth.service import set_user_tenant
from core.db import get_session
from core.exceptions import ConflictError
from core.queue import get_arq_pool
from modules.tenant_onboarding import kyb
from shared.tenant.schemas import KybStatus, TenantCreate, TenantRead, normalize_gstin
from shared.tenant.service import create_tenant, get_tenant

router = APIRouter()


class OtpVerifyRequest(BaseModel):
    otp: str


class RestartKybRequest(BaseModel):
    gstin: str | None = None

    @field_validator("gstin")
    @classmethod
    def _normalize(cls, value: str | None) -> str | None:
        return None if value is None else normalize_gstin(value)


@router.post("/onboarding", response_model=TenantRead)
async def onboard(
    data: TenantCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantRead:
    """Create the tenant in KYB_PENDING and send the first GST OTP. No pipeline yet."""
    if user.tenant_id is not None:
        raise ConflictError("User is already onboarded to a tenant")
    tenant = await create_tenant(session, data)
    await set_user_tenant(session, user, tenant.id)
    await kyb.start_verification(session, tenant.id)
    refreshed = await get_tenant(session, tenant.id)
    return TenantRead.model_validate(refreshed)


@router.post("/onboarding/verify-otp", response_model=TenantRead)
async def verify_otp(
    body: OtpVerifyRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> TenantRead:
    """Verify the OTP; on success, enqueue the onboarding pipeline."""
    tenant_id = _require_tenant(user)
    result = await kyb.submit_otp(session, tenant_id, body.otp)
    if result is KybStatus.VERIFIED:
        await arq_pool.enqueue_job("run_onboarding_pipeline", tenant_id=str(tenant_id))
    refreshed = await get_tenant(session, tenant_id)
    return TenantRead.model_validate(refreshed)


@router.post("/onboarding/resend-otp", response_model=TenantRead)
async def resend_otp(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantRead:
    """Send a fresh OTP for the same GSTIN (capped)."""
    tenant_id = _require_tenant(user)
    await kyb.resend_otp(session, tenant_id)
    refreshed = await get_tenant(session, tenant_id)
    return TenantRead.model_validate(refreshed)


@router.post("/onboarding/restart-kyb", response_model=TenantRead)
async def restart_kyb(
    body: RestartKybRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantRead:
    """Restart KYB for a FAILED tenant, optionally with a corrected GSTIN."""
    tenant_id = _require_tenant(user)
    await kyb.restart_kyb(session, tenant_id, body.gstin)
    refreshed = await get_tenant(session, tenant_id)
    return TenantRead.model_validate(refreshed)


def _require_tenant(user: User) -> "UUID":  # noqa: F821
    if user.tenant_id is None:
        raise ConflictError("User has no tenant to verify")
    return user.tenant_id
```

Add the `UUID` import at the top with the other imports:

```python
from uuid import UUID
```

and change the `_require_tenant` annotation to plain `UUID` (drop the string + noqa):

```python
def _require_tenant(user: User) -> UUID:
    if user.tenant_id is None:
        raise ConflictError("User has no tenant to verify")
    return user.tenant_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_onboarding_endpoint.py -v`
Expected: PASS (existing tests + the new flow tests)

- [ ] **Step 5: Commit**

```bash
git add api/onboarding.py tests/integration/test_onboarding_endpoint.py
git commit -m "feat: gate onboarding on GST-OTP KYB before pipeline enqueue"
```

---

## Task 10: Live Surepass integration test (token-gated, skipped)

**Files:**
- Create: `tests/integration/test_surepass_live.py`

- [ ] **Step 1: Write the test (skipped unless token present)**

Create `tests/integration/test_surepass_live.py`:

```python
"""Live Surepass GST-OTP test. Skipped unless SUREPASS_API_KEY is set.

This is the only test that hits the real Surepass API. It stays skipped until
credentials are available; run it once to confirm the provisional contract in
clients/surepass_client.py matches reality, then update that file if needed.
"""

import os

import pytest

from clients.surepass_client import send_gst_otp

pytestmark = pytest.mark.skipif(
    not os.getenv("SUREPASS_API_KEY"),
    reason="SUREPASS_API_KEY not set; live Surepass test skipped",
)

# A GSTIN you control, whose registered contact can receive the OTP.
_LIVE_GSTIN = os.getenv("SUREPASS_TEST_GSTIN", "")


async def test_live_send_gst_otp_returns_txn_ref() -> None:
    assert _LIVE_GSTIN, "set SUREPASS_TEST_GSTIN to run this test"
    # Requires surepass_use_mock=False in the environment.
    txn = await send_gst_otp(_LIVE_GSTIN)
    assert isinstance(txn, str) and txn
```

- [ ] **Step 2: Run to verify it is skipped**

Run: `uv run pytest tests/integration/test_surepass_live.py -v`
Expected: SKIPPED (`SUREPASS_API_KEY not set`)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_surepass_live.py
git commit -m "test: token-gated live Surepass integration test"
```

---

## Task 11: Full gate — lint, types, tests

**Files:** none (verification only)

- [ ] **Step 1: Run the full local gate**

Run: `make ci`
Expected: `ruff check` clean, `mypy .` clean (strict, whole repo incl. tests), full pytest suite green.

Fix anything that fails (common: a `TenantCreate(...)` somewhere in tests still missing `gstin`; a missing type annotation on a new helper).

- [ ] **Step 2: Commit any fixes**

```bash
git add -A
git commit -m "chore: satisfy lint/types/tests for KYB gate"
```

---

## Self-review notes

- **Spec coverage:** Option A flow (Tasks 7, 9) · `KybStatus` enum (Task 2) · GSTIN validation (Task 2) · model columns + migration (Tasks 4, 5) · Surepass client send/verify + mock path (Task 6) · orchestration with 3-attempt / 3-resend caps (Task 8) · four endpoints + enqueue-on-VERIFIED (Task 9) · OTP-alone gating, company data stored (Tasks 6, 7) · mock-default config (Task 1) · integration on mock, live test skipped (Tasks 9, 10). Deferred items (name-matching, sweeper, persona wiring, audit log) are intentionally absent.
- **Provisional contract:** the live request/response field names in Task 6 (`id_number`, `client_id`, `data.*`) are best-guess pending Surepass docs; Task 10 is where they get confirmed. Only `clients/surepass_client.py` changes if they differ.
