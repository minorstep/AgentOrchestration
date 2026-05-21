"""Task Scheduler — Priority-based task queuing and dispatch."""

import heapq
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4


CANCELLED_STATES = {"cancelled", "cancelling"}


class PriorityQueue:
    def __init__(self):
        self._queue = []
        self._counter = 0

    def push(self, item: Any, priority: int = 0) -> None:
        heapq.heappush(self._queue, (-priority, self._counter, item))
        self._counter += 1

    def pop(self) -> Optional[Any]:
        if self._queue:
            return heapq.heappop(self._queue)[2]
        return None

    def peek(self) -> Optional[Any]:
        if self._queue:
            return self._queue[0][2]
        return None

    def __len__(self) -> int:
        return len(self._queue)


class TaskScheduler:
    def __init__(self):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, Dict[str, Any]] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._cancelled: Dict[str, Dict] = {}
        self._retry_audit: List[Dict[str, Any]] = []
        self._max_retries = 3

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        self._prepare_task(task, task_id)
        self._push_task(task, queue, priority)
        return task_id

    def schedule(
        self,
        task: Dict,
        delay: float,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        run_at = time.time() + delay
        self._prepare_task(task, task_id)
        task["lifecycle_state"] = "scheduled"
        task["scheduled_at"] = time.time()
        task["scheduled_for"] = run_at
        self._scheduled[task_id] = {
            "run_at": run_at,
            "task": task,
            "queue": queue,
            "priority": priority,
        }
        return task_id

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
    ) -> Optional[Dict]:
        now = time.time()
        expired = [
            tid
            for tid, scheduled in self._scheduled.items()
            if scheduled["run_at"] <= now and scheduled["queue"] == queue
        ]
        for tid in expired:
            scheduled = self._scheduled.pop(tid)
            task = scheduled["task"]
            task["lifecycle_state"] = "queued"
            task["enqueued_at"] = now
            self._push_task(
                task,
                scheduled["queue"],
                scheduled["priority"],
            )

        if queue in self._queues and len(self._queues[queue]) > 0:
            task = self._queues[queue].pop()
            if task:
                task["lifecycle_state"] = "running"
                self._in_flight[task["id"]] = task
                return task
        return None

    def complete(self, task_id: str) -> bool:
        return self._in_flight.pop(task_id, None) is not None

    def cancel(self, task_id: str, reason: str = "cancelled") -> bool:
        task = self._in_flight.pop(task_id, None)
        if not task:
            return False
        task["lifecycle_state"] = "cancelled"
        task["cancel_reason"] = reason
        self._cancelled[task_id] = task
        self._audit_retry_decision(task, "cancelled", reason)
        return True

    def fail(
        self,
        task_id: str,
        queue: str = "default",
        *,
        expected_attempt: Optional[int] = None,
        expected_revision: Optional[int] = None,
        expected_parent_attempt: Optional[int] = None,
        expected_parent_revision: Optional[int] = None,
    ) -> bool:
        task = self._in_flight.get(task_id)
        if task:
            retry_block = self._retry_block_reason(
                task,
                expected_attempt,
                expected_revision,
                expected_parent_attempt,
                expected_parent_revision,
            )
            if retry_block:
                self._in_flight.pop(task_id, None)
                task["lifecycle_state"] = "cancelled"
                task["retry_state"] = "rejected"
                task["retry_rejected_reason"] = retry_block
                self._cancelled[task_id] = task
                self._audit_retry_decision(task, "retry_rejected", retry_block)
                return False

            self._in_flight.pop(task_id, None)
            task["retries"] += 1
            task["attempt"] = task["retries"]
            task["revision"] = task.get("revision", 0) + 1
            if task["retries"] < self._max_retries:
                task["lifecycle_state"] = "queued"
                self._audit_retry_decision(task, "retry_queued", "task_failed")
                self.enqueue(task, queue, priority=task.get("priority", 0))
                return True
        return False

    def retry_audit(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._retry_audit]

    def _retry_block_reason(
        self,
        task: Dict,
        expected_attempt: Optional[int],
        expected_revision: Optional[int],
        expected_parent_attempt: Optional[int],
        expected_parent_revision: Optional[int],
    ) -> Optional[str]:
        if (
            expected_attempt is not None
            and task.get("attempt") != expected_attempt
        ):
            return "stale_attempt"
        if (
            expected_revision is not None
            and task.get("revision") != expected_revision
        ):
            return "stale_revision"
        if task.get("lifecycle_state") in CANCELLED_STATES:
            return "task_cancelled"

        parent_id = task.get("parent_task_id")
        parent_state = task.get("parent_lifecycle_state")
        parent_cancelled = parent_id in self._cancelled
        if parent_cancelled or parent_state in CANCELLED_STATES:
            return "parent_cancelled"
        if expected_parent_attempt is not None:
            if task.get("parent_attempt") != expected_parent_attempt:
                return "stale_parent_attempt"
        if expected_parent_revision is not None:
            if task.get("parent_revision") != expected_parent_revision:
                return "stale_parent_revision"
        if parent_id and parent_state is None and not parent_cancelled:
            return "missing_parent_lifecycle"
        return None

    def _audit_retry_decision(
        self,
        task: Dict,
        decision: str,
        reason: str,
    ) -> None:
        self._retry_audit.append({
            "decision": decision,
            "reason": reason,
            "task_id": task.get("id"),
            "parent_task_id": task.get("parent_task_id"),
            "attempt": task.get("attempt"),
            "revision": task.get("revision"),
            "parent_attempt": task.get("parent_attempt"),
            "parent_revision": task.get("parent_revision"),
            "parent_lifecycle_state": task.get("parent_lifecycle_state"),
            "lifecycle_state": task.get("lifecycle_state"),
        })

    def _prepare_task(self, task: Dict, task_id: str) -> None:
        task["id"] = task_id
        task["enqueued_at"] = time.time()
        task.setdefault("retries", 0)
        task.setdefault("attempt", task["retries"])
        task.setdefault("revision", 0)
        task.setdefault("lifecycle_state", "queued")

    def _push_task(self, task: Dict, queue: str, priority: int = 0) -> None:
        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)

# 2019-04-25T08:37:12 update

# 2019-06-04T16:40:00 update

# 2019-07-11T12:01:28 update

# 2019-08-02T12:20:21 update

# 2019-08-23T10:38:50 update

# 2019-10-31T13:55:52 update

# 2019-11-04T20:12:32 update

# 2019-12-13T12:22:36 update

# 2020-02-01T10:32:37 update

# 2020-02-26T09:44:38 update

# 2020-03-09T19:00:55 update

# 2020-05-01T18:40:34 update

# 2020-05-12T15:10:31 update

# 2020-06-30T13:24:19 update

# 2020-09-22T16:00:45 update

# 2020-10-20T10:52:48 update

# 2020-10-21T12:18:08 update

# 2020-11-06T12:35:01 update

# 2020-12-09T08:09:33 update

# 2021-01-07T08:20:36 update

# 2021-10-02T15:23:16 update

# 2021-10-06T16:14:57 update

# 2021-10-06T09:27:41 update

# 2021-11-19T08:37:40 update

# 2022-03-01T16:39:54 update

# 2022-05-26T13:43:07 update

# 2022-06-02T10:50:58 update

# 2022-06-14T10:46:48 update

# 2022-07-31T16:44:34 update

# 2022-08-30T18:20:12 update

# 2022-11-04T14:47:03 update

# 2022-12-06T10:36:49 update

# 2022-12-22T13:21:12 update

# 2022-12-26T12:24:50 update

# 2023-03-09T08:09:55 update

# 2023-05-01T10:07:37 update

# 2023-06-08T14:32:15 update

# 2023-07-14T17:24:18 update

# 2023-12-14T08:38:31 update

# 2024-02-20T13:43:58 update

# 2024-03-24T08:52:42 update

# 2024-03-28T15:27:17 update

# 2024-03-29T18:10:33 update

# 2024-04-15T20:18:31 update

# 2024-05-27T13:11:52 update

# 2024-05-27T16:42:56 update

# 2024-06-20T13:03:45 update

# 2024-06-28T12:32:58 update

# 2024-07-10T14:10:16 update

# 2024-07-26T14:18:59 update

# 2024-08-12T08:21:05 update

# 2024-08-21T16:58:40 update

# 2024-09-27T19:54:30 update

# 2024-10-21T13:47:42 update

# 2024-11-11T09:19:27 update

# 2024-12-24T08:23:41 update

# 2025-02-14T10:35:15 update

# 2025-03-31T18:09:40 update

# 2025-06-21T17:32:49 update

# 2025-07-21T16:52:28 update

# 2025-08-20T19:45:16 update

# 2025-11-04T18:54:24 update

# 2025-12-09T20:17:36 update

# 2026-01-12T15:42:32 update

# 2026-01-23T14:41:20 update

# 2026-03-18T14:43:07 update

# 2026-04-13T11:43:19 update
