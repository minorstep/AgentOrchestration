"""Safe table projection and HTML rendering helpers."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set


@dataclass(frozen=True)
class TableColumn:
    key: str
    label: str
    sensitive: bool = False
    permission: Optional[str] = None


TASK_TABLE_COLUMNS = (
    TableColumn("task_id", "Task ID"),
    TableColumn("title", "Title"),
    TableColumn("status", "Status"),
    TableColumn(
        "assignee_email",
        "Assignee email",
        sensitive=True,
        permission="tasks:view_sensitive",
    ),
    TableColumn(
        "internal_notes",
        "Internal notes",
        sensitive=True,
        permission="tasks:view_sensitive",
    ),
)

MEMBER_TABLE_COLUMNS = (
    TableColumn("member_id", "Member ID"),
    TableColumn("name", "Name"),
    TableColumn("role", "Role"),
    TableColumn(
        "email",
        "Email",
        sensitive=True,
        permission="members:view_sensitive",
    ),
    TableColumn(
        "last_login_ip",
        "Last login IP",
        sensitive=True,
        permission="members:view_sensitive",
    ),
)


def _normalise(values: Optional[Iterable[str]]) -> Set[str]:
    return set(values or ())


def visible_authorized_columns(
    columns: Sequence[TableColumn],
    visible_keys: Optional[Iterable[str]],
    granted_permissions: Optional[Iterable[str]],
) -> List[TableColumn]:
    """Return the columns safe to fetch and render for this request."""
    requested = _normalise(visible_keys)
    permissions = _normalise(granted_permissions)
    allowed: List[TableColumn] = []

    for column in columns:
        if requested and column.key not in requested:
            continue
        if column.sensitive and column.permission not in permissions:
            continue
        allowed.append(column)

    return allowed


def project_table_rows(
    rows: Iterable[Mapping[str, Any]],
    columns: Sequence[TableColumn],
    visible_keys: Optional[Iterable[str]],
    granted_permissions: Optional[Iterable[str]],
) -> List[Dict[str, Any]]:
    """Project rows before rendering so hidden data is never exposed."""
    allowed_columns = visible_authorized_columns(
        columns,
        visible_keys,
        granted_permissions,
    )
    allowed_keys = {column.key for column in allowed_columns}
    return [
        {key: value for key, value in row.items() if key in allowed_keys}
        for row in rows
    ]


def render_table_markup(
    rows: Iterable[Mapping[str, Any]],
    columns: Sequence[TableColumn],
    visible_keys: Optional[Iterable[str]],
    granted_permissions: Optional[Iterable[str]],
) -> str:
    allowed_columns = visible_authorized_columns(
        columns,
        visible_keys,
        granted_permissions,
    )
    projected_rows = project_table_rows(
        rows,
        allowed_columns,
        (column.key for column in allowed_columns),
        granted_permissions,
    )

    header_cells = "".join(
        f"<th data-column=\"{escape(column.key)}\">"
        f"{escape(column.label)}</th>"
        for column in allowed_columns
    )
    body_rows = []
    for row in projected_rows:
        cells = "".join(
            f"<td data-column=\"{escape(column.key)}\">"
            f"{escape(str(row.get(column.key, '')))}</td>"
            for column in allowed_columns
        )
        body_rows.append(f"<tr>{cells}</tr>")

    return (
        "<table>"
        f"<thead><tr>{header_cells}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table>"
    )
