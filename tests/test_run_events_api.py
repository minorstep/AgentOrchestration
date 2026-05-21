from fastapi.testclient import TestClient

from src.api.run_events import (
    InMemoryRunEventStore,
    MAX_RUN_EVENTS_LIMIT,
    MAX_RUN_EVENTS_WINDOW,
    RunEventsValidationError,
    get_run_event_store,
    list_run_events,
)
from src.api.server import create_app


class CountingRunEventStore:
    def __init__(self):
        self.lookups = 0

    def list_run_events(
        self,
        workspace_id,
        run_id,
        *,
        limit,
        offset,
    ):
        self.lookups += 1
        return [
            {
                "id": f"{workspace_id}:{run_id}:1",
                "type": "run.started",
                "sequence": offset,
            }
        ][:limit]


def _client_with_store(store):
    app = create_app()
    app.dependency_overrides[get_run_event_store] = lambda: store
    return TestClient(app)


def test_authorized_run_events_request_uses_store_after_validation():
    store = CountingRunEventStore()
    client = _client_with_store(store)

    response = client.get(
        "/api/v2/runs/run-1/events",
        params={"workspace_id": "workspace-1", "limit": 1, "offset": 0},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert store.lookups == 1
    assert response.json() == {
        "events": [
            {
                "id": "workspace-1:run-1:1",
                "type": "run.started",
                "sequence": 0,
            }
        ],
        "pagination": {
            "limit": 1,
            "offset": 0,
            "window": MAX_RUN_EVENTS_WINDOW,
        },
    }


def test_workspace_header_can_scope_run_events_request():
    store = CountingRunEventStore()
    client = _client_with_store(store)

    response = client.get(
        "/api/v2/runs/run-1/events",
        params={"limit": 1, "offset": 0},
        headers={
            "Authorization": "Bearer test-token",
            "X-Workspace-Id": "workspace-header",
        },
    )

    assert response.status_code == 200
    assert response.json()["events"][0]["id"] == "workspace-header:run-1:1"
    assert store.lookups == 1


def test_missing_workspace_is_rejected_before_store_lookup():
    store = CountingRunEventStore()
    client = _client_with_store(store)

    response = client.get(
        "/api/v2/runs/run-1/events",
        params={"limit": 1},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "workspace_id is required"
    assert store.lookups == 0


def test_blank_workspace_header_is_rejected_before_store_lookup():
    store = CountingRunEventStore()
    client = _client_with_store(store)

    response = client.get(
        "/api/v2/runs/run-1/events",
        params={"limit": 1},
        headers={
            "Authorization": "Bearer test-token",
            "X-Workspace-Id": "   ",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "workspace_id is required"
    assert store.lookups == 0


def test_conflicting_workspace_scopes_are_rejected_before_store_lookup():
    store = CountingRunEventStore()
    client = _client_with_store(store)

    response = client.get(
        "/api/v2/runs/run-1/events",
        params={"workspace_id": "workspace-query", "limit": 1},
        headers={
            "Authorization": "Bearer test-token",
            "X-Workspace-Id": "workspace-header",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "workspace_id query and X-Workspace-Id header must match"
    )
    assert store.lookups == 0


def test_unauthorized_run_events_request_never_touches_store():
    store = CountingRunEventStore()
    client = _client_with_store(store)

    response = client.get(
        "/api/v2/runs/run-1/events",
        params={"workspace_id": "workspace-1", "limit": 1},
    )

    assert response.status_code == 401
    assert store.lookups == 0


def test_blank_bearer_token_never_touches_store():
    store = CountingRunEventStore()
    client = _client_with_store(store)

    response = client.get(
        "/api/v2/runs/run-1/events",
        params={"workspace_id": "workspace-1", "limit": 1},
        headers={"Authorization": "Bearer "},
    )

    assert response.status_code == 401
    assert store.lookups == 0


def test_malformed_limit_is_rejected_before_store_lookup():
    store = CountingRunEventStore()
    client = _client_with_store(store)

    response = client.get(
        "/api/v2/runs/run-1/events",
        params={"workspace_id": "workspace-1", "limit": "many"},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
    assert store.lookups == 0


def test_zero_limit_is_rejected_before_store_lookup():
    store = CountingRunEventStore()
    client = _client_with_store(store)

    response = client.get(
        "/api/v2/runs/run-1/events",
        params={"workspace_id": "workspace-1", "limit": 0},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
    assert store.lookups == 0


def test_negative_offset_is_rejected_before_store_lookup():
    store = CountingRunEventStore()
    client = _client_with_store(store)

    response = client.get(
        "/api/v2/runs/run-1/events",
        params={"workspace_id": "workspace-1", "limit": 1, "offset": -1},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
    assert store.lookups == 0


def test_limit_above_cap_is_rejected_before_store_lookup():
    store = CountingRunEventStore()
    client = _client_with_store(store)

    response = client.get(
        "/api/v2/runs/run-1/events",
        params={
            "workspace_id": "workspace-1",
            "limit": MAX_RUN_EVENTS_LIMIT + 1,
        },
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
    assert store.lookups == 0


def test_exact_pagination_window_boundary_reaches_bounded_lookup():
    store = CountingRunEventStore()
    client = _client_with_store(store)

    response = client.get(
        "/api/v2/runs/run-1/events",
        params={
            "workspace_id": "workspace-1",
            "limit": MAX_RUN_EVENTS_LIMIT,
            "offset": MAX_RUN_EVENTS_WINDOW - MAX_RUN_EVENTS_LIMIT,
        },
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.json()["pagination"] == {
        "limit": MAX_RUN_EVENTS_LIMIT,
        "offset": MAX_RUN_EVENTS_WINDOW - MAX_RUN_EVENTS_LIMIT,
        "window": MAX_RUN_EVENTS_WINDOW,
    }
    assert store.lookups == 1


def test_route_pagination_window_is_rejected_before_store_lookup():
    store = CountingRunEventStore()
    client = _client_with_store(store)

    response = client.get(
        "/api/v2/runs/run-1/events",
        params={
            "workspace_id": "workspace-1",
            "limit": MAX_RUN_EVENTS_LIMIT,
            "offset": MAX_RUN_EVENTS_WINDOW - MAX_RUN_EVENTS_LIMIT + 1,
        },
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        f"offset plus limit must not exceed {MAX_RUN_EVENTS_WINDOW}"
    )
    assert store.lookups == 0


def test_blank_run_id_is_rejected_before_store_lookup():
    store = CountingRunEventStore()

    try:
        list_run_events(
            "workspace-1",
            "   ",
            limit=1,
            offset=0,
            store=store,
        )
    except RunEventsValidationError as exc:
        assert exc.status_code == 422
        assert exc.detail == "run_id is required"
    else:
        raise AssertionError("expected blank run id validation to fail")

    assert store.lookups == 0


def test_run_event_store_is_scoped_by_workspace():
    store = InMemoryRunEventStore()
    store.add_run_event("workspace-a", "run-1", {"id": "event-a"})
    store.add_run_event("workspace-b", "run-1", {"id": "event-b"})

    response = list_run_events(
        "workspace-a",
        "run-1",
        limit=10,
        offset=0,
        store=store,
    )

    assert response["events"] == [{"id": "event-a"}]


def test_pagination_window_is_rejected_before_store_lookup():
    store = CountingRunEventStore()

    try:
        list_run_events(
            "workspace-1",
            "run-1",
            limit=MAX_RUN_EVENTS_LIMIT,
            offset=MAX_RUN_EVENTS_WINDOW - MAX_RUN_EVENTS_LIMIT + 1,
            store=store,
        )
    except RunEventsValidationError as exc:
        assert exc.status_code == 422
        assert "offset plus limit" in exc.detail
    else:
        raise AssertionError("expected pagination window validation to fail")

    assert store.lookups == 0
