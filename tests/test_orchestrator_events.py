from src.common.metrics import MetricsCollector
from src.orchestrator.engine import OrchestrationEngine


def test_unknown_event_type_is_quarantined_without_state_mutation():
    engine = OrchestrationEngine(metrics_collector=MetricsCollector())
    assert engine.dispatch_event(
        {
            "type": "run.started",
            "entity_id": "run-private-123",
            "revision": 7,
            "attempt": 2,
            "lifecycle": "running",
        }
    )

    accepted_state = engine.get_event_state("run-private-123")
    assert not engine.dispatch_event(
        {
            "type": "run.rolled-forward",
            "entity_id": "run-private-123",
            "revision": 8,
            "attempt": 3,
            "lifecycle": "completed",
            "payload": {"token": "do-not-log"},
        }
    )

    assert engine.get_event_state("run-private-123") == accepted_state
    quarantined = engine.quarantined_events()
    assert quarantined[-1]["decision"] == "unknown_event_type"
    assert quarantined[-1]["entity_ref"] != "run-private-123"
    assert "do-not-log" not in repr(quarantined)

    snapshot = engine.metrics.snapshot()
    assert snapshot["counters"]["orchestrator.events.accepted"] == 1
    assert snapshot["counters"]["orchestrator.events.quarantined"] == 1
    counters = snapshot["counters"]
    assert counters["orchestrator.events.quarantined.unknown_event_type"] == 1


def test_stale_revision_event_is_rejected_during_rolling_upgrade():
    engine = OrchestrationEngine(metrics_collector=MetricsCollector())
    assert engine.dispatch_event(
        {
            "type": "task.started",
            "entity_id": "task-42",
            "revision": 11,
            "attempt": 4,
            "lifecycle": "running",
        }
    )

    assert not engine.dispatch_event(
        {
            "type": "task.completed",
            "entity_id": "task-42",
            "revision": 10,
            "attempt": 5,
            "lifecycle": "completed",
        }
    )

    state = engine.get_event_state("task-42")
    assert state["lifecycle"] == "running"
    assert state["revision"] == 11
    assert engine.quarantined_events()[-1]["decision"] == "stale_revision"


def test_policy_lifecycle_mismatch_rejects_before_commit():
    engine = OrchestrationEngine(metrics_collector=MetricsCollector())

    assert not engine.dispatch_event(
        {
            "type": "handler.completed",
            "entity_id": "handler-7",
            "revision": 1,
            "attempt": 1,
            "lifecycle": "running",
        }
    )

    assert engine.get_event_state("handler-7") is None
    assert (
        engine.quarantined_events()[-1]["decision"]
        == "policy_lifecycle_mismatch"
    )


def test_terminal_lifecycle_rewrite_is_quarantined():
    engine = OrchestrationEngine(metrics_collector=MetricsCollector())
    assert engine.dispatch_event(
        {
            "type": "run.started",
            "entity_id": "run-terminal",
            "revision": 1,
            "attempt": 1,
            "lifecycle": "running",
        }
    )
    assert engine.dispatch_event(
        {
            "type": "run.completed",
            "entity_id": "run-terminal",
            "revision": 2,
            "attempt": 1,
            "lifecycle": "completed",
        }
    )

    assert not engine.dispatch_event(
        {
            "type": "run.started",
            "entity_id": "run-terminal",
            "revision": 3,
            "attempt": 2,
            "lifecycle": "running",
        }
    )

    assert engine.get_event_state("run-terminal")["lifecycle"] == "completed"
    assert engine.quarantined_events()[-1]["decision"] == "terminal_lifecycle"


def test_missing_entity_id_is_quarantined_without_state():
    engine = OrchestrationEngine(metrics_collector=MetricsCollector())

    assert not engine.dispatch_event(
        {
            "type": "task.started",
            "revision": 1,
            "attempt": 1,
            "lifecycle": "running",
        }
    )

    assert engine.event_audit_records()[-1]["decision"] == "missing_entity_id"
    assert engine.quarantined_events()[-1]["entity_ref"] == "missing"


def test_invalid_version_marker_is_quarantined_without_state():
    engine = OrchestrationEngine(metrics_collector=MetricsCollector())

    assert not engine.dispatch_event(
        {
            "type": "handler.started",
            "entity_id": "handler-invalid-version",
            "revision": "not-a-number",
            "attempt": 1,
            "lifecycle": "running",
        }
    )

    assert engine.get_event_state("handler-invalid-version") is None
    assert (
        engine.quarantined_events()[-1]["decision"]
        == "invalid_version_marker"
    )
