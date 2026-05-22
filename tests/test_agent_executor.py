import asyncio

import pytest

from src.agent.executor import AgentExecutor


async def echo_handler(agent_id, task):
    return {"agent_id": agent_id, "task": task["id"]}


async def failing_handler(agent_id, task):
    raise RuntimeError(f"failed {task['id']}")


def test_executor_evicts_oldest_completed_results_by_count():
    executor = AgentExecutor(max_results=2)

    first = asyncio.run(
        executor.execute("agent-1", {"id": "task-1"}, echo_handler)
    )
    second = asyncio.run(
        executor.execute("agent-1", {"id": "task-2"}, echo_handler)
    )
    third = asyncio.run(
        executor.execute("agent-1", {"id": "task-3"}, echo_handler)
    )

    assert executor.get_result(first) is None
    assert executor.get_result(second)["task_id"] == "task-2"
    assert executor.get_result(third)["task_id"] == "task-3"
    assert len(executor._results) == 2


def test_executor_bounds_failed_results_too():
    executor = AgentExecutor(max_results=1)

    failed = asyncio.run(
        executor.execute("agent-1", {"id": "bad"}, failing_handler)
    )
    kept = asyncio.run(
        executor.execute("agent-1", {"id": "good"}, echo_handler)
    )

    assert executor.get_result(failed) is None
    assert executor.get_result(kept)["task_id"] == "good"
    assert len(executor._results) == 1


def test_executor_expires_completed_results_by_ttl():
    executor = AgentExecutor(max_results=10, result_ttl_seconds=0.01)

    execution_id = asyncio.run(
        executor.execute("agent-1", {"id": "task-1"}, echo_handler)
    )
    executor._result_completed_at[execution_id] -= 1.0

    assert executor.get_result(execution_id) is None
    assert execution_id not in executor._result_completed_at


def test_executor_rejects_invalid_result_bounds():
    with pytest.raises(ValueError):
        AgentExecutor(max_results=0)
    with pytest.raises(ValueError):
        AgentExecutor(result_ttl_seconds=0)
