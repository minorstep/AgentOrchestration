import asyncio
import time

import pytest

from src.sdk.decorators import task


def test_task_decorator_runs_sync_function():
    @task(name="sync.add")
    def add(left, right):
        return left + right

    result = asyncio.run(add(2, 3))

    assert result == 5
    assert add.__task_config__ == {
        "name": "sync.add",
        "retries": 0,
        "timeout": 300,
    }


def test_task_decorator_runs_sync_function_with_keyword_only_args():
    @task()
    def format_total(*, amount, currency):
        return f"{currency} {amount}"

    result = asyncio.run(format_total(amount=100, currency="GBP"))

    assert result == "GBP 100"
    assert format_total.__task_config__["name"] == "format_total"


def test_task_decorator_preserves_async_function_behavior():
    @task(name="async.double")
    async def double(value):
        await asyncio.sleep(0)
        return value * 2

    result = asyncio.run(double(4))

    assert result == 8
    assert double.__task_config__["name"] == "async.double"


def test_task_decorator_propagates_sync_handler_error():
    @task()
    def fail_sync():
        raise ValueError("handler failed")

    with pytest.raises(ValueError, match="handler failed"):
        asyncio.run(fail_sync())


def test_task_decorator_times_out_sync_function():
    @task(name="sync.slow", timeout=0.01)
    def slow_sync():
        time.sleep(0.05)
        return "late"

    with pytest.raises(TimeoutError, match="sync.slow timed out"):
        asyncio.run(slow_sync())
