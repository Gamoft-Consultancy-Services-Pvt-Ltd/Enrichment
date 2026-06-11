"""Verify that Sprint 1 LeadSource additions are present in the enum."""

import pytest

from shared.events.schemas import LeadSource


@pytest.mark.parametrize(
    "value",
    ["FACEBOOK", "FACEBOOK_LEAD_AD", "INSTAGRAM_LEAD_AD", "FILE_UPLOAD"],
)
def test_new_lead_source_values_present(value: str) -> None:
    assert LeadSource(value).value == value
