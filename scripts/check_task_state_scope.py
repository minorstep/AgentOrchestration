#!/usr/bin/env python3
"""Static guard for unscoped task_state SQL literals."""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.storage.task_state import (  # noqa: E402
    UnscopedTaskStateAccessError,
    assert_task_state_sql_scoped,
)


SKIP_DIRS = {".git", ".venv", "__pycache__", "tests"}


def iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def iter_string_literals(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value, node.lineno
        elif isinstance(node, ast.JoinedStr):
            literal = "".join(
                value.value
                for value in node.values
                if isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            )
            if literal:
                yield literal, node.lineno


def check_file(path: Path, root: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [f"{path.relative_to(root)}:{exc.lineno}: syntax error"]

    failures = []
    for literal, lineno in iter_string_literals(tree):
        if "task_state" not in literal.lower():
            continue
        try:
            assert_task_state_sql_scoped(literal)
        except UnscopedTaskStateAccessError as exc:
            failures.append(f"{path.relative_to(root)}:{lineno}: {exc}")
    return failures


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    failures = [
        failure
        for path in iter_python_files(root)
        for failure in check_file(path, root)
    ]
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
