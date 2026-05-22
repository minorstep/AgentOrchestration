"""Task Scheduler — Priority-based task queuing and dispatch."""

import heapq
import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional
from uuid import uuid4

from src.common.metrics import metrics

logger = logging.getLogger(__name__)


class QueueCapacityError(RuntimeError):
    """Raised when a queue cannot accept another task."""


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
    def __init__(
        self,
        queue_capacity: Optional[Dict[str, int]] = None,
        metrics_collector=None,
    ):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, float] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._enqueue_transactions: Dict[str, str] = {}
        self._queue_capacity = dict(queue_capacity or {})
        self._queue_usage: Dict[str, int] = defaultdict(int)
        self._capacity_audit: List[Dict[str, Any]] = []
        self.metrics = metrics_collector or metrics
        self._max_retries = 3

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
        transaction_id: Optional[str] = None,
    ) -> str:
        if transaction_id and transaction_id in self._enqueue_transactions:
            return self._enqueue_transactions[transaction_id]

        reserved = self._reserve_capacity(queue)
        original = dict(task)
        task_id = str(uuid4())
        task["id"] = task_id
        task["enqueued_at"] = time.time()
        task["retries"] = task.get("retries", 0)

        try:
            if queue not in self._queues:
                self._queues[queue] = PriorityQueue()
            self._queues[queue].push(task, priority)
        except Exception:
            if reserved:
                self._release_capacity(
                    queue,
                    "enqueue_rollback",
                    task_id,
                )
            else:
                self._record_capacity_decision(
                    queue,
                    "enqueue_rollback",
                    task_id,
                )
            task.clear()
            task.update(original)
            raise

        self._record_capacity_decision(queue, "enqueued", task_id)
        if transaction_id:
            self._enqueue_transactions[transaction_id] = task_id
        return task_id

    def schedule(
        self,
        task: Dict,
        delay: float,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        self._scheduled[task_id] = time.time() + delay
        return task_id

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
    ) -> Optional[Dict]:
        now = time.time()
        expired = [tid for tid, t in self._scheduled.items() if t <= now]
        for tid in expired:
            task = self._scheduled.pop(tid)
            if task:
                self.enqueue(task, queue)

        if queue in self._queues and len(self._queues[queue]) > 0:
            task = self._queues[queue].pop()
            if task:
                self._release_capacity(queue, "dequeued", task["id"])
                self._in_flight[task["id"]] = task
                return task
        return None

    def complete(self, task_id: str) -> bool:
        return self._in_flight.pop(task_id, None) is not None

    def fail(self, task_id: str, queue: str = "default") -> bool:
        task = self._in_flight.pop(task_id, None)
        if task:
            task["retries"] += 1
            if task["retries"] < self._max_retries:
                try:
                    self.enqueue(task, queue, priority=task.get("priority", 0))
                except Exception:
                    self._in_flight[task_id] = task
                    self._record_capacity_decision(
                        queue,
                        "retry_enqueue_rollback",
                        task_id,
                    )
                    raise
                return True
        return False

    def queue_capacity_state(
        self,
        queue: str = "default",
    ) -> Dict[str, Optional[int]]:
        capacity = self._queue_capacity.get(queue)
        used = self._queue_usage.get(queue, 0)
        return {
            "queue": queue,
            "capacity": capacity,
            "used": used,
            "available": None if capacity is None else capacity - used,
        }

    def capacity_audit_records(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._capacity_audit]

    def _reserve_capacity(self, queue: str) -> bool:
        capacity = self._queue_capacity.get(queue)
        if capacity is None:
            return False
        if self._queue_usage[queue] >= capacity:
            self._record_capacity_decision(queue, "capacity_exhausted")
            raise QueueCapacityError(f"queue {queue} is at capacity")
        self._queue_usage[queue] += 1
        return True

    def _release_capacity(
        self,
        queue: str,
        decision: str,
        task_id: Optional[str] = None,
    ) -> None:
        if queue not in self._queue_capacity:
            return
        if self._queue_usage[queue] > 0:
            self._queue_usage[queue] -= 1
        self._record_capacity_decision(queue, decision, task_id)

    def _record_capacity_decision(
        self,
        queue: str,
        decision: str,
        task_id: Optional[str] = None,
    ) -> None:
        if queue not in self._queue_capacity:
            return
        record = {
            "queue": queue,
            "decision": decision,
            "used": self._queue_usage.get(queue, 0),
            "capacity": self._queue_capacity.get(queue),
        }
        if task_id:
            record["task_ref"] = task_id[:12]
        self._capacity_audit.append(record)
        self.metrics.increment(f"scheduler.queue_capacity.{decision}")
        logger.info("queue capacity decision", extra={"decision": record})

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
