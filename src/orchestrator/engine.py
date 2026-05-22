"""Orchestration Engine — Core execution and coordination logic."""

import asyncio
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from src.agent import AgentRegistry, AgentStatus
from src.common.metrics import metrics
from src.orchestrator.scheduler import TaskScheduler

logger = logging.getLogger(__name__)


_ALLOWED_EVENT_TRANSITIONS = {
    "run.started": "running",
    "run.completed": "completed",
    "run.failed": "failed",
    "task.started": "running",
    "task.completed": "completed",
    "task.failed": "failed",
    "handler.started": "running",
    "handler.completed": "completed",
    "handler.failed": "failed",
}

_TERMINAL_LIFECYCLES = {"completed", "failed", "cancelled", "terminated"}
_LIFECYCLE_ORDER = {
    "pending": 0,
    "running": 1,
    "completed": 2,
    "failed": 2,
    "cancelled": 2,
    "terminated": 2,
}


class OrchestrationEngine:
    def __init__(
        self,
        max_workers: int = 10,
        agent_timeout: int = 300,
        metrics_collector=None,
    ):
        self.registry = AgentRegistry()
        self.scheduler = TaskScheduler()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.agent_timeout = agent_timeout
        self.metrics = metrics_collector or metrics
        self._running = False
        self._event_state: Dict[str, Dict[str, Any]] = {}
        self._event_audit: List[Dict[str, Any]] = []
        self._quarantined_events: List[Dict[str, Any]] = []
        self._hooks: Dict[str, List[Callable]] = {
            "pre_execute": [],
            "post_execute": [],
            "on_error": [],
            "on_complete": [],
        }

    def register_hook(self, event: str, callback: Callable) -> None:
        if event in self._hooks:
            self._hooks[event].append(callback)

    def dispatch_event(self, event: Dict[str, Any]) -> bool:
        """Apply an orchestrator lifecycle event after version checks."""
        decision = self._validate_event(event)
        if not decision["accepted"]:
            self._quarantine_event(event, decision["reason"])
            return False

        entity_id = str(event["entity_id"])
        state = {
            "lifecycle": str(event["lifecycle"]),
            "revision": int(event["revision"]),
            "attempt": int(event["attempt"]),
            "event_type": str(event["type"]),
            "updated_at": time.time(),
        }
        self._event_state[entity_id] = state
        self.metrics.increment("orchestrator.events.accepted")
        self._record_event_decision(event, "accepted")
        logger.info(
            "orchestrator event accepted",
            extra=self._safe_event_log_context(event, "accepted"),
        )
        return True

    def get_event_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        state = self._event_state.get(entity_id)
        return dict(state) if state else None

    def event_audit_records(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._event_audit]

    def quarantined_events(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._quarantined_events]

    def _validate_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        event_type = event.get("type")
        if event_type not in _ALLOWED_EVENT_TRANSITIONS:
            return {"accepted": False, "reason": "unknown_event_type"}

        entity_id = event.get("entity_id")
        if not entity_id:
            return {"accepted": False, "reason": "missing_entity_id"}

        try:
            revision = int(event["revision"])
            attempt = int(event["attempt"])
        except (KeyError, TypeError, ValueError):
            return {"accepted": False, "reason": "invalid_version_marker"}

        if revision < 0 or attempt < 0:
            return {"accepted": False, "reason": "invalid_version_marker"}

        lifecycle = str(event.get("lifecycle", ""))
        if lifecycle != _ALLOWED_EVENT_TRANSITIONS[event_type]:
            return {"accepted": False, "reason": "policy_lifecycle_mismatch"}

        current = self._event_state.get(str(entity_id))
        if not current:
            return {"accepted": True, "reason": "accepted"}

        if revision < current["revision"]:
            return {"accepted": False, "reason": "stale_revision"}
        if revision == current["revision"] and attempt < current["attempt"]:
            return {"accepted": False, "reason": "stale_attempt"}
        if (
            revision == current["revision"]
            and attempt == current["attempt"]
            and lifecycle == current["lifecycle"]
        ):
            return {"accepted": False, "reason": "duplicate_transition"}
        if (
            current["lifecycle"] in _TERMINAL_LIFECYCLES
            and lifecycle != current["lifecycle"]
        ):
            return {"accepted": False, "reason": "terminal_lifecycle"}
        current_order = _LIFECYCLE_ORDER[current["lifecycle"]]
        if _LIFECYCLE_ORDER[lifecycle] < current_order:
            return {"accepted": False, "reason": "lifecycle_regression"}

        return {"accepted": True, "reason": "accepted"}

    def _quarantine_event(self, event: Dict[str, Any], reason: str) -> None:
        record = self._record_event_decision(event, reason)
        self._quarantined_events.append(record)
        self.metrics.increment("orchestrator.events.quarantined")
        self.metrics.increment(f"orchestrator.events.quarantined.{reason}")
        logger.warning(
            "orchestrator event quarantined",
            extra=self._safe_event_log_context(event, reason),
        )

    def _record_event_decision(
        self,
        event: Dict[str, Any],
        decision: str,
    ) -> Dict[str, Any]:
        entity_id = str(event.get("entity_id", ""))
        current = self._event_state.get(entity_id)
        record = {
            "decision": decision,
            "event_type": str(event.get("type", "unknown")),
            "entity_ref": self._event_ref(entity_id),
            "revision": self._safe_int(event.get("revision")),
            "attempt": self._safe_int(event.get("attempt")),
            "requested_lifecycle": str(event.get("lifecycle", "unknown")),
            "current_lifecycle": current["lifecycle"] if current else None,
        }
        self._event_audit.append(record)
        return record

    def _safe_event_log_context(
        self,
        event: Dict[str, Any],
        decision: str,
    ) -> Dict[str, Any]:
        entity_id = str(event.get("entity_id", ""))
        return {
            "event_decision": decision,
            "event_type": str(event.get("type", "unknown")),
            "entity_ref": self._event_ref(entity_id),
            "revision": self._safe_int(event.get("revision")),
            "attempt": self._safe_int(event.get("attempt")),
        }

    def _event_ref(self, entity_id: str) -> str:
        if not entity_id:
            return "missing"
        return hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:12]

    def _safe_int(self, value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def start(self) -> None:
        self._running = True
        logger.info("Orchestration engine started")
        while self._running:
            task = await self.scheduler.dequeue()
            if task:
                asyncio.create_task(self._execute_task(task))
            await asyncio.sleep(0.1)

    def stop(self) -> None:
        self._running = False
        logger.info("Orchestration engine stopped")

    async def _execute_task(self, task: Dict[str, Any]) -> None:
        task_id = task["id"]
        agent_id = task["target_agent"]
        logger.info(f"Executing task {task_id} on agent {agent_id}")

        for hook in self._hooks["pre_execute"]:
            await hook(task)

        try:
            agent = self.registry.get(agent_id)
            if not agent:
                raise ValueError(f"Agent {agent_id} not found")

            self.registry.update_status(agent_id, AgentStatus.RUNNING)
            result = await asyncio.wait_for(
                self._run_agent_task(agent, task),
                timeout=self.agent_timeout,
            )
            self.registry.update_status(agent_id, AgentStatus.PAUSED)

            for hook in self._hooks["post_execute"]:
                await hook(task, result)

            logger.info(f"Task {task_id} completed successfully")

        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")
            for hook in self._hooks["on_error"]:
                await hook(task, e)

    async def _run_agent_task(self, agent: Dict, task: Dict) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            self._execute_in_thread,
            agent,
            task,
        )

    def _execute_in_thread(self, agent: Dict, task: Dict) -> Any:
        return {
            "status": "completed",
            "output": f"Task {task['id']} processed by {agent['name']}",
        }

# 2019-04-24T14:55:39 update

# 2019-05-01T16:01:52 update

# 2019-05-27T19:55:55 update

# 2019-06-02T09:38:08 update

# 2019-07-10T15:36:32 update

# 2019-07-22T11:36:40 update

# 2019-08-28T10:50:39 update

# 2019-08-30T14:21:57 update

# 2019-09-12T18:46:28 update

# 2019-10-02T09:55:59 update

# 2019-10-03T16:01:13 update

# 2019-12-03T13:07:37 update

# 2020-01-10T13:47:02 update

# 2020-01-31T13:14:49 update

# 2020-03-11T08:03:44 update

# 2020-03-31T15:51:14 update

# 2020-04-10T11:21:15 update

# 2020-06-08T09:31:33 update

# 2020-06-16T20:32:00 update

# 2020-07-21T18:48:01 update

# 2020-09-29T15:16:08 update

# 2020-11-18T14:09:09 update

# 2020-11-26T18:02:40 update

# 2021-01-07T11:18:24 update

# 2021-04-05T15:49:29 update

# 2021-04-27T11:58:27 update

# 2021-05-17T14:54:17 update

# 2021-06-07T11:46:07 update

# 2021-08-31T14:55:54 update

# 2021-09-10T17:29:34 update

# 2021-09-14T10:27:30 update

# 2021-10-06T14:04:05 update

# 2022-03-15T18:11:19 update

# 2022-09-15T18:32:09 update

# 2022-11-17T08:15:16 update

# 2023-02-17T12:24:53 update

# 2023-04-25T14:26:37 update

# 2023-05-22T09:03:39 update

# 2023-09-06T20:26:58 update

# 2023-11-28T17:54:23 update

# 2023-12-27T15:38:11 update

# 2024-03-12T20:10:32 update

# 2024-04-04T20:43:06 update

# 2024-05-27T12:23:51 update

# 2024-05-27T16:42:42 update

# 2024-07-23T13:27:05 update

# 2024-07-24T19:24:13 update

# 2024-11-03T18:25:58 update

# 2025-04-23T20:03:19 update

# 2026-02-16T17:12:09 update

# 2026-03-12T11:33:28 update
