import time

from fastapi.testclient import TestClient

from src.api.server import create_app


def _client():
    app = create_app()
    auth = app.state.auth_service
    auth.register_token(
        "docs-token",
        scopes={"docs:read"},
        roles={"workspace:reader"},
    )
    auth.register_token(
        "session-token",
        scopes={"docs:read"},
        roles={"workspace:reader"},
    )
    auth.register_token(
        "stale-token",
        scopes={"docs:read"},
        roles={"workspace:reader"},
        expires_at=time.time() - 1,
    )
    auth.register_token(
        "revoked-token",
        scopes={"docs:read"},
        roles={"workspace:reader"},
        revoked=True,
    )
    auth.register_token(
        "wrong-scope",
        scopes={"agents:read"},
        roles={"workspace:reader"},
    )
    auth.register_token(
        "wrong-role",
        scopes={"docs:read"},
        roles={"workspace:writer"},
    )
    return TestClient(app)


def test_default_fastapi_docs_and_schema_are_not_public():
    client = _client()

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_public_health_still_allows_anonymous_access():
    client = _client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_openapi_schema_requires_authentication_before_data_return():
    client = _client()

    response = client.get("/api/openapi.json")

    assert response.status_code == 401
    assert "openapi" not in response.text.lower()


def test_malformed_authorization_header_is_rejected():
    client = _client()

    response = client.get(
        "/api/openapi.json",
        headers={"Authorization": "Token docs-token"},
    )

    assert response.status_code == 401
    assert "openapi" not in response.text.lower()


def test_stale_and_revoked_credentials_are_denied():
    client = _client()

    stale_response = client.get(
        "/api/openapi.json",
        headers={"Authorization": "Bearer stale-token"},
    )
    revoked_response = client.get(
        "/api/openapi.json",
        headers={"Authorization": "Bearer revoked-token"},
    )

    assert stale_response.status_code == 401
    assert revoked_response.status_code == 401
    assert "openapi" not in stale_response.text.lower()
    assert "openapi" not in revoked_response.text.lower()


def test_docs_scope_and_workspace_role_are_required():
    client = _client()

    scope_response = client.get(
        "/api/openapi.json",
        headers={"Authorization": "Bearer wrong-scope"},
    )
    role_response = client.get(
        "/api/openapi.json",
        headers={"Authorization": "Bearer wrong-role"},
    )

    assert scope_response.status_code == 403
    assert role_response.status_code == 403
    assert "openapi" not in scope_response.text.lower()
    assert "openapi" not in role_response.text.lower()


def test_authorized_bearer_can_read_docs_and_schema():
    client = _client()
    headers = {"Authorization": "Bearer docs-token"}

    schema_response = client.get("/api/openapi.json", headers=headers)
    docs_response = client.get("/api/docs", headers=headers)
    redoc_response = client.get("/api/redoc", headers=headers)

    assert schema_response.status_code == 200
    assert schema_response.json()["info"]["title"] == "Agent Orchestrator API"
    assert docs_response.status_code == 200
    assert redoc_response.status_code == 200


def test_authorized_browser_session_can_read_schema():
    client = _client()
    client.cookies.set("ao_session", "session-token")

    response = client.get("/api/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Agent Orchestrator API"
