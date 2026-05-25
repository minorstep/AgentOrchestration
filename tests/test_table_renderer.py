import unittest
import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "api"
    / "table_renderer.py"
)
SPEC = importlib.util.spec_from_file_location("table_renderer", SCRIPT_PATH)
table_renderer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = table_renderer
SPEC.loader.exec_module(table_renderer)

MEMBER_TABLE_COLUMNS = table_renderer.MEMBER_TABLE_COLUMNS
TASK_TABLE_COLUMNS = table_renderer.TASK_TABLE_COLUMNS
project_table_rows = table_renderer.project_table_rows
render_table_markup = table_renderer.render_table_markup


class TableRendererTests(unittest.TestCase):
    def test_hidden_sensitive_task_fields_are_absent_from_markup(self):
        rows = [
            {
                "task_id": "task-1",
                "title": "Rotate secret",
                "status": "open",
                "assignee_email": "ops@example.com",
                "internal_notes": "Token rotation before launch",
            }
        ]

        markup = render_table_markup(
            rows,
            TASK_TABLE_COLUMNS,
            visible_keys={"task_id", "title", "status"},
            granted_permissions={"tasks:view_sensitive"},
        )

        self.assertIn("Rotate secret", markup)
        self.assertNotIn("ops@example.com", markup)
        self.assertNotIn("Token rotation", markup)
        self.assertNotIn('data-column="assignee_email"', markup)
        self.assertNotIn('data-column="internal_notes"', markup)

    def test_unauthorized_column_toggle_does_not_render_member_data(self):
        rows = [
            {
                "member_id": "member-1",
                "name": "Alex",
                "role": "admin",
                "email": "alex@example.com",
                "last_login_ip": "192.0.2.12",
            }
        ]

        markup = render_table_markup(
            rows,
            MEMBER_TABLE_COLUMNS,
            visible_keys={"member_id", "name", "email", "last_login_ip"},
            granted_permissions=set(),
        )

        self.assertIn("Alex", markup)
        self.assertNotIn("alex@example.com", markup)
        self.assertNotIn("192.0.2.12", markup)
        self.assertNotIn('data-column="email"', markup)
        self.assertNotIn('data-column="last_login_ip"', markup)

    def test_authorized_toggle_fetches_only_allowed_visible_fields(self):
        rows = [
            {
                "member_id": "member-1",
                "name": "Alex",
                "role": "admin",
                "email": "alex@example.com",
                "last_login_ip": "192.0.2.12",
            }
        ]

        payload = project_table_rows(
            rows,
            MEMBER_TABLE_COLUMNS,
            visible_keys={"member_id", "name", "email"},
            granted_permissions={"members:view_sensitive"},
        )
        markup = render_table_markup(
            rows,
            MEMBER_TABLE_COLUMNS,
            visible_keys={"member_id", "name", "email"},
            granted_permissions={"members:view_sensitive"},
        )

        self.assertEqual(
            payload,
            [
                {
                    "member_id": "member-1",
                    "name": "Alex",
                    "email": "alex@example.com",
                }
            ],
        )
        self.assertIn("alex@example.com", markup)
        self.assertNotIn("192.0.2.12", markup)

    def test_rendered_values_are_escaped(self):
        rows = [
            {
                "task_id": "task-1",
                "title": "<script>alert(1)</script>",
                "status": "open",
            }
        ]

        markup = render_table_markup(
            rows,
            TASK_TABLE_COLUMNS,
            visible_keys={"task_id", "title", "status"},
            granted_permissions=set(),
        )

        self.assertNotIn("<script>", markup)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", markup)


if __name__ == "__main__":
    unittest.main()
