import pytest

from src.agent.sandbox import AgentSandbox, ResourceLimits


def test_sandbox_create_revalidates_mutated_resource_limits(tmp_path):
    sandbox = AgentSandbox(base_path=str(tmp_path))
    limits = ResourceLimits(cpu_time=30, memory_mb=256, disk_mb=50)
    limits.disk_mb = -1

    with pytest.raises(ValueError, match="sandbox.resource_limits.disk_mb"):
        sandbox.create("agent-a", limits)

    assert not (tmp_path / "agent-a").exists()


def test_apply_limits_revalidates_before_os_limit_calls(monkeypatch, tmp_path):
    sandbox = AgentSandbox(base_path=str(tmp_path))
    limits = ResourceLimits(cpu_time=30, memory_mb=256, disk_mb=50)
    limits.memory_mb = "invalid"
    calls = []

    def record_setrlimit(*args):
        calls.append(args)

    monkeypatch.setattr(
        "src.agent.sandbox.resource.setrlimit",
        record_setrlimit,
    )

    with pytest.raises(ValueError, match="sandbox.resource_limits.memory_mb"):
        sandbox.apply_limits("agent-a", limits)

    assert calls == []
