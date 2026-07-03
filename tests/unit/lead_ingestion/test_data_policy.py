"""Unit tests for COMP-301 data minimization — modules/lead_ingestion/data_policy.py."""

from typing import Any

from modules.lead_ingestion.data_policy import (
    COLLECTION_JUSTIFICATION,
    LEAD_FIELD_CLASSIFICATIONS,
    FieldSensitivity,
    detect_sensitive_fields,
    strip_sensitive_fields,
)

# ---------------------------------------------------------------------------
# FieldSensitivity classification model (ST1 + ST2)
# ---------------------------------------------------------------------------


def test_pii_direct_fields_classified() -> None:
    assert LEAD_FIELD_CLASSIFICATIONS["full_name"] == FieldSensitivity.PII_DIRECT
    assert LEAD_FIELD_CLASSIFICATIONS["phone"] == FieldSensitivity.PII_DIRECT
    assert LEAD_FIELD_CLASSIFICATIONS["email"] == FieldSensitivity.PII_DIRECT


def test_pii_indirect_fields_classified() -> None:
    assert LEAD_FIELD_CLASSIFICATIONS["location"] == FieldSensitivity.PII_INDIRECT


def test_pii_possible_fields_classified() -> None:
    assert LEAD_FIELD_CLASSIFICATIONS["raw_event_json"] == FieldSensitivity.PII_POSSIBLE
    assert LEAD_FIELD_CLASSIFICATIONS["extra_fields"] == FieldSensitivity.PII_POSSIBLE


def test_operational_fields_classified() -> None:
    assert LEAD_FIELD_CLASSIFICATIONS["pipeline_stage"] == FieldSensitivity.OPERATIONAL
    assert LEAD_FIELD_CLASSIFICATIONS["source_channel"] == FieldSensitivity.OPERATIONAL


def test_collection_justification_covers_core_fields() -> None:
    for field in ("full_name", "phone", "email", "location", "raw_event_json", "extra_fields"):
        assert field in COLLECTION_JUSTIFICATION
        assert len(COLLECTION_JUSTIFICATION[field]) > 10


# ---------------------------------------------------------------------------
# detect_sensitive_fields (ST3 + ST5)
# ---------------------------------------------------------------------------


def test_aadhaar_no_separators_detected() -> None:
    result = detect_sensitive_fields({"uid": "123456789012"})
    assert result == {"uid": "AADHAAR"}


def test_aadhaar_space_separator_detected() -> None:
    result = detect_sensitive_fields({"aadhaar_no": "1234 5678 9012"})
    assert result == {"aadhaar_no": "AADHAAR"}


def test_aadhaar_hyphen_separator_detected() -> None:
    result = detect_sensitive_fields({"uid_no": "1234-5678-9012"})
    assert result == {"uid_no": "AADHAAR"}


def test_pan_uppercase_detected() -> None:
    result = detect_sensitive_fields({"pan_number": "ABCDE1234F"})
    assert result == {"pan_number": "PAN"}


def test_pan_lowercase_detected() -> None:
    # Case normalisation: lowercase PAN should still be caught
    result = detect_sensitive_fields({"pan": "abcde1234f"})
    assert result == {"pan": "PAN"}


def test_credit_card_luhn_valid_detected() -> None:
    # 4111111111111111 is the canonical Luhn-valid Visa test number
    result = detect_sensitive_fields({"card_no": "4111111111111111"})
    assert result == {"card_no": "CREDIT_CARD"}


def test_credit_card_luhn_invalid_not_detected() -> None:
    # Flip one digit to break Luhn — 4111111111111112
    result = detect_sensitive_fields({"card_no": "4111111111111112"})
    assert "card_no" not in result


def test_credit_card_with_spaces_detected() -> None:
    result = detect_sensitive_fields({"cc": "4111 1111 1111 1111"})
    assert result == {"cc": "CREDIT_CARD"}


def test_credit_card_with_hyphens_detected() -> None:
    result = detect_sensitive_fields({"cc": "4111-1111-1111-1111"})
    assert result == {"cc": "CREDIT_CARD"}


def test_ifsc_detected() -> None:
    # IFSC format: 4 uppercase letters + '0' + 6 alphanumeric
    result = detect_sensitive_fields({"bank_code": "HDFC0001234"})
    assert result == {"bank_code": "IFSC"}


def test_passport_detected() -> None:
    # Indian passport: 1 uppercase letter + 7 digits
    result = detect_sensitive_fields({"passport": "A1234567"})
    assert result == {"passport": "PASSPORT"}


def test_passport_lowercase_detected() -> None:
    result = detect_sensitive_fields({"travel_doc": "a1234567"})
    assert result == {"travel_doc": "PASSPORT"}


def test_clean_values_not_detected() -> None:
    data = {
        "budget": "50000",
        "company": "Acme Corp",
        "notes": "Referred by Raj",
        "city": "Mumbai",
    }
    result = detect_sensitive_fields(data)
    assert result == {}


def test_empty_dict_no_detection() -> None:
    assert detect_sensitive_fields({}) == {}


def test_none_value_skipped() -> None:
    result = detect_sensitive_fields({"field": None})
    assert result == {}


def test_integer_value_coerced_to_str() -> None:
    # Integer Aadhaar stored without quotes
    result = detect_sensitive_fields({"uid": 123456789012})
    assert result == {"uid": "AADHAAR"}


def test_multiple_sensitive_fields_all_detected() -> None:
    data: dict[str, Any] = {
        "aadhaar": "1234 5678 9012",
        "pan": "ABCDE1234F",
        "budget": "100000",
    }
    result = detect_sensitive_fields(data)
    assert result == {"aadhaar": "AADHAAR", "pan": "PAN"}


# ---------------------------------------------------------------------------
# strip_sensitive_fields (ST5)
# ---------------------------------------------------------------------------


def test_strip_removes_sensitive_key() -> None:
    extra = {"pan_number": "ABCDE1234F", "budget": "50000"}
    clean, removed = strip_sensitive_fields(extra)
    assert "pan_number" not in clean
    assert "budget" in clean
    assert removed == {"pan_number": "PAN"}


def test_strip_does_not_mutate_input() -> None:
    extra = {"uid": "123456789012", "name": "Raj"}
    original_keys = set(extra.keys())
    strip_sensitive_fields(extra)
    assert set(extra.keys()) == original_keys


def test_strip_clean_dict_unchanged() -> None:
    extra = {"budget": "50000", "company": "Acme"}
    clean, removed = strip_sensitive_fields(extra)
    assert clean == extra
    assert removed == {}


def test_strip_empty_dict_no_op() -> None:
    clean, removed = strip_sensitive_fields({})
    assert clean == {}
    assert removed == {}


def test_strip_multiple_sensitive_all_removed() -> None:
    extra: dict[str, Any] = {
        "aadhaar": "1234 5678 9012",
        "pan": "ABCDE1234F",
        "budget": "75000",
        "bank_code": "HDFC0001234",
    }
    clean, removed = strip_sensitive_fields(extra)
    assert set(clean.keys()) == {"budget"}
    assert set(removed.keys()) == {"aadhaar", "pan", "bank_code"}
