#!/usr/bin/env python3
"""Audit the Docker build context before image builds."""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set


DEFAULT_MAX_BYTES = 5 * 1024 * 1024
MAX_BYTES_ENV = "AGENT_ORCHESTRATION_DOCKER_CONTEXT_MAX_BYTES"
PROHIBITED_PATTERNS = (
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "__pycache__",
    "*.egg-info",
    ".coverage",
    "htmlcov",
    "build",
    "dist",
    "node_modules",
)


@dataclass(frozen=True)
class ContextEntry:
    path: str
    size: int


@dataclass(frozen=True)
class ContextAudit:
    total_bytes: int
    top_entries: Sequence[ContextEntry]
    prohibited_paths: Sequence[str]


def load_dockerignore(context: Path) -> List[str]:
    dockerignore = context / ".dockerignore"
    if not dockerignore.exists():
        return []

    patterns: List[str] = []
    for raw_line in dockerignore.read_text().splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def matches_pattern(relative_path: str, pattern: str) -> bool:
    pattern = pattern.strip()
    if not pattern or pattern.startswith("!"):
        return False

    pattern = pattern.strip("/")
    pattern = pattern.rstrip("/")
    if not pattern:
        return False

    path_parts = relative_path.split("/")
    if "/" not in pattern:
        return any(fnmatch.fnmatch(part, pattern) for part in path_parts)

    return (
        fnmatch.fnmatch(relative_path, pattern)
        or relative_path.startswith(pattern + "/")
    )


def is_ignored(relative_path: str, patterns: Iterable[str]) -> bool:
    return any(matches_pattern(relative_path, pattern) for pattern in patterns)


def is_prohibited(relative_path: str) -> bool:
    return any(
        matches_pattern(relative_path, pattern)
        for pattern in PROHIBITED_PATTERNS
    )


def audit_context(context: Path, top_count: int = 10) -> ContextAudit:
    context = context.resolve()
    patterns = load_dockerignore(context)
    totals_by_entry: Dict[str, int] = {}
    prohibited: Set[str] = set()
    total_bytes = 0

    for root, dirs, files in os.walk(context, topdown=True):
        root_path = Path(root)
        kept_dirs = []
        for dirname in sorted(dirs):
            full_path = root_path / dirname
            relative_path = full_path.relative_to(context).as_posix()
            if is_ignored(relative_path, patterns):
                continue
            if is_prohibited(relative_path):
                prohibited.add(relative_path)
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs

        for filename in sorted(files):
            full_path = root_path / filename
            if full_path.is_symlink():
                continue

            relative_path = full_path.relative_to(context).as_posix()
            if is_ignored(relative_path, patterns):
                continue
            if is_prohibited(relative_path):
                prohibited.add(relative_path)

            size = full_path.stat().st_size
            total_bytes += size
            top_level = relative_path.split("/", 1)[0]
            totals_by_entry[top_level] = (
                totals_by_entry.get(top_level, 0) + size
            )

    top_entries = [
        ContextEntry(path=path, size=size)
        for path, size in sorted(
            totals_by_entry.items(),
            key=lambda item: (-item[1], item[0]),
        )[:top_count]
    ]
    return ContextAudit(
        total_bytes=total_bytes,
        top_entries=top_entries,
        prohibited_paths=sorted(prohibited),
    )


def parse_size(raw_value: str) -> int:
    value = raw_value.strip().lower()
    units = (
        ("gib", 1024 ** 3),
        ("gb", 1000 ** 3),
        ("mib", 1024 ** 2),
        ("mb", 1000 ** 2),
        ("kib", 1024),
        ("kb", 1000),
        ("b", 1),
    )
    for suffix, multiplier in units:
        if value.endswith(suffix):
            number = value[: -len(suffix)].strip()
            return int(float(number) * multiplier)
    return int(value)


def format_size(byte_count: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    size = float(byte_count)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{byte_count} B"
        size /= 1024
    return f"{byte_count} B"


def print_report(audit: ContextAudit, max_bytes: int) -> None:
    print(
        "Docker build context: "
        f"{format_size(audit.total_bytes)} "
        f"(budget {format_size(max_bytes)})"
    )
    print("Top included entries:")
    for entry in audit.top_entries:
        print(f"  {format_size(entry.size):>10}  {entry.path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if the Docker build context is too large or unsafe."
    )
    parser.add_argument("--context", default=".", help="Docker context root")
    parser.add_argument(
        "--max-bytes",
        default=os.environ.get(MAX_BYTES_ENV, str(DEFAULT_MAX_BYTES)),
        help=(
            "Maximum included context size. Accepts bytes or KB, MB, "
            "KiB, MiB, GB, GiB suffixes."
        ),
    )
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args(argv)

    max_bytes = parse_size(args.max_bytes)
    audit = audit_context(Path(args.context), top_count=args.top)
    print_report(audit, max_bytes)

    failures = []
    if audit.total_bytes > max_bytes:
        failures.append(
            "context size exceeds budget: "
            f"{format_size(audit.total_bytes)} > {format_size(max_bytes)}"
        )
    if audit.prohibited_paths:
        failures.append(
            "prohibited generated paths are included: "
            + ", ".join(audit.prohibited_paths)
        )

    if failures:
        print("Docker build context audit failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
