"""Domain exceptions for the lead_ingestion module."""


class HmacValidationError(Exception):
    """Webhook payload HMAC-SHA256 signature does not match."""


class PreFlightHaltError(Exception):
    """Tenant config is missing or has no signals; lead cannot be processed."""


class DuplicateEventError(Exception):
    """A platform_event_id has already been logged; dedup guard fired."""


class ChannelApiError(Exception):
    """A call to an external channel API (Meta Graph, etc.) failed."""


class FilterClientError(Exception):
    """The two-stage filter (Groq classify_message) returned an unexpected result."""


class OAuthStateError(Exception):
    """OAuth state parameter is missing, malformed, or HMAC verification failed."""
