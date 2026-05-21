from fastapi.testclient import TestClient

from src.api.server import create_app
from src.common.auth import (
    JWTAuthError,
    create_service_token,
    validate_service_token,
)


SECRET = "test-agent-worker-secret"
NOW = 1_800_000_000


def _client(monkeypatch):
    monkeypatch.setenv("AO_JWT_SECRET", SECRET)
    monkeypatch.delenv("AO_JWT_REVOKED_JTI", raising=False)
    monkeypatch.delenv("AO_JWT_REVOKED_TOKENS", raising=False)
    return TestClient(create_app())


def _token(**overrides):
    audience = overrides.pop("audience", "agent-workers")
    ttl = overrides.pop("ttl", 300)
    claims = {
        "sub": "worker-service",
        "scope": "agent:worker",
        "workspace_roles": ["workspace:worker"],
        "jti": "active-token",
    }
    claims.update(overrides)
    return create_service_token(
        claims,
        secret=SECRET,
        audience=audience,
        ttl=ttl,
    )


def test_anonymous_agent_worker_request_is_denied(monkeypatch):
    response = _client(monkeypatch).get("/api/v2/agents")

    assert response.status_code == 401


def test_malformed_bearer_token_is_denied(monkeypatch):
    response = _client(monkeypatch).get(
        "/api/v2/agents",
        headers={"Authorization": "Bearer not-a-jwt"},
    )

    assert response.status_code == 401


def test_malformed_timestamp_claim_is_denied(monkeypatch):
    token = _token(exp="later")

    response = _client(monkeypatch).get(
        "/api/v2/agents",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_stale_agent_worker_token_is_denied(monkeypatch):
    token = _token(ttl=-120)

    response = _client(monkeypatch).get(
        "/api/v2/agents",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_revoked_agent_worker_token_is_denied(monkeypatch):
    monkeypatch.setenv("AO_JWT_SECRET", SECRET)
    monkeypatch.setenv("AO_JWT_REVOKED_JTI", "revoked-token")
    token = _token(jti="revoked-token")

    response = TestClient(create_app()).get(
        "/api/v2/agents",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_wrong_audience_token_is_denied_before_agent_route(monkeypatch):
    token = _token(audience="control-plane")

    response = _client(monkeypatch).get(
        "/api/v2/agents",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_insufficient_scope_is_denied(monkeypatch):
    token = _token(scope="agents:read")

    response = _client(monkeypatch).get(
        "/api/v2/agents",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_insufficient_workspace_role_is_denied(monkeypatch):
    token = _token(workspace_roles=["workspace:viewer"])

    response = _client(monkeypatch).get(
        "/api/v2/agents",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_authorized_bearer_can_complete_agent_worker_request(monkeypatch):
    token = _token()

    response = _client(monkeypatch).post(
        "/api/v2/agents?name=worker-1&agent_type=worker.processor",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "registered"


def test_authorized_browser_session_uses_same_auth_path(monkeypatch):
    token = _token(jti="browser-token")
    client = _client(monkeypatch)
    client.cookies.set("ao_session", token)

    response = client.get("/api/v2/agents")

    assert response.status_code == 200
    assert "agents" in response.json()


def test_revoked_raw_token_is_rejected_by_validator():
    token = _token(jti="raw-token")

    try:
        validate_service_token(
            token,
            secret=SECRET,
            revoked_tokens={token},
            required_scope="agent:worker",
            required_role="workspace:worker",
        )
    except JWTAuthError as exc:
        assert exc.code == "revoked"
    else:
        raise AssertionError("revoked token was accepted")
