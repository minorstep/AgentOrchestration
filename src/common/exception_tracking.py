"""Exception tracking context sanitization."""

from typing import Any, Dict, Iterable, Optional
from uuid import uuid4

from src.common.metrics import metrics


RAW_FIELD_NAMES = {
    "payload",
    "raw_payload",
    "body",
    "input",
    "inputs",
    "args",
    "kwargs",
    "locals",
    "local_variables",
    "request",
    "response",
    "secret",
    "token",
    "password",
    "authorization",
    "credentials",
}

LOOKUP_FIELDS = {
    "task_id",
    "error_class",
}


class ExceptionContextSanitizer:
    """Builds exception events without raw task data or captured locals."""

    def sanitize(
        self,
        error: BaseException,
        *,
        task_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        local_variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event = {
            "event_id": str(uuid4()),
            "task_id": task_id or self._task_id(context),
            "error_class": error.__class__.__name__,
        }
        sanitized_context = self._sanitize_context(context or {})
        if sanitized_context:
            event["context"] = sanitized_context
        if local_variables:
            event["local_variable_count"] = len(local_variables)
        return event

    def _sanitize_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        sanitized: Dict[str, Any] = {}
        for key, value in context.items():
            if self._is_raw_field(key):
                continue
            if key in LOOKUP_FIELDS and isinstance(value, str):
                sanitized[key] = value
                continue
            if isinstance(value, dict):
                nested = self._sanitize_context(value)
                if nested:
                    sanitized[key] = nested
                continue
            if isinstance(value, (list, tuple)):
                nested_values = [
                    item
                    for item in self._sanitize_sequence(value)
                    if item is not None
                ]
                if nested_values:
                    sanitized[key] = nested_values
                continue
            if isinstance(value, (int, float, bool)) or value is None:
                sanitized[key] = value
        return sanitized

    def _sanitize_sequence(self, values: Iterable[Any]) -> Iterable[Any]:
        for value in values:
            if isinstance(value, dict):
                nested = self._sanitize_context(value)
                yield nested or None
            elif isinstance(value, (str, int, float, bool)) or value is None:
                yield value

    @staticmethod
    def _task_id(context: Optional[Dict[str, Any]]) -> Optional[str]:
        if not context:
            return None
        task_id = context.get("task_id") or context.get("id")
        return task_id if isinstance(task_id, str) else None

    @staticmethod
    def _is_raw_field(key: str) -> bool:
        key_lower = key.lower()
        return key_lower in RAW_FIELD_NAMES or key_lower.endswith("_payload")


class ExceptionTracker:
    def __init__(self, sanitizer=None, metrics_collector=None):
        self.sanitizer = sanitizer or ExceptionContextSanitizer()
        self.metrics = metrics_collector or metrics
        self._events: Dict[str, Dict[str, Any]] = {}
        self._index: Dict[tuple, str] = {}

    def capture(
        self,
        error: BaseException,
        *,
        task: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        local_variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        combined_context: Dict[str, Any] = {}
        if task:
            combined_context.update(task)
            task_id = task_id or task.get("id")
        if context:
            combined_context.update(context)

        event = self.sanitizer.sanitize(
            error,
            task_id=task_id,
            context=combined_context,
            local_variables=local_variables,
        )
        self._events[event["event_id"]] = event
        self._index[(event.get("task_id"), event["error_class"])] = event[
            "event_id"
        ]
        self.metrics.increment("exceptions.tracked.sanitized")
        return dict(event)

    def find(self, task_id: str, error_class: str) -> Optional[Dict[str, Any]]:
        event_id = self._index.get((task_id, error_class))
        if not event_id:
            return None
        return dict(self._events[event_id])
