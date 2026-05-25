import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "check_docker_context.py"
)
SPEC = importlib.util.spec_from_file_location(
    "check_docker_context",
    SCRIPT_PATH,
)
docker_context = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = docker_context
SPEC.loader.exec_module(docker_context)


def write_file(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class DockerContextAuditTests(unittest.TestCase):
    def test_audit_counts_included_files_and_top_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            dockerignore_content = ".pytest_cache\n"
            write_file(context / ".dockerignore", dockerignore_content)
            write_file(context / "src" / "app.py", "print('ok')\n")
            write_file(context / "README.md", "docs\n")
            write_file(context / ".pytest_cache" / "cache", "cached-data\n")

            audit = docker_context.audit_context(context)

        self.assertEqual(
            audit.total_bytes,
            len(dockerignore_content) + len("print('ok')\n") + len("docs\n"),
        )
        self.assertEqual(
            [entry.path for entry in audit.top_entries],
            [".dockerignore", "src", "README.md"],
        )
        self.assertEqual(audit.prohibited_paths, [])

    def test_audit_reports_prohibited_generated_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            write_file(context / ".mypy_cache" / "cache.json", "{}")
            write_file(context / "src" / "app.py", "print('ok')\n")

            audit = docker_context.audit_context(context)

        self.assertIn(".mypy_cache", audit.prohibited_paths)

    def test_main_fails_when_budget_is_exceeded(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            write_file(context / "large.txt", "x" * 12)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = docker_context.main(
                    [
                        "--context",
                        str(context),
                        "--max-bytes",
                        "10",
                        "--top",
                        "2",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("context size exceeds budget", stderr.getvalue())
        self.assertIn("large.txt", stdout.getvalue())

    def test_main_passes_when_generated_dirs_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            write_file(context / ".dockerignore", ".pytest_cache\n")
            write_file(context / ".pytest_cache" / "cache", "cached-data\n")
            write_file(context / "app.py", "print('ok')\n")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = docker_context.main(
                    ["--context", str(context), "--max-bytes", "1KiB"]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("Docker build context", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
