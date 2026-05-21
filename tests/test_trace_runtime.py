import pytest

from src.orchestrator.trace_runtime import (
    TraceAggregator,
    TraceLimitExceeded,
    aggregate_trace_events,
)


class TestTraceAggregator:
    def test_add_records_bounded_trace_event(self):
        aggregator = TraceAggregator(
            max_events=2,
            max_total_bytes=256,
            max_event_bytes=128,
        )

        aggregator.add({"run_id": "run-1", "message": "started"})

        assert aggregator.snapshot() == [
            {"run_id": "run-1", "message": "started"}
        ]
        assert aggregator.stats.event_count == 1
        assert aggregator.stats.total_bytes > 0

    def test_rejects_oversized_event_before_mutation(self):
        aggregator = TraceAggregator(
            max_events=2,
            max_total_bytes=256,
            max_event_bytes=32,
        )

        with pytest.raises(TraceLimitExceeded) as exc:
            aggregator.add({"message": "x" * 80})

        assert exc.value.metric == "event_bytes"
        assert aggregator.snapshot() == []
        assert aggregator.stats.total_bytes == 0

    def test_rejects_total_memory_growth_before_mutation(self):
        aggregator = TraceAggregator(
            max_events=5,
            max_total_bytes=70,
            max_event_bytes=128,
        )
        aggregator.add({"message": "x" * 20})

        with pytest.raises(TraceLimitExceeded) as exc:
            aggregator.add({"message": "y" * 40})

        assert exc.value.metric == "total_bytes"
        assert aggregator.snapshot() == [{"message": "x" * 20}]

    def test_rejects_event_count_growth_before_mutation(self):
        aggregator = TraceAggregator(
            max_events=1,
            max_total_bytes=256,
            max_event_bytes=128,
        )
        aggregator.add({"message": "first"})

        with pytest.raises(TraceLimitExceeded) as exc:
            aggregator.add({"message": "second"})

        assert exc.value.metric == "event_count"
        assert aggregator.snapshot() == [{"message": "first"}]

    def test_extend_is_atomic_when_batch_exceeds_limit(self):
        aggregator = TraceAggregator(
            max_events=3,
            max_total_bytes=80,
            max_event_bytes=128,
        )
        aggregator.add({"message": "existing"})

        with pytest.raises(TraceLimitExceeded):
            aggregator.extend([{"message": "next"}, {"message": "z" * 60}])

        assert aggregator.snapshot() == [{"message": "existing"}]

    def test_aggregate_trace_events_returns_snapshot(self):
        snapshot = aggregate_trace_events(
            [{"event": "started"}, {"event": "finished"}],
            max_events=2,
            max_total_bytes=128,
            max_event_bytes=64,
        )

        assert snapshot == [{"event": "started"}, {"event": "finished"}]
