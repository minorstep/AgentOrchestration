import logging

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from src.api.middleware import SECURITY_HEADERS, clear_request_state
from src.api.server import create_app


def assert_security_headers(response):
    for header, value in SECURITY_HEADERS.items():
        assert response.headers[header] == value


def test_security_headers_are_applied_to_normal_responses():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert_security_headers(response)


def test_security_headers_are_applied_to_rejected_responses():
    client = TestClient(create_app())

    response = client.get("/api/v2/agents")

    assert response.status_code == 401
    assert response.text == "Unauthorized"
    assert_security_headers(response)


def test_exception_responses_do_not_leak_request_material(caplog):
    app = create_app()

    @app.get("/boom")
    async def boom(request: Request):
        request.state.secret = "payload-token"
        raise RuntimeError("database password payload-token")

    client = TestClient(app, raise_server_exceptions=False)

    with caplog.at_level(logging.ERROR, logger="src.api.middleware"):
        response = client.get(
            "/boom?api_key=payload-token",
            headers={"Authorization": "Bearer payload-token"},
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    assert_security_headers(response)
    assert "payload-token" not in caplog.text
    assert "api_key" not in caplog.text


def test_request_local_state_can_be_cleared():
    request = Request(
        {"type": "http", "method": "GET", "path": "/", "headers": []}
    )
    request.state.secret = "payload-token"

    clear_request_state(request)

    with pytest.raises(AttributeError):
        request.state.secret


def test_security_headers_are_applied_to_http_exception_responses():
    app = create_app()

    @app.get("/forbidden")
    async def forbidden():
        raise HTTPException(status_code=403, detail="Forbidden")

    client = TestClient(app)

    response = client.get("/forbidden")

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert_security_headers(response)
