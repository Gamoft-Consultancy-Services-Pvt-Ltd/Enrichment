"""Unit test: /health degraded response must not leak raw exception details.

CodeQL py/stack-trace-exposure: exception strings from DB/Redis failures can
carry internal connection info and must never reach an unauthenticated caller.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

import main

_DB_SECRET = "SECRET_DB_HOST=db.internal:5432"
_REDIS_SECRET = "SECRET_REDIS_AUTH=hunter2"


class _FailingSession:
    async def __aenter__(self) -> "_FailingSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(_DB_SECRET)


class _FailingPool:
    async def ping(self) -> None:
        raise RuntimeError(_REDIS_SECRET)


def test_health_degraded_does_not_leak_exception_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "async_session_factory", lambda: _FailingSession())
    monkeypatch.setattr(main.app.state, "arq_pool", _FailingPool(), raising=False)

    # Not used as a context manager: lifespan (real Redis/DB init) never runs.
    resp = TestClient(main.app).get("/health")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    # Component names are still reported so operators know what is down...
    assert set(body["errors"]) == {"db", "redis"}
    # ...but the raw exception text must never be exposed to the caller.
    assert _DB_SECRET not in resp.text
    assert _REDIS_SECRET not in resp.text
