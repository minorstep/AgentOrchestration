"""Workflow Manager — Defines and executes multi-step agent workflows."""

from copy import deepcopy
from enum import Enum
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4


logger = logging.getLogger(__name__)


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReservedMetadataKeyError(ValueError):
    def __init__(self, key_path: str):
        self.key_path = key_path
        super().__init__(f"Reserved metadata key is not allowed: {key_path}")


RESERVED_METADATA_KEYS = frozenset(
    {
        "agent_id",
        "agentid",
        "agent_type",
        "agenttype",
        "attempt",
        "children",
        "completed_at",
        "completedat",
        "created_at",
        "createdat",
        "error",
        "handler",
        "id",
        "lifecycle",
        "lifecycle_state",
        "lifecyclestate",
        "parent_id",
        "parentid",
        "parent_workflow_id",
        "parentworkflowid",
        "priority",
        "queue",
        "result",
        "retries",
        "revision",
        "route",
        "routing",
        "scheduled_at",
        "scheduledat",
        "started_at",
        "startedat",
        "state",
        "status",
        "step_id",
        "stepid",
        "step_map",
        "stepmap",
        "steps",
        "task_id",
        "taskid",
        "timeout",
        "updated_at",
        "updatedat",
        "workflow_id",
        "workflowid",
    }
)


def _normalise_metadata_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")


def _reserved_metadata_violation(
    metadata: Any,
    path: str = "",
) -> Optional[str]:
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            key_path = f"{path}.{key}" if path else str(key)
            if _normalise_metadata_key(key) in RESERVED_METADATA_KEYS:
                return key_path

            nested_violation = _reserved_metadata_violation(value, key_path)
            if nested_violation:
                return nested_violation
    elif isinstance(metadata, list):
        for index, value in enumerate(metadata):
            nested_violation = _reserved_metadata_violation(
                value,
                f"{path}[{index}]",
            )
            if nested_violation:
                return nested_violation

    return None


def _validate_user_metadata(
    metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise TypeError("workflow metadata must be a dictionary")

    violation = _reserved_metadata_violation(metadata)
    if violation:
        raise ReservedMetadataKeyError(violation)

    return deepcopy(metadata)


class WorkflowStep:
    def __init__(
        self,
        name: str,
        handler: Callable,
        retries: int = 0,
        timeout: int = 300,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.id = str(uuid4())
        self.name = name
        self.handler = handler
        self.retries = retries
        self.timeout = timeout
        self._metadata = _validate_user_metadata(metadata)
        self.status = StepStatus.PENDING
        self.result: Any = None
        self.error: Optional[str] = None

    @property
    def metadata(self) -> Dict[str, Any]:
        return deepcopy(self._metadata)

    @metadata.setter
    def metadata(self, value: Optional[Dict[str, Any]]) -> None:
        self._metadata = _validate_user_metadata(value)


class Workflow:
    def __init__(
        self,
        name: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        audit_callback: Optional[Callable[[str, Dict[str, str]], None]] = None,
    ):
        self.id = str(uuid4())
        self.name = name
        self.description = description
        self._metadata = _validate_user_metadata(metadata)
        self._audit_callback = audit_callback
        self.steps: List[WorkflowStep] = []
        self._step_map: Dict[str, WorkflowStep] = {}
        self.status = StepStatus.PENDING

    @property
    def metadata(self) -> Dict[str, Any]:
        return deepcopy(self._metadata)

    @metadata.setter
    def metadata(self, value: Optional[Dict[str, Any]]) -> None:
        self._metadata = _validate_user_metadata(value)

    def add_step(self, step: WorkflowStep) -> "Workflow":
        violation = _reserved_metadata_violation(step._metadata)
        if violation:
            self._audit(
                "workflow_step_rejected",
                {
                    "reason": "reserved_metadata_key",
                    "key_path": violation,
                    "workflow_status": self.status.value,
                    "step_status": step.status.value,
                },
            )
            raise ReservedMetadataKeyError(violation)

        self.steps.append(step)
        self._step_map[step.id] = step
        return self

    def get_step(self, step_id: str) -> Optional[WorkflowStep]:
        return self._step_map.get(step_id)

    def _audit(self, event: str, details: Dict[str, str]) -> None:
        if self._audit_callback:
            details["workflow_id"] = self.id
            self._audit_callback(event, details)


class WorkflowManager:
    def __init__(self):
        self._workflows: Dict[str, Workflow] = {}
        self._audit_log: List[Dict[str, str]] = []

    @property
    def audit_log(self) -> List[Dict[str, str]]:
        return deepcopy(self._audit_log)

    def create_workflow(
        self,
        name: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Workflow:
        try:
            workflow = Workflow(
                name,
                description,
                metadata,
                self._record_audit,
            )
        except ReservedMetadataKeyError as exc:
            self._record_audit(
                "workflow_registration_rejected",
                {"reason": "reserved_metadata_key", "key_path": exc.key_path},
            )
            raise

        self._workflows[workflow.id] = workflow
        return workflow

    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> List[Workflow]:
        return list(self._workflows.values())

    def delete_workflow(self, workflow_id: str) -> bool:
        return self._workflows.pop(workflow_id, None) is not None

    def execute_workflow(self, workflow_id: str) -> bool:
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return False

        violation = self._definition_metadata_violation(workflow)
        if violation:
            key_path, status_before = violation
            self._record_audit(
                "workflow_execution_deferred",
                {
                    "reason": "reserved_metadata_key",
                    "key_path": key_path,
                    "workflow_id": workflow.id,
                    "workflow_status": status_before,
                },
            )
            return False

        workflow.status = StepStatus.RUNNING
        for step in workflow.steps:
            step.status = StepStatus.RUNNING
            try:
                result = step.handler()
                step.result = result
                step.status = StepStatus.COMPLETED
            except Exception as e:
                step.error = str(e)
                step.status = StepStatus.FAILED
                workflow.status = StepStatus.FAILED
                return False

        workflow.status = StepStatus.COMPLETED
        return True

    def _definition_metadata_violation(
        self, workflow: Workflow
    ) -> Optional[Tuple[str, str]]:
        violation = _reserved_metadata_violation(workflow._metadata)
        if violation:
            return violation, workflow.status.value

        for step in workflow.steps:
            violation = _reserved_metadata_violation(step._metadata)
            if violation:
                return f"{step.name}.{violation}", workflow.status.value

        return None

    def _record_audit(self, event: str, details: Dict[str, str]) -> None:
        record = {"event": event, **details}
        self._audit_log.append(record)
        logger.warning(
            "Workflow metadata decision: %s",
            event,
            extra={
                key: value
                for key, value in record.items()
                if key != "event"
            },
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
