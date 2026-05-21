import inspect

import pytest

from src.storage import (
    ScopedTaskStateRepository,
    UnscopedTaskStateAccessError,
    postgres_workspace_policy_sql,
)


class TestScopedTaskStateRepository:
    def setup_method(self):
        self.repository = ScopedTaskStateRepository()

    def test_task_id_collision_is_scoped_by_workspace(self):
        self.repository.upsert(
            "workspace-a",
            "task-1",
            {"status": "queued", "retry_count": 0},
        )
        self.repository.upsert(
            "workspace-b",
            "task-1",
            {"status": "running", "retry_count": 2},
        )

        self.repository.update(
            "workspace-a",
            "task-1",
            {"status": "complete", "retry_count": 1},
        )

        workspace_a_task = self.repository.get("workspace-a", "task-1")
        workspace_b_task = self.repository.get("workspace-b", "task-1")

        assert workspace_a_task["status"] == "complete"
        assert workspace_a_task["retry_count"] == 1
        assert workspace_b_task["status"] == "running"
        assert workspace_b_task["retry_count"] == 2

    @pytest.mark.parametrize(
        "method,args",
        [
            ("upsert", ("", "task-1", {"status": "queued"})),
            ("get", ("", "task-1")),
            ("update", ("", "task-1", {"status": "complete"})),
            ("delete", ("", "task-1")),
            ("list_workspace", ("",)),
        ],
    )
    def test_reads_and_writes_require_workspace_scope(self, method, args):
        with pytest.raises(UnscopedTaskStateAccessError):
            getattr(self.repository, method)(*args)

    def test_unscoped_task_id_helpers_are_blocked(self):
        with pytest.raises(UnscopedTaskStateAccessError):
            self.repository.get_by_task_id("task-1")

        public_methods = [
            (name, member)
            for name, member in inspect.getmembers(
                ScopedTaskStateRepository,
                predicate=inspect.isfunction,
            )
            if not name.startswith("_")
        ]

        for name, member in public_methods:
            parameters = inspect.signature(member).parameters
            if "task_id" in parameters:
                assert "workspace_id" in parameters, name

    def test_list_workspace_excludes_other_workspace_task_state(self):
        self.repository.upsert("workspace-a", "task-1", {"status": "queued"})
        self.repository.upsert("workspace-b", "task-1", {"status": "queued"})
        self.repository.upsert("workspace-a", "task-2", {"status": "running"})

        tasks = self.repository.list_workspace("workspace-a")

        assert {task["task_id"] for task in tasks} == {"task-1", "task-2"}
        assert {task["workspace_id"] for task in tasks} == {"workspace-a"}

    def test_postgres_policy_sql_enforces_workspace_predicates(self):
        sql = postgres_workspace_policy_sql()

        assert "ENABLE ROW LEVEL SECURITY" in sql
        assert "CREATE POLICY task_state_workspace_scope" in sql
        assert (
            "workspace_id = current_setting('app.current_workspace_id', true)"
            in sql
        )
        assert "WITH CHECK" in sql

    def test_postgres_policy_sql_rejects_unsafe_identifiers(self):
        with pytest.raises(ValueError):
            postgres_workspace_policy_sql(
                table_name="task_state; DROP TABLE tasks",
            )
