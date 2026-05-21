"""Workspace-scoped task state repository."""

import copy
import re
from typing import Any, Dict, List, Optional, Tuple


class UnscopedTaskStateAccessError(ValueError):
    """Raised when task state access lacks workspace scope."""


_UNSCOPED_METHODS = {
    "delete_by_task_id",
    "get_by_task_id",
    "update_by_task_id",
}


class ScopedTaskStateRepository:
    """Stores task state behind mandatory workspace predicates."""

    def __init__(self):
        self._records: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def __getattr__(self, name: str):
        if name in _UNSCOPED_METHODS:
            raise UnscopedTaskStateAccessError(
                f"{name} is blocked; use workspace-scoped task state methods"
            )
        raise AttributeError(name)

    def upsert(
        self,
        workspace_id: str,
        task_id: str,
        state: Dict[str, Any],
    ) -> Dict[str, Any]:
        key = self._key(workspace_id, task_id)
        record = copy.deepcopy(state)
        record["workspace_id"] = key[0]
        record["task_id"] = key[1]
        self._records[key] = record
        return copy.deepcopy(record)

    def get(self, workspace_id: str, task_id: str) -> Optional[Dict[str, Any]]:
        key = self._key(workspace_id, task_id)
        record = self._records.get(key)
        return copy.deepcopy(record) if record is not None else None

    def update(
        self,
        workspace_id: str,
        task_id: str,
        changes: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        key = self._key(workspace_id, task_id)
        if key not in self._records:
            return None

        record = copy.deepcopy(self._records[key])
        record.update(copy.deepcopy(changes))
        record["workspace_id"] = key[0]
        record["task_id"] = key[1]
        self._records[key] = record
        return copy.deepcopy(record)

    def delete(self, workspace_id: str, task_id: str) -> bool:
        key = self._key(workspace_id, task_id)
        return self._records.pop(key, None) is not None

    def list_workspace(self, workspace_id: str) -> List[Dict[str, Any]]:
        workspace_id = self._require_scope("workspace_id", workspace_id)
        return [
            copy.deepcopy(record)
            for (record_workspace_id, _), record in self._records.items()
            if record_workspace_id == workspace_id
        ]

    def _key(self, workspace_id: str, task_id: str) -> Tuple[str, str]:
        return (
            self._require_scope("workspace_id", workspace_id),
            self._require_scope("task_id", task_id),
        )

    def _require_scope(self, name: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise UnscopedTaskStateAccessError(f"{name} is required")
        return value.strip()


def postgres_workspace_policy_sql(
    table_name: str = "task_state",
    workspace_column: str = "workspace_id",
    setting_name: str = "app.current_workspace_id",
) -> str:
    """Returns PostgreSQL row-level policy SQL for task state."""
    table = _validate_identifier(table_name)
    column = _validate_identifier(workspace_column)
    setting = setting_name.replace("'", "''")

    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;\n"
        f"CREATE POLICY {table}_workspace_scope ON {table}\n"
        "USING (\n"
        f"    {column} = current_setting('{setting}', true)\n"
        ")\n"
        "WITH CHECK (\n"
        f"    {column} = current_setting('{setting}', true)\n"
        ");"
    )


def _validate_identifier(identifier: str) -> str:
    if not isinstance(identifier, str):
        raise ValueError("SQL identifier must be a string")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise ValueError("SQL identifier must contain only safe characters")
    return identifier
