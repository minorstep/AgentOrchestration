"""Workflow Manager — Defines and executes multi-step agent workflows."""

from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from uuid import uuid4


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPENSATED = "compensated"
    COMPENSATION_FAILED = "compensation_failed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class WorkflowStep:
    def __init__(
        self,
        name: str,
        handler: Callable,
        retries: int = 0,
        timeout: int = 300,
        compensate: Optional[Callable] = None,
    ):
        self.id = str(uuid4())
        self.name = name
        self.handler = handler
        self.compensate = compensate
        self.retries = retries
        self.timeout = timeout
        self.status = StepStatus.PENDING
        self.result: Any = None
        self.error: Optional[str] = None
        self.compensation_error: Optional[str] = None
        self.blocked_reason: Optional[str] = None


class Workflow:
    def __init__(self, name: str, description: str = ""):
        self.id = str(uuid4())
        self.name = name
        self.description = description
        self.steps: List[WorkflowStep] = []
        self._step_map: Dict[str, WorkflowStep] = {}
        self._blocked_step_ids: Set[str] = set()
        self.audit_records: List[Dict[str, str]] = []
        self.blocked_reason: Optional[str] = None
        self.status = StepStatus.PENDING

    def add_step(self, step: WorkflowStep) -> "Workflow":
        if self.blocked_reason:
            raise ValueError(
                "cannot register new steps after workflow dispatch is blocked"
            )
        self.steps.append(step)
        self._step_map[step.id] = step
        return self

    def get_step(self, step_id: str) -> Optional[WorkflowStep]:
        return self._step_map.get(step_id)


class WorkflowManager:
    def __init__(self):
        self._workflows: Dict[str, Workflow] = {}

    def create_workflow(self, name: str, description: str = "") -> Workflow:
        workflow = Workflow(name, description)
        self._workflows[workflow.id] = workflow
        return workflow

    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> List[Workflow]:
        return list(self._workflows.values())

    def delete_workflow(self, workflow_id: str) -> bool:
        return self._workflows.pop(workflow_id, None) is not None

    def dispatch_step(self, workflow_id: str, step_id: str) -> bool:
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return False

        step = workflow.get_step(step_id)
        if not step or not self._can_dispatch(workflow, step):
            return False

        return self._execute_step(workflow, step)

    def execute_workflow(self, workflow_id: str) -> bool:
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return False

        workflow.status = StepStatus.RUNNING
        completed_steps: List[WorkflowStep] = []

        for step in workflow.steps:
            if not self._can_dispatch(workflow, step):
                workflow.status = StepStatus.FAILED
                return False

            if self._execute_step(workflow, step):
                completed_steps.append(step)
                continue

            partial_rollback = self._run_compensation(
                workflow,
                completed_steps,
            )
            reason = "partial_rollback" if partial_rollback else "failed_step"
            self._block_downstream(workflow, step, reason)
            workflow.status = StepStatus.FAILED
            return False

        workflow.status = StepStatus.COMPLETED
        return True

    def _can_dispatch(self, workflow: Workflow, step: WorkflowStep) -> bool:
        if workflow.blocked_reason or step.id in workflow._blocked_step_ids:
            step.status = StepStatus.BLOCKED
            step.blocked_reason = workflow.blocked_reason or "dispatch_blocked"
            self._record_audit(
                workflow,
                step,
                "dispatch_blocked",
                step.blocked_reason,
            )
            return False

        if step.status is not StepStatus.PENDING:
            self._record_audit(
                workflow,
                step,
                "dispatch_blocked",
                "non_pending_state",
            )
            return False

        return True

    def _execute_step(self, workflow: Workflow, step: WorkflowStep) -> bool:
        step.status = StepStatus.RUNNING
        self._record_audit(workflow, step, "dispatch_started", "accepted")

        try:
            step.result = step.handler()
            step.status = StepStatus.COMPLETED
            self._record_audit(
                workflow,
                step,
                "dispatch_completed",
                "completed",
            )
            return True
        except Exception as e:
            step.error = str(e)
            step.status = StepStatus.FAILED
            self._record_audit(
                workflow,
                step,
                "dispatch_failed",
                "handler_error",
            )
            return False

    def _run_compensation(
        self,
        workflow: Workflow,
        completed_steps: List[WorkflowStep],
    ) -> bool:
        partial_rollback = False

        for step in reversed(completed_steps):
            if not step.compensate:
                self._record_audit(
                    workflow,
                    step,
                    "compensation_skipped",
                    "no_action",
                )
                continue

            try:
                step.compensate()
                step.status = StepStatus.COMPENSATED
                self._record_audit(
                    workflow,
                    step,
                    "compensation_completed",
                    "completed",
                )
            except Exception as e:
                partial_rollback = True
                step.status = StepStatus.COMPENSATION_FAILED
                step.compensation_error = str(e)
                self._record_audit(
                    workflow,
                    step,
                    "compensation_failed",
                    "handler_error",
                )

        return partial_rollback

    def _block_downstream(
        self,
        workflow: Workflow,
        failed_step: WorkflowStep,
        reason: str,
    ) -> None:
        workflow.blocked_reason = reason
        try:
            start = workflow.steps.index(failed_step) + 1
        except ValueError:
            start = len(workflow.steps)

        for step in workflow.steps[start:]:
            if step.status is StepStatus.PENDING:
                step.status = StepStatus.BLOCKED
                step.blocked_reason = reason
            workflow._blocked_step_ids.add(step.id)
            self._record_audit(workflow, step, "downstream_blocked", reason)

    def _record_audit(
        self,
        workflow: Workflow,
        step: WorkflowStep,
        event: str,
        reason: str,
    ) -> None:
        workflow.audit_records.append(
            {
                "workflow_id": workflow.id,
                "step_id": step.id,
                "step_name": step.name,
                "event": event,
                "reason": reason,
            }
        )

# 2019-03-27T19:58:07 update

# 2019-05-09T09:42:56 update

# 2019-12-03T10:07:42 update

# 2020-01-16T18:43:28 update

# 2020-03-20T10:40:15 update

# 2020-04-17T15:36:50 update

# 2020-05-04T14:44:01 update

# 2020-06-16T13:17:31 update

# 2020-08-05T17:00:24 update

# 2020-09-04T08:29:23 update

# 2020-09-09T17:52:02 update

# 2020-10-23T10:57:44 update

# 2020-12-05T20:55:47 update

# 2021-01-15T19:23:40 update

# 2021-02-03T20:43:12 update

# 2021-03-16T12:26:47 update

# 2021-04-20T14:33:28 update

# 2021-10-14T15:03:32 update

# 2021-10-21T17:24:55 update

# 2021-11-16T17:01:08 update

# 2021-11-22T09:51:21 update

# 2021-12-21T16:15:47 update

# 2022-03-23T16:52:27 update

# 2022-12-21T09:25:50 update

# 2023-01-09T09:55:25 update

# 2023-01-13T11:06:15 update

# 2023-01-26T11:00:59 update

# 2023-02-23T08:56:54 update

# 2023-05-17T08:07:16 update

# 2023-06-06T17:09:34 update

# 2023-06-13T10:35:28 update

# 2023-08-24T20:36:06 update

# 2023-10-30T19:10:13 update

# 2024-01-02T08:27:25 update

# 2024-01-24T12:13:15 update

# 2024-02-08T13:35:49 update

# 2024-05-07T16:09:24 update

# 2024-05-11T09:48:46 update

# 2024-05-21T19:25:41 update

# 2024-06-05T12:00:30 update

# 2024-06-25T09:40:26 update

# 2024-09-17T13:49:39 update

# 2024-10-14T17:39:35 update

# 2024-11-27T20:14:35 update

# 2024-12-25T19:31:41 update

# 2025-01-16T13:15:09 update

# 2025-02-05T14:06:59 update

# 2025-02-17T20:55:11 update

# 2025-04-30T19:36:53 update

# 2025-07-17T10:14:40 update

# 2025-08-29T12:13:15 update

# 2025-09-03T13:51:11 update

# 2025-09-19T16:08:24 update

# 2025-11-27T08:38:12 update

# 2026-01-27T13:23:38 update

# 2026-01-28T11:22:50 update
