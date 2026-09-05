import unittest

from app.taskhub import automatic_rework_requirement, failure_needs_workspace_bootstrap


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


if __name__ == "__main__":
    unittest.main()
