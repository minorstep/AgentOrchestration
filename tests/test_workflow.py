from src.orchestrator.workflow import StepStatus, WorkflowManager, WorkflowStep


class TestWorkflowCompensation:
    def setup_method(self):
        self.manager = WorkflowManager()

    def test_partial_rollback_blocks_downstream_with_sanitized_audit(self):
        calls = []
        workflow = self.manager.create_workflow("registration")

        def reserve_resource():
            calls.append("reserve")
            return {"secret": "not-audit-safe"}

        def rollback_resource():
            calls.append("rollback")
            raise RuntimeError("rollback transport included private token")

        def failing_binding():
            calls.append("bind")
            raise RuntimeError("binding rejected")

        def downstream_dispatch():
            calls.append("downstream")

        workflow.add_step(
            WorkflowStep(
                "reserve-resource",
                reserve_resource,
                compensate=rollback_resource,
            )
        )
        workflow.add_step(WorkflowStep("bind-policy", failing_binding))
        downstream = WorkflowStep("dispatch-downstream", downstream_dispatch)
        workflow.add_step(downstream)

        assert not self.manager.execute_workflow(workflow.id)

        assert calls == ["reserve", "bind", "rollback"]
        assert workflow.status is StepStatus.FAILED
        assert workflow.blocked_reason == "partial_rollback"
        assert workflow.steps[0].status is StepStatus.COMPENSATION_FAILED
        assert downstream.status is StepStatus.BLOCKED
        assert downstream.blocked_reason == "partial_rollback"
        assert not self.manager.dispatch_step(workflow.id, downstream.id)
        assert calls == ["reserve", "bind", "rollback"]

        audit_events = [record["event"] for record in workflow.audit_records]
        assert "compensation_failed" in audit_events
        assert "downstream_blocked" in audit_events
        assert "dispatch_blocked" in audit_events
        assert any(
            record["event"] == "downstream_blocked"
            and record["reason"] == "partial_rollback"
            and record["step_name"] == "dispatch-downstream"
            for record in workflow.audit_records
        )
        assert "secret" not in str(workflow.audit_records)
        assert "token" not in str(workflow.audit_records)

    def test_successful_compensation_blocks_later_steps_after_failure(self):
        calls = []
        workflow = self.manager.create_workflow("compensated-failure")

        def create_binding():
            calls.append("create")

        def rollback_binding():
            calls.append("rollback")

        def reject_transition():
            calls.append("reject")
            raise RuntimeError("policy violation")

        def downstream_dispatch():
            calls.append("downstream")

        first = WorkflowStep(
            "create-binding",
            create_binding,
            compensate=rollback_binding,
        )
        second = WorkflowStep("reject-transition", reject_transition)
        third = WorkflowStep("downstream-dispatch", downstream_dispatch)
        workflow.add_step(first).add_step(second).add_step(third)

        assert not self.manager.execute_workflow(workflow.id)

        assert calls == ["create", "reject", "rollback"]
        assert first.compensation_error is None
        assert workflow.blocked_reason == "failed_step"
        assert first.status is StepStatus.COMPENSATED
        assert third.status is StepStatus.BLOCKED
        assert third.blocked_reason == "failed_step"
        assert not self.manager.dispatch_step(workflow.id, third.id)
        assert calls == ["create", "reject", "rollback"]
        assert any(
            record["event"] == "compensation_completed"
            and record["step_name"] == "create-binding"
            for record in workflow.audit_records
        )
