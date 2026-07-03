"""Integration tests for GET /me — TestClient with token verification patched."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth.token as token_module
from auth.models import User
from main import app

NS = "https://leadengine/"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient whose token verification is stubbed by the Bearer string."""

    def fake_verify(token: str) -> dict[str, Any]:
        if token == "admin-token":
            return {"sub": "auth0|admin", f"{NS}email": "ops@us.com", f"{NS}role": "PLATFORM_ADMIN"}
        from core.exceptions import AuthenticationError

        raise AuthenticationError("bad token")

    monkeypatch.setattr(token_module, "verify_token", fake_verify)
    return TestClient(app)


async def test_me_with_valid_token_returns_user_and_persists(
    client: TestClient, session: AsyncSession
) -> None:
    resp = client.get("/me", headers={"Authorization": "Bearer admin-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "ops@us.com"
    assert body["role"] == "PLATFORM_ADMIN"
    assert body["tenant_id"] is None

    rows = (
        (await session.execute(select(User).where(User.auth0_sub == "auth0|admin"))).scalars().all()
    )
    assert len(rows) == 1


def test_me_without_token_is_401(client: TestClient) -> None:
    resp = client.get("/me")
    assert resp.status_code == 401


def test_me_with_invalid_token_is_401(client: TestClient) -> None:
    resp = client.get("/me", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401
