"""Run event stream service helpers."""

from typing import Any, Dict, List, Protocol


MAX_RUN_EVENTS_LIMIT = 100
MAX_RUN_EVENTS_WINDOW = 1000
DEFAULT_RUN_EVENTS_LIMIT = 100


class RunEventsValidationError(ValueError):
    """Raised when a run-events request should fail with a stable 4xx."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class RunEventStore(Protocol):
    def list_run_events(
        self,
        workspace_id: str,
        run_id: str,
        *,
        limit: int,
        offset: int,
    ) -> List[Dict[str, Any]]:
        """Return events for a run after the caller has passed validation."""


class InMemoryRunEventStore:
    def __init__(self):
        self._events: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def add_run_event(
        self,
        workspace_id: str,
        run_id: str,
        event: Dict[str, Any],
    ) -> None:
        workspace_events = self._events.setdefault(workspace_id, {})
        workspace_events.setdefault(run_id, []).append(dict(event))

    def list_run_events(
        self,
        workspace_id: str,
        run_id: str,
        *,
        limit: int,
        offset: int,
    ) -> List[Dict[str, Any]]:
        events = self._events.get(workspace_id, {}).get(run_id, [])
        return [dict(event) for event in events[offset:offset + limit]]


run_event_store = InMemoryRunEventStore()


def get_run_event_store() -> RunEventStore:
    return run_event_store


def list_run_events(
    workspace_id: str,
    run_id: str,
    *,
    limit: int,
    offset: int,
    store: RunEventStore,
) -> Dict[str, Any]:
    _validate_run_event_request(workspace_id, run_id, limit, offset)
    events = store.list_run_events(
        workspace_id,
        run_id,
        limit=limit,
        offset=offset,
    )
    return {
        "events": events,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "window": MAX_RUN_EVENTS_WINDOW,
        },
    }


def resolve_workspace_id(
    query_workspace_id: str = None,
    header_workspace_id: str = None,
) -> str:
    query_value = _normalise_optional_scope(query_workspace_id)
    header_value = _normalise_optional_scope(header_workspace_id)
    if query_value and header_value and query_value != header_value:
        raise RunEventsValidationError(
            422,
            "workspace_id query and X-Workspace-Id header must match",
        )
    workspace_id = header_value or query_value
    if not workspace_id:
        raise RunEventsValidationError(422, "workspace_id is required")
    return workspace_id


def _validate_run_event_request(
    workspace_id: str,
    run_id: str,
    limit: int,
    offset: int,
) -> None:
    if not workspace_id or not workspace_id.strip():
        raise RunEventsValidationError(422, "workspace_id is required")
    if not run_id or not run_id.strip():
        raise RunEventsValidationError(422, "run_id is required")
    if limit < 1:
        raise RunEventsValidationError(422, "limit must be at least 1")
    if limit > MAX_RUN_EVENTS_LIMIT:
        raise RunEventsValidationError(
            422,
            f"limit must be no greater than {MAX_RUN_EVENTS_LIMIT}",
        )
    if offset < 0:
        raise RunEventsValidationError(422, "offset must be non-negative")
    if offset + limit > MAX_RUN_EVENTS_WINDOW:
        raise RunEventsValidationError(
            422,
            f"offset plus limit must not exceed {MAX_RUN_EVENTS_WINDOW}",
        )


def _normalise_optional_scope(value: str = None) -> str:
    if value is None:
        return ""
    return value.strip()
