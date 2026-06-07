"""Public schemas and enums for the tenant_config registry.

The single source of truth for the config status/dimension enums and the
validated value objects (signals, weights, thresholds). models.py, service.py,
modules/scoring, and tests import from here — never from models.py.
"""

from enum import StrEnum


class ConfigStatus(StrEnum):
    """The lifecycle state of a single tenant_config version."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"


class Dimension(StrEnum):
    """The five scoring dimensions every config must cover."""

    FIT = "FIT"
    INTENT = "INTENT"
    ENGAGEMENT = "ENGAGEMENT"
    BEHAVIOUR = "BEHAVIOUR"
    CONTEXT = "CONTEXT"
