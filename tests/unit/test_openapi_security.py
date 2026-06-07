"""Unit test: the API advertises Auth0's OAuth2 authorization-code flow (for /docs login)."""

from main import app


def test_openapi_uses_oauth2_authorization_code_flow() -> None:
    schema = app.openapi()
    schemes = schema["components"]["securitySchemes"]
    oauth = [s for s in schemes.values() if s.get("type") == "oauth2"]
    assert oauth, "expected an OAuth2 security scheme for the /docs login flow"
    flow = oauth[0]["flows"]["authorizationCode"]
    assert flow["authorizationUrl"].endswith("/authorize")
    assert flow["tokenUrl"].endswith("/oauth/token")
