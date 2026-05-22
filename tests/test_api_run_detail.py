from contextlib import contextmanager

from fastapi.testclient import TestClient

from src.api import routes
from src.api.server import create_app


class CountingRunStore:
    def __init__(self):
        self.lookups = 0
        self.runs = {
            "run-123": {
                "id": "run-123",
                "status": "succeeded",
                "tenant_id": "tenant-a",
                "created_at": 10.0,
                "updated_at": 12.0,
                "result": {"ok": True},
                "worker_id": "worker-private",
                "internal_state": "reserved",
                "debug_context": {"trace": "private"},
                "scheduler_lock": "lock-private",
            }
        }

    def get(self, run_id):
        self.lookups += 1
        run = self.runs.get(run_id)
        return dict(run) if run is not None else None


@contextmanager
def installed_run_service(store):
    original = routes.run_detail_service
    routes.run_detail_service = routes.RunDetailService(store)
    try:
        yield
    finally:
        routes.run_detail_service = original


def test_run_detail_public_response_excludes_admin_fields():
    store = CountingRunStore()
    with installed_run_service(store):
        response = TestClient(create_app()).get(
            "/api/v2/runs/run-123",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 200
    assert store.lookups == 1
    body = response.json()
    assert body["id"] == "run-123"
    assert body["result"] == {"ok": True}
    assert "admin" not in body
    assert "worker_id" not in body
    assert "debug_context" not in body


def test_run_detail_admin_response_requires_admin_header():
    store = CountingRunStore()
    with installed_run_service(store):
        response = TestClient(create_app()).get(
            "/api/v2/runs/run-123?include_admin_fields=true",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 403
    assert store.lookups == 0


def test_run_detail_admin_response_includes_admin_fields_for_admin():
    store = CountingRunStore()
    with installed_run_service(store):
        response = TestClient(create_app()).get(
            "/api/v2/runs/run-123?include_admin_fields=true",
            headers={
                "Authorization": "Bearer token",
                "X-Admin": "true",
            },
        )

    assert response.status_code == 200
    assert store.lookups == 1
    body = response.json()
    assert body["admin"]["worker_id"] == "worker-private"
    assert body["admin"]["internal_state"] == "reserved"
    assert body["admin"]["debug_context"] == {"trace": "private"}


def test_run_detail_malformed_id_fails_before_lookup():
    store = CountingRunStore()
    with installed_run_service(store):
        response = TestClient(create_app()).get(
            "/api/v2/runs/bad%20id",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "Malformed run id"
    assert store.lookups == 0


def test_run_detail_missing_bearer_fails_before_lookup():
    store = CountingRunStore()
    with installed_run_service(store):
        response = TestClient(create_app()).get("/api/v2/runs/run-123")

    assert response.status_code == 401
    assert store.lookups == 0
