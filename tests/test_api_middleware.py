import logging

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.api.middleware import ErrorSanitizingMiddleware


def make_client():
    app = FastAPI()
    app.add_middleware(ErrorSanitizingMiddleware)

    @app.get("/ok")
    async def ok(request: Request):
        request.state.private_token = "secret-state-token"
        return {"ok": True}

    @app.get("/boom")
    async def boom(request: Request):
        request.state.private_token = "secret-state-token"
        raise RuntimeError(
            "database password=secret token=abc authorization=Bearer abc"
        )

    return TestClient(app, raise_server_exceptions=False)


def test_normal_request_gets_sanitized_marker():
    response = make_client().get("/ok")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.headers["X-Error-Sanitized"] == "true"


def test_exception_response_hides_sensitive_detail(caplog):
    caplog.set_level(logging.ERROR, logger="src.api.middleware")

    response = make_client().get(
        "/boom?token=query-secret",
        headers={
            "Authorization": "Bearer request-secret",
            "X-Api-Key": "api-secret",
        },
    )

    assert response.status_code == 500
    assert response.headers["X-Error-Sanitized"] == "true"
    assert response.json() == {
        "error": "internal_server_error",
        "message": "Internal server error",
    }
    assert "request-secret" not in response.text
    assert "api-secret" not in response.text
    assert "secret-state-token" not in response.text
    assert "query-secret" not in response.text
    assert "database password" not in response.text

    log_text = caplog.text
    assert "Unhandled request error" in log_text
    assert "request-secret" not in log_text
    assert "api-secret" not in log_text
    assert "secret-state-token" not in log_text
    assert "query-secret" not in log_text
    assert "database password" not in log_text

    logged_request = caplog.records[0].request
    assert logged_request["query"] == "[redacted]"
    assert logged_request["headers"]["authorization"] == "[redacted]"
    assert logged_request["headers"]["x-api-key"] == "[redacted]"


def test_request_state_is_cleared_after_exception():
    seen_states = []
    app = FastAPI()

    class InspectingMiddleware(ErrorSanitizingMiddleware):
        async def dispatch(self, request, call_next):
            response = await super().dispatch(request, call_next)
            seen_states.append(dict(request.scope.get("state", {})))
            return response

    app.add_middleware(InspectingMiddleware)

    @app.get("/boom")
    async def boom(request: Request):
        request.state.private_token = "secret-state-token"
        raise RuntimeError("secret failure")

    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/boom")

    assert response.status_code == 500
    assert seen_states == [{}]


def test_rejected_request_gets_marker_and_clears_state():
    seen_states = []
    app = FastAPI()

    class RejectingMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.private_token = "secret-state-token"
            return JSONResponse({"error": "rejected"}, status_code=403)

    class InspectingMiddleware(ErrorSanitizingMiddleware):
        async def dispatch(self, request, call_next):
            response = await super().dispatch(request, call_next)
            seen_states.append(dict(request.scope.get("state", {})))
            return response

    app.add_middleware(RejectingMiddleware)
    app.add_middleware(InspectingMiddleware)

    @app.get("/blocked")
    async def blocked():
        return {"ok": False}

    response = TestClient(app).get("/blocked")

    assert response.status_code == 403
    assert response.headers["X-Error-Sanitized"] == "true"
    assert response.text == '{"error":"rejected"}'
    assert "secret-state-token" not in response.text
    assert seen_states == [{}]
