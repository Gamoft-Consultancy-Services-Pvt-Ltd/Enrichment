"""LeadFormFields schema and the canonical column-name mapping.

STANDARD_FIELD_MAP normalises common header aliases from CSV/XLSX uploads and
Lead Ad form fields onto the four canonical identity fields. Unrecognised headers
go into extra_fields verbatim — they are never dropped.
"""

from typing import Any

from pydantic import BaseModel, Field

STANDARD_FIELD_MAP: dict[str, str] = {
    # phone variations
    "Mobile": "phone",
    "Phone": "phone",
    "Phone Number": "phone",
    "Cell": "phone",
    "Contact Number": "phone",
    # email variations
    "Email": "email",
    "Email Address": "email",
    "E-mail": "email",
    # name variations
    "Contact Name": "full_name",
    "Name": "full_name",
    "Full Name": "full_name",
    "Customer Name": "full_name",
    # location variations
    "Location": "location",
    "City": "location",
    "Address": "location",
    "Area": "location",
}


class LeadFormFields(BaseModel):
    """Extracted identity fields from a structured form row or Lead Ad payload."""

    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    location: str | None = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)
