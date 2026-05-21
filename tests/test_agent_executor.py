import asyncio

from src.agent.executor import AgentExecutor


class TestAgentExecutorCancellation:
    def test_cancel_stores_terminal_result_for_active_execution(self):
        asyncio.run(self._cancel_stores_terminal_result_for_active_execution())

    async def _cancel_stores_terminal_result_for_active_execution(self):
        executor = AgentExecutor()
        started = asyncio.Event()

        async def handler(agent_id, task):
            started.set()
            await asyncio.Event().wait()

        execution = asyncio.create_task(
            executor.execute("agent-1", {"id": "task-1"}, handler)
        )
        await started.wait()
        execution_id = next(iter(executor._active_tasks))

        assert executor.cancel(execution_id)

        immediate_result = executor.get_result(execution_id)
        assert immediate_result["status"] == "cancelled"
        assert immediate_result["cancelled"] is True
        assert immediate_result["agent_id"] == "agent-1"
        assert immediate_result["task_id"] == "task-1"

        assert await asyncio.wait_for(execution, timeout=1) == execution_id
        final_result = executor.get_result(execution_id)
        assert final_result["status"] == "cancelled"
        assert execution_id not in executor._active_tasks

    def test_outer_task_cancellation_stores_terminal_result(self):
        asyncio.run(self._outer_task_cancellation_stores_terminal_result())

    async def _outer_task_cancellation_stores_terminal_result(self):
        executor = AgentExecutor()
        started = asyncio.Event()

        async def handler(agent_id, task):
            started.set()
            await asyncio.Event().wait()

        execution = asyncio.create_task(
            executor.execute("agent-2", {"id": "task-2"}, handler)
        )
        await started.wait()
        execution_id = next(iter(executor._active_tasks))

        execution.cancel()

        try:
            await execution
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError(
                "execution task should propagate cancellation"
            )

        result = executor.get_result(execution_id)
        assert result["status"] == "cancelled"
        assert result["cancelled"] is True
        assert result["agent_id"] == "agent-2"
        assert result["task_id"] == "task-2"
        assert execution_id not in executor._active_tasks
