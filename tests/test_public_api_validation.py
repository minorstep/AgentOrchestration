from fastapi.testclient import TestClient

from src.agent import AgentRegistry
from src.api import routes as api_routes
from src.api.server import create_app


AUTH_HEADERS = {"Authorization": "Bearer test-token"}


class TestPublicApiValidation:
    def setup_method(self):
        api_routes.registry = AgentRegistry()
        self.client = TestClient(create_app())

    def test_unauthorized_public_api_request_returns_error_code(self):
        response = self.client.get("/api/v2/agents")

        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "UNAUTHORIZED"

    def test_malformed_status_fails_before_lookup(self, monkeypatch):
        def fail_lookup(*args, **kwargs):
            raise AssertionError("registry lookup should not run")

        monkeypatch.setattr(api_routes.registry, "list", fail_lookup)

        response = self.client.get(
            "/api/v2/agents",
            params={"status": "archived"},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "VALIDATION_FAILED"

    def test_malformed_group_fails_before_lookup(self, monkeypatch):
        def fail_lookup(*args, **kwargs):
            raise AssertionError("registry lookup should not run")

        monkeypatch.setattr(api_routes.registry, "list", fail_lookup)

        response = self.client.get(
            "/api/v2/agents",
            params={"group": "../worker"},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "VALIDATION_FAILED"

    def test_authorized_valid_filter_uses_registry(self):
        api_routes.registry.register("agent-1", "worker.processor")
        api_routes.registry.register("agent-2", "monitor.watcher")

        response = self.client.get(
            "/api/v2/agents",
            params={"status": "pending", "group": "worker"},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert [agent["name"] for agent in response.json()["agents"]] == [
            "agent-1"
        ]

    def test_missing_registration_name_returns_deterministic_400(self):
        response = self.client.post(
            "/api/v2/agents",
            params={"agent_type": "worker.processor"},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "VALIDATION_FAILED"
        assert api_routes.registry.count() == 0

    def test_malformed_registration_fails_before_mutation(self, monkeypatch):
        def fail_register(*args, **kwargs):
            raise AssertionError("registry mutation should not run")

        monkeypatch.setattr(api_routes.registry, "register", fail_register)

        response = self.client.post(
            "/api/v2/agents",
            params={"name": "agent-1", "agent_type": "worker"},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "VALIDATION_FAILED"

    def test_authorized_registration_succeeds(self):
        response = self.client.post(
            "/api/v2/agents",
            params={"name": "agent-1", "agent_type": "worker.processor"},
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["status"] == "registered"
        assert api_routes.registry.count() == 1

    def test_agent_count_route_is_not_treated_as_agent_id(self, monkeypatch):
        api_routes.registry.register("agent-1", "worker.processor")

        def fail_get(*args, **kwargs):
            raise AssertionError("dynamic agent lookup should not run")

        monkeypatch.setattr(api_routes.registry, "get", fail_get)

        response = self.client.get(
            "/api/v2/agents/count",
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_malformed_agent_id_fails_before_lookup(self, monkeypatch):
        def fail_get(*args, **kwargs):
            raise AssertionError("registry lookup should not run")

        monkeypatch.setattr(api_routes.registry, "get", fail_get)

        response = self.client.get(
            "/api/v2/agents/bad%20id",
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "VALIDATION_FAILED"
