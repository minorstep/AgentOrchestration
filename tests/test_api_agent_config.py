from fastapi.testclient import TestClient

from src.agent.registry import AgentRegistry
from src.api import routes
from src.api.server import create_app


class TestAgentConfigEtags:
    def setup_method(self):
        self.registry = AgentRegistry()
        routes.registry = self.registry
        self.client = TestClient(create_app())
        self.auth = {"Authorization": "Bearer test-token"}

    def test_authorized_update_requires_current_etag(self):
        agent_id = self.registry.register(
            "worker-a",
            "worker.processor",
            {"limit": 1},
        )
        original_etag = self.registry.get(agent_id)["etag"]

        response = self.client.put(
            f"/api/v2/agents/{agent_id}/config",
            headers={**self.auth, "If-Match": original_etag},
            json={"limit": 2},
        )

        assert response.status_code == 200
        assert response.json()["config"] == {"limit": 2}
        assert response.headers["etag"] != original_etag

        stale_response = self.client.put(
            f"/api/v2/agents/{agent_id}/config",
            headers={**self.auth, "If-Match": original_etag},
            json={"limit": 3},
        )

        assert stale_response.status_code == 412
        assert self.registry.get(agent_id)["config"] == {"limit": 2}

    def test_unauthorized_update_is_rejected_without_mutation(self):
        agent_id = self.registry.register(
            "worker-b",
            "worker.processor",
            {"mode": "safe"},
        )
        etag = self.registry.get(agent_id)["etag"]

        response = self.client.put(
            f"/api/v2/agents/{agent_id}/config",
            headers={"If-Match": etag},
            json={"mode": "unsafe"},
        )

        assert response.status_code == 401
        assert self.registry.get(agent_id)["config"] == {"mode": "safe"}

    def test_missing_if_match_is_rejected_without_mutation(self):
        agent_id = self.registry.register(
            "worker-c",
            "worker.processor",
            {"version": 1},
        )

        response = self.client.put(
            f"/api/v2/agents/{agent_id}/config",
            headers=self.auth,
            json={"version": 2},
        )

        assert response.status_code == 428
        assert self.registry.get(agent_id)["config"] == {"version": 1}

    def test_malformed_if_match_is_rejected_without_mutation(self):
        agent_id = self.registry.register(
            "worker-d",
            "worker.processor",
            {"version": 1},
        )

        response = self.client.put(
            f"/api/v2/agents/{agent_id}/config",
            headers={**self.auth, "If-Match": 'W/"weak"'},
            json={"version": 2},
        )

        assert response.status_code == 400
        assert self.registry.get(agent_id)["config"] == {"version": 1}

    def test_malformed_body_is_rejected_without_mutation(self):
        agent_id = self.registry.register(
            "worker-e",
            "worker.processor",
            {"version": 1},
        )
        etag = self.registry.get(agent_id)["etag"]

        response = self.client.put(
            f"/api/v2/agents/{agent_id}/config",
            headers={**self.auth, "If-Match": etag},
            json=["not", "an", "object"],
        )

        assert response.status_code == 422
        assert self.registry.get(agent_id)["config"] == {"version": 1}
