"""Tests for core.logging — structured logging setup."""

import json

from _pytest.capture import CaptureFixture

from core.logging import configure_logging, get_logger
from tests.helpers import build_settings

_PROD_SECRETS = {
    "env": "production",
    "groq_api_key": "gsk_test",
    "channel_credentials_encryption_key": "key",
    "meta_app_secret": "secret",
    "auth0_domain": "acme.us.auth0.com",
    "auth0_audience": "api://leadengine",
    "meta_webhook_verify_token": "token",
    "auth0_mgmt_client_id": "mgmt_client_id",
    "auth0_mgmt_client_secret": "mgmt_client_secret",
}


def _last_line(text: str) -> str:
    """Return the last non-empty line of captured output."""
    return [line for line in text.splitlines() if line.strip()][-1]


def test_production_logs_are_json(capsys: CaptureFixture[str]) -> None:
    """In production, each log line is a single JSON object with our fields."""
    configure_logging(build_settings(**_PROD_SECRETS))

    get_logger("test").info("lead_scored", score=82)

    record = json.loads(_last_line(capsys.readouterr().out))
    assert record["event"] == "lead_scored"
    assert record["score"] == 82
    assert record["level"] == "info"
    assert "timestamp" in record


def test_development_logs_are_human_readable(capsys: CaptureFixture[str]) -> None:
    """In development, output is pretty console text, not JSON."""
    configure_logging(build_settings(env="development"))

    get_logger("test").info("lead_scored", score=82)

    line = _last_line(capsys.readouterr().out)
    assert "lead_scored" in line
    try:
        json.loads(line)
        raise AssertionError("development output should not be JSON")
    except json.JSONDecodeError:
        pass


def test_bound_context_appears_in_logs(capsys: CaptureFixture[str]) -> None:
    """Context bound to a logger (e.g. tenant_id) rides along on every line."""
    configure_logging(build_settings(**_PROD_SECRETS))

    get_logger("test").bind(tenant_id="t-123").info("lead_scored")

    record = json.loads(_last_line(capsys.readouterr().out))
    assert record["tenant_id"] == "t-123"
