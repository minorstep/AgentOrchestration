import pytest

from src.orchestrator.workflow import (
    ReservedMetadataKeyError,
    StepStatus,
    WorkflowManager,
    WorkflowStep,
)


def test_create_workflow_rejects_reserved_metadata_without_registration():
    manager = WorkflowManager()

    with pytest.raises(ReservedMetadataKeyError) as exc:
        manager.create_workflow("reserved", metadata={"status": "completed"})

    assert exc.value.key_path == "status"
    assert manager.list_workflows() == []
    assert manager.audit_log == [
        {
            "event": "workflow_registration_rejected",
            "reason": "reserved_metadata_key",
            "key_path": "status",
        }
    ]
    assert "completed" not in str(manager.audit_log)


def test_add_step_rejects_nested_reserved_metadata_and_preserves_state():
    manager = WorkflowManager()
    workflow = manager.create_workflow("deploy", metadata={"owner": "release"})
    step = WorkflowStep("prepare", lambda: "ok", metadata={"label": "safe"})
    step._metadata = {"operator": {"queue": "fast-lane"}}

    with pytest.raises(ReservedMetadataKeyError) as exc:
        workflow.add_step(step)

    assert exc.value.key_path == "operator.queue"
    assert workflow.status is StepStatus.PENDING
    assert step.status is StepStatus.PENDING
    assert workflow.steps == []
    assert manager.audit_log[-1] == {
        "event": "workflow_step_rejected",
        "reason": "reserved_metadata_key",
        "key_path": "operator.queue",
        "workflow_status": "pending",
        "step_status": "pending",
        "workflow_id": workflow.id,
    }
    assert "fast-lane" not in str(manager.audit_log)


def test_execute_workflow_defers_mutated_metadata_without_running_handlers():
    manager = WorkflowManager()
    workflow = manager.create_workflow("release", metadata={"owner": "ops"})
    called = []
    workflow.add_step(WorkflowStep("ship", lambda: called.append("ran")))
    workflow._metadata = {"policy": [{"workflowId": "spoofed"}]}

    assert manager.execute_workflow(workflow.id) is False

    assert called == []
    assert workflow.status is StepStatus.PENDING
    assert workflow.steps[0].status is StepStatus.PENDING
    assert manager.audit_log[-1] == {
        "event": "workflow_execution_deferred",
        "reason": "reserved_metadata_key",
        "key_path": "policy[0].workflowId",
        "workflow_id": workflow.id,
        "workflow_status": "pending",
    }
    assert "spoofed" not in str(manager.audit_log)


def test_metadata_properties_return_copies_and_valid_metadata_executes():
    manager = WorkflowManager()
    workflow = manager.create_workflow(
        "valid",
        metadata={"owner": {"team": "platform"}, "tags": ["canary"]},
    )
    step = WorkflowStep(
        "collect",
        lambda: {"ok": True},
        metadata={"display": {"label": "collect"}},
    )
    workflow.add_step(step)

    workflow_metadata = workflow.metadata
    step_metadata = step.metadata
    workflow_metadata["owner"]["team"] = "mutated"
    step_metadata["display"]["label"] = "mutated"

    assert workflow.metadata["owner"]["team"] == "platform"
    assert step.metadata["display"]["label"] == "collect"
    assert manager.execute_workflow(workflow.id) is True
    assert workflow.status is StepStatus.COMPLETED
    assert step.status is StepStatus.COMPLETED
    assert step.result == {"ok": True}
