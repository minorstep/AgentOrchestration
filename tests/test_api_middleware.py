from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware import RequestContextMiddleware, get_request_context


def make_app():
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ok")
    async def ok():
        context = get_request_context()
        return {"path": context.path, "agent_id": context.agent_id}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret-token-should-not-leak")

    @app.get("/agents/{agent_id}/boom")
    async def agent_boom(agent_id: str):
        raise RuntimeError(f"secret-token-should-not-leak:{agent_id}")

    return app


def test_context_is_available_during_normal_request_and_cleared_afterwards():
    client = TestClient(make_app())

    response = client.get("/ok", headers={"X-Agent-ID": "agent-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-Context"] == "isolated"
    assert response.headers["X-Agent-Context-Cleared"] == "true"
    assert response.json() == {"path": "/ok", "agent_id": "agent-123"}
    assert get_request_context() is None


def test_invalid_agent_context_rejects_before_handler_and_clears_state():
    client = TestClient(make_app())

    response = client.get("/ok", headers={"X-Agent-ID": "../../secret-token"})

    assert response.status_code == 400
    assert response.headers["X-Request-Context"] == "rejected"
    assert response.headers["X-Agent-Context-Cleared"] == "true"
    assert response.json() == {"detail": "Invalid request context"}
    assert get_request_context() is None


def test_context_is_cleared_after_exception_without_exposing_details(caplog):
    client = TestClient(make_app())

    response = client.get("/boom", headers={"X-Agent-ID": "agent-123"})

    assert response.status_code == 500
    assert response.headers["X-Request-Context"] == "error"
    assert response.headers["X-Agent-Context-Cleared"] == "true"
    assert response.json() == {"detail": "Internal Server Error"}
    assert "secret-token-should-not-leak" not in response.text
    assert "secret-token-should-not-leak" not in caplog.text
    assert get_request_context() is None


def test_consecutive_requests_do_not_share_agent_context():
    client = TestClient(make_app())

    first = client.get("/ok", headers={"X-Agent-ID": "agent-one"})
    second = client.get("/ok", headers={"X-Agent-ID": "agent-two"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["agent_id"] == "agent-one"
    assert second.json()["agent_id"] == "agent-two"
    assert first.headers["X-Agent-Context-Cleared"] == "true"
    assert second.headers["X-Agent-Context-Cleared"] == "true"
    assert get_request_context() is None


def test_exception_logs_do_not_expose_agent_path_material(caplog):
    client = TestClient(make_app())

    response = client.get("/agents/agent-secret-123/boom")

    assert response.status_code == 500
    assert response.headers["X-Agent-Context-Cleared"] == "true"
    assert "agent-secret-123" not in caplog.text
    assert "/agents/<agent>/boom" in caplog.text
    assert get_request_context() is None
