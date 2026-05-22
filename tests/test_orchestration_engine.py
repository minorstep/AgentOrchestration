import asyncio

import pytest

from src.agent import AgentStatus
from src.orchestrator.engine import OrchestrationEngine


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def engine_factory():
    engines = []

    def factory(**kwargs):
        engine = OrchestrationEngine(**kwargs)
        engines.append(engine)
        return engine

    yield factory

    for engine in engines:
        engine.executor.shutdown(wait=True)


class TestOrchestrationEngineDelegationDepth:
    def test_rejects_over_depth_task_before_side_effects(self, engine_factory):
        engine = engine_factory(max_delegation_depth=2)
        calls = []

        async def pre_execute(task):
            calls.append(("pre_execute", task["id"]))

        async def should_not_run(agent, task):
            raise AssertionError("over-depth task reached worker execution")

        engine.register_hook("pre_execute", pre_execute)
        engine._run_agent_task = should_not_run

        task_id = engine.scheduler.enqueue(
            {
                "type": "delegate",
                "target_agent": "missing-agent",
                "delegation_depth": 3,
            }
        )
        task = run(engine.scheduler.dequeue())

        run(engine._execute_task(task))

        outcome = engine.get_terminal_outcome(task_id)
        assert outcome["status"] == "failed"
        assert outcome["reason"] == "delegation_depth_exceeded"
        assert outcome["delegation_depth"] == 3
        assert outcome["max_delegation_depth"] == 2
        assert outcome["terminal"] is True
        assert task["terminal_outcome"] is outcome
        assert task["status"] == "failed"
        assert calls == []
        assert task_id not in engine.scheduler._in_flight
        assert not engine.scheduler.fail(task_id)

    def test_rejects_only_once_under_duplicate_retry_delivery(
        self,
        engine_factory,
    ):
        engine = engine_factory(max_delegation_depth=1)
        task_id = engine.scheduler.enqueue(
            {
                "type": "delegate",
                "target_agent": "worker",
                "delegation_depth": 2,
            }
        )
        task = run(engine.scheduler.dequeue())

        run(engine._execute_task(task))
        first = engine.get_terminal_outcome(task_id)
        run(engine._execute_task(task))

        assert engine.get_terminal_outcome(task_id) is first
        assert task_id not in engine.scheduler._in_flight

    def test_allows_task_at_max_depth_to_execute_and_clear_lock(
        self,
        engine_factory,
    ):
        engine = engine_factory(max_delegation_depth=2)
        agent_id = engine.registry.register(
            "delegate-worker",
            "worker.processor",
        )
        results = []

        async def run_agent(agent, task):
            return {
                "accepted_depth": task["delegation_depth"],
                "agent": agent["name"],
            }

        async def post_execute(task, result):
            results.append((task["id"], result))

        engine._run_agent_task = run_agent
        engine.register_hook("post_execute", post_execute)

        task_id = engine.scheduler.enqueue(
            {
                "type": "delegate",
                "target_agent": agent_id,
                "delegation_depth": 2,
            }
        )
        task = run(engine.scheduler.dequeue())

        run(engine._execute_task(task))

        assert engine.get_terminal_outcome(task_id) is None
        assert results == [
            (
                task_id,
                {"accepted_depth": 2, "agent": "delegate-worker"},
            )
        ]
        assert task_id not in engine.scheduler._in_flight
        assert (
            engine.registry.get(agent_id)["status"]
            == AgentStatus.PAUSED.value
        )

    def test_treats_invalid_depth_as_over_depth(self, engine_factory):
        engine = engine_factory(max_delegation_depth=4)
        task_id = engine.scheduler.enqueue(
            {
                "type": "delegate",
                "target_agent": "worker",
                "metadata": {"delegation_depth": "not-a-number"},
            }
        )
        task = run(engine.scheduler.dequeue())

        run(engine._execute_task(task))

        outcome = engine.get_terminal_outcome(task_id)
        assert outcome["reason"] == "delegation_depth_exceeded"
        assert outcome["delegation_depth"] == 5
