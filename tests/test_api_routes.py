from uuid import uuid4

from fastapi.testclient import TestClient

from src.api import routes
from src.api.server import create_app


AUTH_HEADERS = {"Authorization": "Bearer test-token"}


class SpyRegistry:
    def __init__(self):
        self.list_called = False
        self.get_called = False
        self.register_called = False
        self._agent_id = str(uuid4())

    def list(self, status=None, group=None):
        self.list_called = True
        return []

    def get(self, agent_id):
        self.get_called = True
        return None

    def register(self, name, agent_type, config=None):
        self.register_called = True
        return self._agent_id

    def delete(self, agent_id):
        return False

    def update_status(self, agent_id, status):
        return False

    def count(self):
        return 0


def test_agents_route_accepts_authorized_request(monkeypatch):
    spy = SpyRegistry()
    monkeypatch.setattr(routes.service, "registry", spy)
    client = TestClient(create_app())

    response = client.get("/api/v2/agents", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"agents": []}
    assert spy.list_called


def test_agents_route_rejects_unauthorized_request(monkeypatch):
    spy = SpyRegistry()
    monkeypatch.setattr(routes.service, "registry", spy)
    client = TestClient(create_app())

    response = client.get("/api/v2/agents")

    assert response.status_code == 401
    assert not spy.list_called


def test_invalid_status_returns_validation_error_without_lookup(monkeypatch):
    spy = SpyRegistry()
    monkeypatch.setattr(routes.service, "registry", spy)
    client = TestClient(create_app())

    response = client.get("/api/v2/agents?status=broken", headers=AUTH_HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "validation_error"
    assert response.json()["detail"]["field"] == "status"
    assert not spy.list_called


def test_malformed_agent_id_returns_validation_error_without_lookup(
    monkeypatch,
):
    spy = SpyRegistry()
    monkeypatch.setattr(routes.service, "registry", spy)
    client = TestClient(create_app())

    response = client.get("/api/v2/agents/not-a-uuid", headers=AUTH_HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "validation_error"
    assert response.json()["detail"]["field"] == "agent_id"
    assert not spy.get_called


def test_blank_registration_payload_does_not_mutate_registry(monkeypatch):
    spy = SpyRegistry()
    monkeypatch.setattr(routes.service, "registry", spy)
    client = TestClient(create_app())

    response = client.post(
        "/api/v2/agents?name=&agent_type=",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "validation_error"
    assert not spy.register_called


def test_agents_count_route_is_not_shadowed_by_agent_id_route(monkeypatch):
    spy = SpyRegistry()
    monkeypatch.setattr(routes.service, "registry", spy)
    client = TestClient(create_app())

    response = client.get("/api/v2/agents/count", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"count": 0}
