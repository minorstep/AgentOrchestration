from src.orchestrator.engine import OrchestrationEngine


def test_event_intake_rejects_cross_tenant_transition():
    engine = OrchestrationEngine()

    assert engine.ingest_event(
        {
            "type": "run.lifecycle",
            "run_id": "run-1",
            "tenant_id": "tenant-a",
            "state": "queued",
            "revision": 1,
            "attempt": 1,
            "payload": {"secret": "do-not-record"},
        }
    )

    assert not engine.ingest_event(
        {
            "type": "run.lifecycle",
            "run_id": "run-1",
            "tenant_id": "tenant-b",
            "state": "running",
            "revision": 2,
            "attempt": 1,
            "payload": {"secret": "do-not-record"},
        }
    )

    assert engine.event_state("run-1") == {
        "tenant_id": "tenant-a",
        "revision": 1,
        "attempt": 1,
        "state": "queued",
    }
    assert engine.event_audit()[-1]["reason"] == "tenant_mismatch"
    assert "payload" not in engine.event_audit()[-1]


def test_event_intake_rejects_stale_attempt_and_revision():
    engine = OrchestrationEngine()

    assert engine.ingest_event(
        {
            "type": "task.lifecycle",
            "task_id": "task-1",
            "tenant_id": "tenant-a",
            "state": "running",
            "revision": 3,
            "attempt": 2,
        }
    )
    assert not engine.ingest_event(
        {
            "type": "task.lifecycle",
            "task_id": "task-1",
            "tenant_id": "tenant-a",
            "state": "completed",
            "revision": 2,
            "attempt": 2,
        }
    )
    assert not engine.ingest_event(
        {
            "type": "task.lifecycle",
            "task_id": "task-1",
            "tenant_id": "tenant-a",
            "state": "completed",
            "revision": 4,
            "attempt": 1,
        }
    )

    assert engine.event_state("task-1")["state"] == "running"
    assert [item["reason"] for item in engine.event_audit()[-2:]] == [
        "stale_revision",
        "stale_attempt",
    ]


def test_event_intake_accepts_monotonic_same_tenant_transition():
    engine = OrchestrationEngine()

    assert engine.ingest_event(
        {
            "type": "run.lifecycle",
            "run_id": "run-2",
            "tenant_id": "tenant-a",
            "state": "queued",
            "revision": 1,
            "attempt": 1,
        }
    )
    assert engine.ingest_event(
        {
            "type": "run.lifecycle",
            "run_id": "run-2",
            "tenant_id": "tenant-a",
            "state": "running",
            "revision": 2,
            "attempt": 1,
        }
    )
    assert engine.ingest_event(
        {
            "type": "run.lifecycle",
            "run_id": "run-2",
            "tenant_id": "tenant-a",
            "state": "completed",
            "revision": 3,
            "attempt": 1,
        }
    )

    assert engine.event_state("run-2")["state"] == "completed"


def test_event_intake_rejects_terminal_lifecycle_rewrite():
    engine = OrchestrationEngine()

    for revision, state in [(1, "queued"), (2, "running"), (3, "completed")]:
        assert engine.ingest_event(
            {
                "type": "run.lifecycle",
                "run_id": "run-3",
                "tenant_id": "tenant-a",
                "state": state,
                "revision": revision,
                "attempt": 1,
            }
        )

    assert not engine.ingest_event(
        {
            "type": "run.lifecycle",
            "run_id": "run-3",
            "tenant_id": "tenant-a",
            "state": "running",
            "revision": 4,
            "attempt": 1,
        }
    )

    assert engine.event_state("run-3")["state"] == "completed"
    assert engine.event_audit()[-1]["reason"] == "invalid_lifecycle"
