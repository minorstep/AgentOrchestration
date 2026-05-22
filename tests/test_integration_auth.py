from typing import Optional

from fastapi.testclient import TestClient

from src.api.server import create_app
from src.common.integration_auth import (
    IntegrationAuthService,
    PrincipalRecord,
    PrincipalStore,
)


NOW = 1000.0


def make_client() -> TestClient:
    store = PrincipalStore()
    active = PrincipalRecord(
        user_id="user-active",
        scopes={"api:access", "webhook:manage"},
        workspace_roles={"workspace-a": "admin"},
        expires_at=NOW + 600,
    )
    store.add_token("active-token", active)
    store.add_session("active-session", active)
    store.add_token(
        "disabled-token",
        disabled_record(),
    )
    store.add_session("disabled-session", disabled_record())
    store.add_token(
        "revoked-token",
        PrincipalRecord(
            user_id="user-revoked",
            scopes={"api:access", "webhook:manage"},
            workspace_roles={"workspace-a": "admin"},
            revoked=True,
            expires_at=NOW + 600,
        ),
    )
    store.add_token(
        "expired-token",
        PrincipalRecord(
            user_id="user-expired",
            scopes={"api:access", "webhook:manage"},
            workspace_roles={"workspace-a": "admin"},
            expires_at=NOW - 1,
        ),
    )
    store.add_token(
        "viewer-token",
        PrincipalRecord(
            user_id="user-viewer",
            scopes={"api:access", "webhook:manage"},
            workspace_roles={"workspace-a": "viewer"},
            expires_at=NOW + 600,
        ),
    )
    store.add_token(
        "missing-scope-token",
        PrincipalRecord(
            user_id="user-no-scope",
            scopes={"api:access"},
            workspace_roles={"workspace-a": "admin"},
            expires_at=NOW + 600,
        ),
    )
    auth_service = IntegrationAuthService(store, clock=lambda: NOW)
    return TestClient(create_app({"auth_service": auth_service}))


def disabled_record() -> PrincipalRecord:
    return PrincipalRecord(
        user_id="user-disabled",
        scopes={"api:access", "webhook:manage"},
        workspace_roles={"workspace-a": "admin"},
        disabled=True,
        expires_at=NOW + 600,
    )


def create_webhook(
    client: TestClient,
    headers: dict,
    cookies: Optional[dict] = None,
) -> tuple[int, dict]:
    if cookies:
        client.cookies.update(cookies)
    response = client.post(
        "/api/v2/integrations/webhooks",
        params={"target_url": "https://hooks.example.test/events"},
        headers={"X-Workspace-Id": "workspace-a", **headers},
    )
    return response.status_code, response.json()


def test_token_client_can_create_webhook_with_workspace_admin_role():
    status, body = create_webhook(
        make_client(),
        {"Authorization": "Bearer active-token"},
    )

    assert status == 200
    assert body["status"] == "registered"
    assert body["workspace_id"] == "workspace-a"
    assert body["created_by"] == "user-active"


def test_browser_session_can_create_same_webhook_workflow():
    status, body = create_webhook(
        make_client(),
        {},
        cookies={"ao_session": "active-session"},
    )

    assert status == 200
    assert body["status"] == "registered"
    assert body["created_by"] == "user-active"


def test_disabled_user_is_blocked_before_webhook_management_runs():
    status, body = create_webhook(
        make_client(),
        {"Authorization": "Bearer disabled-token"},
    )

    assert status == 403
    assert body["error"] == "USER_DISABLED"


def test_disabled_browser_session_is_blocked_too():
    status, body = create_webhook(
        make_client(),
        {},
        cookies={"ao_session": "disabled-session"},
    )

    assert status == 403
    assert body["error"] == "USER_DISABLED"


def test_revoked_credentials_are_rejected():
    status, body = create_webhook(
        make_client(),
        {"Authorization": "Bearer revoked-token"},
    )

    assert status == 403
    assert body["error"] == "CREDENTIAL_REVOKED"


def test_expired_credentials_are_rejected():
    status, body = create_webhook(
        make_client(),
        {"Authorization": "Bearer expired-token"},
    )

    assert status == 401
    assert body["error"] == "CREDENTIAL_EXPIRED"


def test_anonymous_webhook_management_is_rejected():
    status, body = create_webhook(make_client(), {})

    assert status == 401
    assert body["error"] == "AUTH_REQUIRED"


def test_malformed_bearer_header_is_rejected():
    status, body = create_webhook(make_client(), {"Authorization": "Bearer"})

    assert status == 401
    assert body["error"] == "INVALID_CREDENTIAL"


def test_missing_webhook_scope_is_rejected():
    status, body = create_webhook(
        make_client(),
        {"Authorization": "Bearer missing-scope-token"},
    )

    assert status == 403
    assert body["error"] == "INSUFFICIENT_SCOPE"


def test_insufficient_workspace_role_is_rejected():
    status, body = create_webhook(
        make_client(),
        {"Authorization": "Bearer viewer-token"},
    )

    assert status == 403
    assert body["error"] == "INSUFFICIENT_ROLE"
