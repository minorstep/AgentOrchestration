from src.orchestrator.workflow import StepStatus, WorkflowManager, WorkflowStep


class TestWorkflowConditionEvaluation:
    def setup_method(self):
        self.manager = WorkflowManager()
        self.workflow = self.manager.create_workflow("guarded")

    def test_condition_runs_before_step_transition_and_allows_safe_handler(self):
        seen_states = []

        def condition(workflow, step):
            seen_states.append((workflow.status, step.status))
            return True

        step = WorkflowStep("safe", lambda: "ok", condition=condition)
        self.workflow.add_step(step)

        assert self.manager.execute_workflow(self.workflow.id)
        assert seen_states == [(StepStatus.RUNNING, StepStatus.PENDING)]
        assert step.status is StepStatus.COMPLETED
        assert step.result == "ok"
        assert self.workflow.audit_log[-1]["decision"] == "accepted"

    def test_false_condition_skips_step_without_running_handler(self):
        called = []
        step = WorkflowStep("skip", lambda: called.append(True), condition=lambda: False)
        self.workflow.add_step(step)

        assert self.manager.execute_workflow(self.workflow.id)
        assert called == []
        assert step.status is StepStatus.SKIPPED
        assert self.workflow.status is StepStatus.COMPLETED
        assert self.workflow.audit_log[-1]["reason"] == "condition_false"

    def test_side_effecting_condition_is_rejected_and_lifecycle_state_is_restored(self):
        called = []
        first = WorkflowStep("side-effect", lambda: called.append("first"))
        second = WorkflowStep("later", lambda: called.append("second"))

        def condition(workflow, step):
            workflow.status = StepStatus.COMPLETED
            step.status = StepStatus.COMPLETED
            second.status = StepStatus.COMPLETED
            return True

        first.condition = condition
        self.workflow.add_step(first).add_step(second)

        assert not self.manager.execute_workflow(self.workflow.id)
        assert called == []
        assert self.workflow.status is StepStatus.FAILED
        assert first.status is StepStatus.FAILED
        assert second.status is StepStatus.PENDING
        assert first.error == "workflow condition rejected"
        assert self.workflow.audit_log[-1]["decision"] == "rejected"
        assert self.workflow.audit_log[-1]["reason"] == "condition_side_effect"

    def test_condition_cannot_mutate_workflow_graph_before_dispatch(self):
        injected = WorkflowStep("injected", lambda: "bad")

        def condition(workflow, step):
            workflow.add_step(injected)
            return True

        step = WorkflowStep("graph-guard", lambda: "ok", condition=condition)
        self.workflow.add_step(step)

        assert not self.manager.execute_workflow(self.workflow.id)
        assert self.workflow.steps == [step]
        assert self.workflow.get_step(injected.id) is None
        assert step.status is StepStatus.FAILED
        assert self.workflow.audit_log[-1]["reason"] == "condition_side_effect"

    def test_non_boolean_condition_is_rejected_before_handler_runs(self):
        called = []
        step = WorkflowStep("bad-return", lambda: called.append(True), condition=lambda: "yes")
        self.workflow.add_step(step)

        assert not self.manager.execute_workflow(self.workflow.id)
        assert called == []
        assert step.status is StepStatus.FAILED
        assert step.error == "workflow condition rejected"
        assert self.workflow.audit_log[-1]["reason"] == "condition_non_boolean"
