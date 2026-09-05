import unittest

from app.taskhub import (
    automatic_rework_category,
    automatic_rework_requirement,
    automatic_rework_route,
    failure_needs_workspace_bootstrap,
)


class AutomaticReworkTests(unittest.TestCase):
    def test_requirement_contains_failure_and_original_scope(self) -> None:
        requirement = automatic_rework_requirement(
            {"context": {"requirement": "修复质量基线，不部署生产"}},
            {"type": "test.run", "title": "运行 npm run check", "input": {"command": "check"}},
            {"code": "TEST_FAILED", "stdout_tail": "NOT NULL constraint failed: products.shop_id"},
        )

        self.assertIn("products.shop_id", requirement)
        self.assertIn("修复质量基线", requirement)
        self.assertIn("do not weaken, skip, delete", requirement)
        self.assertIn("Do not commit, deploy", requirement)

    def test_command_not_found_is_routed_to_workspace_bootstrap(self) -> None:
        self.assertTrue(failure_needs_workspace_bootstrap({"exit_code": 127, "stderr_tail": "tsc: not found\n"}))
        self.assertTrue(failure_needs_workspace_bootstrap({"stderr_tail": "tool: command not found"}))
        self.assertFalse(failure_needs_workspace_bootstrap({"exit_code": 1, "stdout_tail": "11 tests failed"}))

    def test_failure_categories_separate_environment_lint_and_tests(self) -> None:
        self.assertEqual(automatic_rework_category({"exit_code": 127}), "workspace_environment")
        self.assertEqual(automatic_rework_category({"stdout_tail": "ruff check\nF401 imported but unused"}), "lint")
        self.assertEqual(automatic_rework_category({"stdout_tail": "pytest\n3 failed"}), "tests")

    def test_rework_route_is_reusable_for_a_and_b(self) -> None:
        for line in ("a", "b"):
            workspace_id = f"storefront-implementation-{line}"
            worker_id = f"worker-implementation-{line}"
            route = automatic_rework_route(
                {"workspace_id": workspace_id},
                {"state": "active", "workspace_id": workspace_id, "default_worker_id": worker_id},
            )
            self.assertEqual(route, {"workspace_id": workspace_id, "worker_id": worker_id})

    def test_rework_route_rejects_cross_line_workspace(self) -> None:
        route = automatic_rework_route(
            {"workspace_id": "storefront-implementation-a"},
            {
                "state": "active",
                "workspace_id": "storefront-implementation-b",
                "default_worker_id": "worker-implementation-b",
            },
        )
        self.assertIsNone(route)

    def test_rework_route_requires_active_complete_pipeline(self) -> None:
        self.assertIsNone(
            automatic_rework_route(
                {"workspace_id": "storefront-a"},
                {"state": "paused", "workspace_id": "storefront-a", "default_worker_id": "worker-a"},
            )
        )
        self.assertIsNone(
            automatic_rework_route(
                {"workspace_id": "storefront-a"},
                {"state": "active", "workspace_id": "storefront-a", "default_worker_id": None},
            )
        )


if __name__ == "__main__":
    unittest.main()
