"""Task Scheduler — Priority-based task queuing and dispatch."""

import heapq
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4


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

    def pop_ordered(self) -> List[Any]:
        items = []
        while self._queue:
            items.append(self.pop())
        return items

    def peek(self) -> Optional[Any]:
        if self._queue:
            return self._queue[0][2]
        return None

    def __len__(self) -> int:
        return len(self._queue)


class TaskScheduler:
    def __init__(
        self,
        fairness_budgets: Optional[Dict[str, int]] = None,
        audit_limit: int = 100,
    ):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, Dict] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._in_flight_by_class: Dict[str, Set[str]] = defaultdict(set)
        self._terminal_tasks: Set[str] = set()
        self._audit_log: List[Dict[str, Any]] = []
        self._audit_limit = audit_limit
        self._metrics: Dict[str, int] = defaultdict(int)
        self._fairness_budgets = {
            "urgent": 1,
            "standard": 5,
            "background": 2,
        }
        if fairness_budgets:
            for priority_class, budget in fairness_budgets.items():
                self._fairness_budgets[priority_class] = self._validate_budget(
                    priority_class,
                    budget,
                )
        self._max_retries = 3

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
        priority_class: Optional[str] = None,
    ) -> str:
        task_id = task.get("id") or str(uuid4())
        task["id"] = task_id
        task["queue"] = queue
        task["priority"] = priority
        task["priority_class"] = priority_class or self._priority_class(priority)
        task["lifecycle_state"] = task.get("lifecycle_state", "queued")
        task["state_revision"] = int(task.get("state_revision", 0))
        task["enqueued_at"] = time.time()
        task["retries"] = int(task.get("retries", 0))

        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)
        self._audit("queued", task, queue=queue)
        return task_id

    def schedule(self, task: Dict, delay: float, queue: str = "default", priority: int = 0) -> str:
        task_id = task.get("id") or str(uuid4())
        task["id"] = task_id
        task["queue"] = queue
        task["priority"] = priority
        task["priority_class"] = task.get("priority_class") or self._priority_class(priority)
        task["lifecycle_state"] = task.get("lifecycle_state", "scheduled")
        task["state_revision"] = int(task.get("state_revision", 0))
        self._scheduled[task_id] = {
            "run_at": time.time() + delay,
            "task": task,
            "queue": queue,
            "priority": priority,
        }
        self._audit("scheduled", task, queue=queue)
        return task_id

    async def dequeue(self, queue: str = "default", timeout: float = 1.0) -> Optional[Dict]:
        now = time.time()
        expired = [
            tid for tid, entry in self._scheduled.items()
            if entry["run_at"] <= now
        ]
        for tid in expired:
            entry = self._scheduled.pop(tid)
            task = entry["task"]
            task["lifecycle_state"] = "queued"
            self._bump_revision(task)
            self._queue_existing(task, entry["queue"], entry["priority"])

        if queue in self._queues and len(self._queues[queue]) > 0:
            return self._dequeue_with_preconditions(queue)
        return None

    def complete(self, task_id: str) -> bool:
        task = self._in_flight.pop(task_id, None)
        if not task:
            self._audit_rejection(
                "complete_rejected",
                task_id,
                "not_in_flight",
            )
            return False
        self._clear_in_flight_class(task)
        task["lifecycle_state"] = "completed"
        self._bump_revision(task)
        self._terminal_tasks.add(task_id)
        self._metrics["scheduler.completed"] += 1
        self._audit("completed", task, queue=task.get("queue", "default"))
        return True

    def fail(self, task_id: str, queue: str = "default") -> bool:
        task = self._in_flight.pop(task_id, None)
        if task:
            self._clear_in_flight_class(task)
            task["retries"] += 1
            if task["retries"] < self._max_retries:
                task["lifecycle_state"] = "queued"
                self._bump_revision(task)
                self._queue_existing(task, queue, task.get("priority", 0))
                self._metrics["scheduler.retried"] += 1
                self._audit("retry_queued", task, queue=queue)
                return True
            task["lifecycle_state"] = "failed"
            self._bump_revision(task)
            self._terminal_tasks.add(task_id)
            self._metrics["scheduler.failed"] += 1
            self._audit("failed", task, queue=queue)
            return False
        self._audit_rejection("fail_rejected", task_id, "not_in_flight")
        return False

    def audit_log(self) -> List[Dict[str, Any]]:
        return list(self._audit_log)

    def metrics_snapshot(self) -> Dict[str, int]:
        return dict(self._metrics)

    def _dequeue_with_preconditions(self, queue: str) -> Optional[Dict]:
        deferred: List[Tuple[Dict, int]] = []
        selected = None

        for task in self._queues[queue].pop_ordered():
            if selected is not None:
                deferred.append((task, task.get("priority", 0)))
                continue
            decision, reason = self._dispatch_decision(task)
            if decision == "dispatch":
                selected = task
                continue
            if decision == "defer":
                deferred.append((task, task.get("priority", 0)))
                self._audit("deferred", task, queue=queue, reason=reason)
                self._metrics[f"scheduler.deferred.{reason}"] += 1
                continue
            self._audit("rejected", task, queue=queue, reason=reason)
            self._metrics[f"scheduler.rejected.{reason}"] += 1

        for task, priority in deferred:
            self._queue_existing(task, queue, priority, audit=False)

        if selected is None:
            return None

        self._commit_dispatch(selected, queue)
        return selected

    def _dispatch_decision(self, task: Dict) -> Tuple[str, str]:
        task_id = task["id"]
        if task.get("lifecycle_state") != "queued":
            return "reject", "invalid_lifecycle_state"
        if task_id in self._in_flight:
            return "reject", "duplicate_in_flight"
        if task_id in self._terminal_tasks:
            return "reject", "terminal_rewrite"

        priority_class = task["priority_class"]
        budget = self._fairness_budgets.get(priority_class, 1)
        if len(self._in_flight_by_class[priority_class]) >= budget:
            return "defer", "fairness_budget_exhausted"
        return "dispatch", "accepted"

    def _commit_dispatch(self, task: Dict, queue: str) -> None:
        task["lifecycle_state"] = "in_flight"
        task["dispatched_at"] = time.time()
        self._bump_revision(task)
        self._in_flight[task["id"]] = task
        self._in_flight_by_class[task["priority_class"]].add(task["id"])
        self._metrics[f"scheduler.dispatched.{task['priority_class']}"] += 1
        self._audit("dispatched", task, queue=queue)

    def _queue_existing(
        self,
        task: Dict,
        queue: str,
        priority: int,
        audit: bool = True,
    ) -> None:
        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        task["queue"] = queue
        task["priority"] = priority
        task["priority_class"] = task.get("priority_class") or self._priority_class(priority)
        self._queues[queue].push(task, priority)
        if audit:
            self._audit("queued", task, queue=queue)

    def _clear_in_flight_class(self, task: Dict) -> None:
        priority_class = task.get("priority_class", "standard")
        self._in_flight_by_class[priority_class].discard(task["id"])

    def _priority_class(self, priority: int) -> str:
        if priority >= 10:
            return "urgent"
        if priority < 0:
            return "background"
        return "standard"

    def _validate_budget(self, priority_class: str, budget: int) -> int:
        if not isinstance(budget, int) or budget < 1:
            raise ValueError(f"Invalid fairness budget for {priority_class}")
        return budget

    def _bump_revision(self, task: Dict) -> None:
        task["state_revision"] = int(task.get("state_revision", 0)) + 1

    def _audit(
        self,
        event: str,
        task: Dict,
        queue: str,
        reason: str = "",
    ) -> None:
        entry = {
            "event": event,
            "task_id": task.get("id"),
            "queue": queue,
            "priority_class": task.get("priority_class"),
            "lifecycle_state": task.get("lifecycle_state"),
            "state_revision": task.get("state_revision"),
            "reason": reason,
            "timestamp": time.time(),
        }
        self._append_audit(entry)

    def _audit_rejection(self, event: str, task_id: str, reason: str) -> None:
        self._append_audit({
            "event": event,
            "task_id": task_id,
            "queue": "",
            "priority_class": "",
            "lifecycle_state": "",
            "state_revision": "",
            "reason": reason,
            "timestamp": time.time(),
        })

    def _append_audit(self, entry: Dict[str, Any]) -> None:
        self._audit_log.append(entry)
        if len(self._audit_log) > self._audit_limit:
            self._audit_log = self._audit_log[-self._audit_limit:]

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
