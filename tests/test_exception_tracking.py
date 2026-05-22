import asyncio
import json
import logging
import sys

from src.agent.executor import AgentExecutor
from src.common.exception_tracking import (
    ExceptionContextSanitizer,
    ExceptionTracker,
)
from src.common.logging import StructuredFormatter
from src.common.metrics import MetricsCollector
from src.orchestrator.engine import OrchestrationEngine


def test_sanitizer_preserves_lookup_fields_and_drops_nested_payloads():
    sanitizer = ExceptionContextSanitizer()

    event = sanitizer.sanitize(
        RuntimeError("payment token leaked"),
        context={
            "task_id": "task-123",
            "payload": {"customer": "private"},
            "worker": {
                "attempt": 2,
                "raw_payload": "secret-body",
                "nested": {
                    "input": {"token": "private-token"},
                    "safe_flag": True,
                },
            },
        },
    )

    assert event["task_id"] == "task-123"
    assert event["error_class"] == "RuntimeError"
    assert "payment token leaked" not in repr(event)
    assert "private" not in repr(event)
    assert "secret-body" not in repr(event)
    assert "safe_flag" in repr(event)


def test_sanitizer_records_local_names_without_local_values():
    sanitizer = ExceptionContextSanitizer()

    event = sanitizer.sanitize(
        ValueError("bad local"),
        task_id="task-456",
        local_variables={
            "payload": {"card": "4111111111111111"},
            "temporary_secret": "secret",
        },
    )

    assert event["task_id"] == "task-456"
    assert event["error_class"] == "ValueError"
    assert event["local_variable_count"] == 2
    assert "4111111111111111" not in repr(event)
    assert "secret" not in repr(event).lower()


def test_exception_tracker_supports_dashboard_lookup_by_task_and_error_class():
    metrics = MetricsCollector()
    tracker = ExceptionTracker(metrics_collector=metrics)

    event = tracker.capture(
        KeyError("raw payload should not appear"),
        task={
            "id": "task-789",
            "payload": {"document": "raw-document"},
        },
        local_variables={"raw_payload": "raw-local"},
    )

    lookup = tracker.find("task-789", "KeyError")
    assert lookup["event_id"] == event["event_id"]
    assert lookup["task_id"] == "task-789"
    assert lookup["error_class"] == "KeyError"
    assert "raw-document" not in repr(lookup)
    assert "raw-local" not in repr(lookup)
    assert metrics.snapshot()["counters"]["exceptions.tracked.sanitized"] == 1


def test_executor_stores_sanitized_exception_event():
    tracker = ExceptionTracker(metrics_collector=MetricsCollector())
    executor = AgentExecutor(exception_tracker=tracker)

    async def failing_handler(agent_id, task):
        leaked_payload = task["payload"]
        assert leaked_payload
        raise RuntimeError("handler saw raw payload")

    execution_id = asyncio.run(
        executor.execute(
            "agent-secret",
            {"id": "task-exec", "payload": {"secret": "raw"}},
            failing_handler,
        )
    )

    result = executor.get_result(execution_id)
    assert result["error"]["task_id"] == "task-exec"
    assert result["error"]["error_class"] == "RuntimeError"
    assert "handler saw raw payload" not in repr(result)
    assert "agent-secret" not in repr(result)
    assert "raw" not in repr(result)


def test_engine_error_hook_receives_sanitized_event():
    engine = OrchestrationEngine()
    received = []

    async def error_hook(task, error_event):
        received.append(error_event)

    engine.register_hook("on_error", error_hook)
    asyncio.run(
        engine._execute_task(
            {
                "id": "task-engine",
                "target_agent": "missing-agent",
                "payload": {"api_key": "secret"},
            }
        )
    )

    assert received[0]["task_id"] == "task-engine"
    assert received[0]["error_class"] == "ValueError"
    assert "missing-agent" not in repr(received)
    assert "secret" not in repr(received)


def test_structured_logging_exception_context_is_sanitized():
    formatter = StructuredFormatter()

    try:
        raise RuntimeError("raw card 4111111111111111")
    except RuntimeError:
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "worker failed",
            (),
            exc_info=sys.exc_info(),
        )
        record.exception_context = {
            "task_id": "task-log",
            "payload": {"card": "4111111111111111"},
            "locals": {"token": "secret"},
        }
        record.local_variables = {
            "payload": {"card": "4111111111111111"},
        }
        output = json.loads(formatter.format(record))

    assert output["exception"]["task_id"] == "task-log"
    assert output["exception"]["error_class"] == "RuntimeError"
    assert "4111111111111111" not in repr(output)
    assert "secret" not in repr(output).lower()
