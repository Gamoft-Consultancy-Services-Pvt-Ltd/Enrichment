"""Unit tests for shared.tenant_config.schemas — pure validation, no DB."""

from shared.tenant_config.schemas import ConfigStatus, Dimension


def test_config_status_membership_is_exact() -> None:
    assert {m.value for m in ConfigStatus} == {"DRAFT", "ACTIVE", "ARCHIVED", "REJECTED"}


def test_dimension_membership_is_exact() -> None:
    assert {m.value for m in Dimension} == {
        "FIT",
        "INTENT",
        "ENGAGEMENT",
        "BEHAVIOUR",
        "CONTEXT",
    }
