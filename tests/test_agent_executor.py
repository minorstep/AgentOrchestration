import asyncio

from src.agent.executor import AgentExecutor


def test_handler_failure_records_terminal_reason():
    async def run():
        executor = AgentExecutor()

        async def handler(agent_id, task):
            raise RuntimeError("boom")

        execution_id = await executor.execute(
            "agent-1",
            {"id": "task-1"},
            handler,
        )

        result = executor.get_result(execution_id)
        assert result["status"] == "failed"
        assert result["reason"] == "boom"
        assert result["error_type"] == "RuntimeError"
        assert result["agent_id"] == "agent-1"
        assert result["task_id"] == "task-1"

    asyncio.run(run())


def test_cancel_records_reason_once_before_cancelling_work():
    async def run():
        executor = AgentExecutor()
        started = asyncio.Event()

        async def handler(agent_id, task):
            started.set()
            await asyncio.sleep(10)

        execution_task = asyncio.create_task(
            executor.execute("agent-1", {"id": "task-1"}, handler)
        )
        await started.wait()
        execution_id = next(iter(executor.active_executions()))

        assert executor.cancel(execution_id, reason="caller_cancelled")
        returned_id = await execution_task

        result = executor.get_result(execution_id)
        assert returned_id == execution_id
        assert result["status"] == "cancelled"
        assert result["reason"] == "caller_cancelled"
        assert result["task_id"] == "task-1"
        assert result["agent_id"] == "agent-1"
        assert executor.active_executions() == {}

    asyncio.run(run())


def test_shutdown_records_failure_reason_before_cancelling_active_work():
    async def run():
        executor = AgentExecutor()
        started = asyncio.Event()

        async def handler(agent_id, task):
            started.set()
            await asyncio.sleep(10)

        execution_task = asyncio.create_task(
            executor.execute("agent-1", {"id": "task-1"}, handler)
        )
        await started.wait()
        execution_id = next(iter(executor.active_executions()))

        await executor.shutdown()
        returned_id = await execution_task

        result = executor.get_result(execution_id)
        assert returned_id == execution_id
        assert result["status"] == "failed"
        assert result["reason"] == "worker_shutdown"
        assert result["error_type"] == "RuntimeLifecycleError"
        assert result["task_id"] == "task-1"
        assert result["agent_id"] == "agent-1"
        assert executor.active_executions() == {}

    asyncio.run(run())
