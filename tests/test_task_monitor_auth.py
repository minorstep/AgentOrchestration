import time

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.common.auth import (
    AuthError,
    TASK_MONITOR_SCOPE,
    WORKSPACE_ADMIN_ROLE,
    WORKSPACE_VIEWER_ROLE,
    auth_service,
)


class TestTaskMonitorAuth:
    def setup_method(self):
        auth_service.reset()
        self.client = TestClient(create_app())

    def test_api_key_can_poll_task_monitor(self):
        auth_service.register_api_key(
            "token-ok",
            subject="operator-1",
            workspace_id="workspace-a",
            scopes={TASK_MONITOR_SCOPE},
            roles={WORKSPACE_VIEWER_ROLE},
        )

        response = self.client.get(
            "/api/v2/workspaces/workspace-a/task-monitor/poll",
            headers={"Authorization": "Bearer token-ok"},
        )

        assert response.status_code == 200
        assert response.json()["principal"] == "operator-1"
        assert response.json()["events"] == []

    def test_browser_session_can_poll_task_monitor(self):
        auth_service.register_browser_session(
            "session-ok",
            subject="browser-user",
            workspace_id="workspace-a",
            scopes={TASK_MONITOR_SCOPE},
            roles={WORKSPACE_VIEWER_ROLE},
        )

        self.client.cookies.set("ao_session", "session-ok")
        response = self.client.get(
            "/api/v2/workspaces/workspace-a/task-monitor/poll",
        )

        assert response.status_code == 200
        assert response.json()["client_type"] == "browser-session"

    def test_revoked_api_key_is_denied_before_poll(self):
        auth_service.register_api_key(
            "revoked-token",
            subject="operator-1",
            workspace_id="workspace-a",
            scopes={TASK_MONITOR_SCOPE},
            roles={WORKSPACE_VIEWER_ROLE},
        )
        auth_service.revoke_api_key("revoked-token")

        response = self.client.get(
            "/api/v2/workspaces/workspace-a/task-monitor/poll",
            headers={"Authorization": "Bearer revoked-token"},
        )

        assert response.status_code == 401

    def test_route_revalidation_rejects_mid_poll_revocation(self):
        context = auth_service.register_api_key(
            "mid-poll-token",
            subject="operator-1",
            workspace_id="workspace-a",
            scopes={TASK_MONITOR_SCOPE},
            roles={WORKSPACE_VIEWER_ROLE},
        )
        auth_service.revoke_api_key("mid-poll-token")

        with pytest.raises(AuthError) as exc:
            auth_service.revalidate_task_monitor_poll(context, "workspace-a")

        assert exc.value.status_code == 401
        assert exc.value.reason == "revoked"

    def test_anonymous_task_monitor_request_is_denied(self):
        response = self.client.get(
            "/api/v2/workspaces/workspace-a/task-monitor/poll",
        )

        assert response.status_code == 401

    def test_malformed_bearer_token_is_denied(self):
        response = self.client.get(
            "/api/v2/workspaces/workspace-a/task-monitor/poll",
            headers={"Authorization": "Bearer "},
        )

        assert response.status_code == 401

    def test_expired_api_key_is_denied(self):
        auth_service.register_api_key(
            "expired-token",
            subject="operator-1",
            workspace_id="workspace-a",
            scopes={TASK_MONITOR_SCOPE},
            roles={WORKSPACE_VIEWER_ROLE},
            expires_at=time.time() - 1,
        )

        response = self.client.get(
            "/api/v2/workspaces/workspace-a/task-monitor/poll",
            headers={"Authorization": "Bearer expired-token"},
        )

        assert response.status_code == 401

    def test_insufficient_scope_is_denied(self):
        auth_service.register_api_key(
            "wrong-scope-token",
            subject="operator-1",
            workspace_id="workspace-a",
            scopes={"agent:manage"},
            roles={WORKSPACE_VIEWER_ROLE},
        )

        response = self.client.get(
            "/api/v2/workspaces/workspace-a/task-monitor/poll",
            headers={"Authorization": "Bearer wrong-scope-token"},
        )

        assert response.status_code == 403

    def test_insufficient_role_is_denied(self):
        auth_service.register_api_key(
            "wrong-role-token",
            subject="operator-1",
            workspace_id="workspace-a",
            scopes={TASK_MONITOR_SCOPE},
            roles={"billing.viewer"},
        )

        response = self.client.get(
            "/api/v2/workspaces/workspace-a/task-monitor/poll",
            headers={"Authorization": "Bearer wrong-role-token"},
        )

        assert response.status_code == 403

    def test_cross_workspace_key_is_denied(self):
        auth_service.register_api_key(
            "cross-workspace-token",
            subject="operator-1",
            workspace_id="workspace-a",
            scopes={TASK_MONITOR_SCOPE},
            roles={WORKSPACE_ADMIN_ROLE},
        )

        response = self.client.get(
            "/api/v2/workspaces/workspace-b/task-monitor/poll",
            headers={"Authorization": "Bearer cross-workspace-token"},
        )

        assert response.status_code == 403

    def test_audit_records_do_not_store_raw_tokens(self):
        raw_token = "very-private-token"
        auth_service.register_api_key(
            raw_token,
            subject="operator-1",
            workspace_id="workspace-a",
            scopes={TASK_MONITOR_SCOPE},
            roles={WORKSPACE_VIEWER_ROLE},
        )

        response = self.client.get(
            "/api/v2/workspaces/workspace-a/task-monitor/poll",
            headers={"Authorization": f"Bearer {raw_token}"},
        )

        assert response.status_code == 200
        assert raw_token not in str(auth_service.audit_events())
