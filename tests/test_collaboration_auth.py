from typing import Optional

from fastapi.testclient import TestClient

from src.api.server import create_app
from src.common.collaboration_auth import (
    CollaborationAuthService,
    PrincipalRecord,
    PrincipalStore,
    SavedView,
    SavedViewStore,
)


NOW = 1000.0


def active_record(
    role: str = "editor",
    scopes: Optional[set[str]] = None,
    expires_at: float = NOW + 600,
) -> PrincipalRecord:
    return PrincipalRecord(
        user_id="user-active",
        scopes=scopes or {"api:access", "saved-view:share"},
        workspace_roles={"workspace-a": role},
        expires_at=expires_at,
    )


def make_client() -> TestClient:
    principal_store = PrincipalStore()
    view_store = SavedViewStore()
    view_store.add(
        SavedView(
            view_id="view-a",
            workspace_id="workspace-a",
            owner_id="owner-a",
        )
    )
    view_store.add(
        SavedView(
            view_id="view-b",
            workspace_id="workspace-b",
            owner_id="owner-b",
        )
    )
    active = active_record()
    principal_store.add_token("active-token", active)
    principal_store.add_session("active-session", active)
    principal_store.add_token(
        "revoked-token",
        PrincipalRecord(
            user_id="user-revoked",
            scopes={"api:access", "saved-view:share"},
            workspace_roles={"workspace-a": "editor"},
            revoked=True,
            expires_at=NOW + 600,
        ),
    )
    principal_store.add_token(
        "expired-token",
        active_record(expires_at=NOW - 1),
    )
    principal_store.add_token(
        "viewer-token",
        active_record(role="viewer"),
    )
    principal_store.add_token(
        "missing-scope-token",
        active_record(scopes={"api:access"}),
    )
    auth_service = CollaborationAuthService(
        principal_store,
        view_store,
        clock=lambda: NOW,
    )
    return TestClient(create_app({"auth_service": auth_service}))


def share_view(
    client: TestClient,
    view_id: str = "view-a",
    headers: Optional[dict] = None,
    cookies: Optional[dict] = None,
) -> tuple[int, dict]:
    if cookies:
        client.cookies.update(cookies)
    response = client.post(
        f"/api/v2/saved-views/{view_id}/share",
        params={"target_user_id": "teammate"},
        headers={"X-Workspace-Id": "workspace-a", **(headers or {})},
    )
    return response.status_code, response.json()


def test_token_member_can_share_saved_view_in_own_workspace():
    status, body = share_view(
        make_client(),
        headers={"Authorization": "Bearer active-token"},
    )

    assert status == 200
    assert body["status"] == "shared"
    assert body["view_id"] == "view-a"
    assert body["workspace_id"] == "workspace-a"
    assert body["shared_by"] == "user-active"


def test_browser_session_member_can_share_saved_view():
    status, body = share_view(
        make_client(),
        cookies={"ao_session": "active-session"},
    )

    assert status == 200
    assert body["status"] == "shared"
    assert body["workspace_id"] == "workspace-a"


def test_anonymous_saved_view_share_is_denied():
    status, body = share_view(make_client())

    assert status == 401
    assert body["error"] == "AUTH_REQUIRED"


def test_malformed_token_is_denied():
    status, body = share_view(
        make_client(),
        headers={"Authorization": "Bearer"},
    )

    assert status == 401
    assert body["error"] == "INVALID_CREDENTIAL"


def test_revoked_principal_is_denied():
    status, body = share_view(
        make_client(),
        headers={"Authorization": "Bearer revoked-token"},
    )

    assert status == 403
    assert body["error"] == "CREDENTIAL_REVOKED"


def test_expired_principal_is_denied():
    status, body = share_view(
        make_client(),
        headers={"Authorization": "Bearer expired-token"},
    )

    assert status == 401
    assert body["error"] == "CREDENTIAL_EXPIRED"


def test_missing_share_scope_is_denied():
    status, body = share_view(
        make_client(),
        headers={"Authorization": "Bearer missing-scope-token"},
    )

    assert status == 403
    assert body["error"] == "INSUFFICIENT_SCOPE"


def test_insufficient_workspace_role_is_denied():
    status, body = share_view(
        make_client(),
        headers={"Authorization": "Bearer viewer-token"},
    )

    assert status == 403
    assert body["error"] == "INSUFFICIENT_ROLE"


def test_cross_workspace_saved_view_is_denied():
    status, body = share_view(
        make_client(),
        view_id="view-b",
        headers={"Authorization": "Bearer active-token"},
    )

    assert status == 403
    assert body["error"] == "WORKSPACE_MISMATCH"
