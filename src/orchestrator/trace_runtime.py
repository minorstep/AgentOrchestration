"""Bounded trace aggregation for orchestration runtime events."""

from dataclasses import dataclass
import json
from typing import Any, Dict, Iterable, List, Mapping, Tuple


@dataclass(frozen=True)
class TraceAggregationStats:
    event_count: int
    total_bytes: int
    max_events: int
    max_total_bytes: int
    max_event_bytes: int


class TraceLimitExceeded(ValueError):
    def __init__(self, metric: str, attempted: int, limit: int):
        super().__init__(
            f"Trace aggregation {metric} limit exceeded: {attempted} > {limit}"
        )
        self.metric = metric
        self.attempted = attempted
        self.limit = limit


class TraceAggregator:
    """Collect trace events while enforcing server-side memory limits."""

    def __init__(
        self,
        max_events: int = 1000,
        max_total_bytes: int = 1024 * 1024,
        max_event_bytes: int = 64 * 1024,
    ):
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        if max_total_bytes < 1:
            raise ValueError("max_total_bytes must be at least 1")
        if max_event_bytes < 1:
            raise ValueError("max_event_bytes must be at least 1")

        self.max_events = max_events
        self.max_total_bytes = max_total_bytes
        self.max_event_bytes = max_event_bytes
        self._events: List[Dict[str, Any]] = []
        self._total_bytes = 0

    def add(self, event: Mapping[str, Any]) -> None:
        prepared, size = self._prepare_event(event)
        self._validate_growth([(prepared, size)])
        self._events.append(prepared)
        self._total_bytes += size

    def extend(self, events: Iterable[Mapping[str, Any]]) -> None:
        prepared = [self._prepare_event(event) for event in events]
        self._validate_growth(prepared)
        for event, size in prepared:
            self._events.append(event)
            self._total_bytes += size

    def snapshot(self) -> List[Dict[str, Any]]:
        return [dict(event) for event in self._events]

    def clear(self) -> None:
        self._events.clear()
        self._total_bytes = 0

    @property
    def stats(self) -> TraceAggregationStats:
        return TraceAggregationStats(
            event_count=len(self._events),
            total_bytes=self._total_bytes,
            max_events=self.max_events,
            max_total_bytes=self.max_total_bytes,
            max_event_bytes=self.max_event_bytes,
        )

    def _prepare_event(
        self,
        event: Mapping[str, Any],
    ) -> Tuple[Dict[str, Any], int]:
        if not isinstance(event, Mapping):
            raise TypeError("trace event must be a mapping")

        prepared = dict(event)
        serialized = json.dumps(
            prepared,
            default=str,
            sort_keys=True,
            separators=(",", ":"),
        )
        size = len(serialized.encode("utf-8"))
        if size > self.max_event_bytes:
            raise TraceLimitExceeded("event_bytes", size, self.max_event_bytes)
        return prepared, size

    def _validate_growth(
        self,
        prepared: List[Tuple[Dict[str, Any], int]],
    ) -> None:
        attempted_events = len(self._events) + len(prepared)
        if attempted_events > self.max_events:
            raise TraceLimitExceeded(
                "event_count",
                attempted_events,
                self.max_events,
            )

        attempted_bytes = self._total_bytes + sum(size for _, size in prepared)
        if attempted_bytes > self.max_total_bytes:
            raise TraceLimitExceeded(
                "total_bytes",
                attempted_bytes,
                self.max_total_bytes,
            )


def aggregate_trace_events(
    events: Iterable[Mapping[str, Any]],
    max_events: int = 1000,
    max_total_bytes: int = 1024 * 1024,
    max_event_bytes: int = 64 * 1024,
) -> List[Dict[str, Any]]:
    aggregator = TraceAggregator(
        max_events=max_events,
        max_total_bytes=max_total_bytes,
        max_event_bytes=max_event_bytes,
    )
    aggregator.extend(events)
    return aggregator.snapshot()
