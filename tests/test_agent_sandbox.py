from pathlib import Path

import pytest

from src.agent.sandbox import AgentSandbox


def test_base_path_is_resolved_before_use(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    sandbox = AgentSandbox("../workspace")

    assert sandbox.base_path == workspace.resolve()


def test_create_returns_resolved_child_path(tmp_path):
    sandbox = AgentSandbox(str(tmp_path))

    sandbox_path = sandbox.create("agent-1")

    assert sandbox_path == (tmp_path / "agent-1").resolve()
    assert sandbox.get_path("agent-1") == sandbox_path


def test_create_rejects_parent_directory_escape(tmp_path):
    sandbox = AgentSandbox(str(tmp_path / "root"))

    with pytest.raises(ValueError, match="escapes base path"):
        sandbox.create("../outside")

    assert sandbox.get_path("../outside") is None
    assert not (tmp_path / "outside").exists()


def test_create_rejects_existing_symlink_escape(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    sandbox = AgentSandbox(str(root))

    with pytest.raises(ValueError, match="escapes base path"):
        sandbox.create("link/agent-1")

    assert sandbox.get_path("link/agent-1") is None
    assert not (outside / "agent-1").exists()


def test_destroy_removes_resolved_sandbox(tmp_path):
    sandbox = AgentSandbox(str(tmp_path))
    sandbox_path = sandbox.create("agent-1")

    assert sandbox.destroy("agent-1")

    assert not Path(sandbox_path).exists()
