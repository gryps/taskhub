import unittest
from unittest.mock import patch

from app.workflow_executor import AUTOMATED_STAGES, action_for_result, run_automation_cycle, validate_stage_output


class WorkflowExecutorTests(unittest.TestCase):
    def test_planning_is_a_persistent_automated_stage(self) -> None:
        self.assertEqual(AUTOMATED_STAGES["planning"], "planner")

    @patch("app.workflow_executor.complete_planning_run")
    @patch("app.workflow_executor.run_structured_role")
    @patch("app.workflow_executor.run_role")
    @patch("app.workflow_executor.claim_next_role_run")
    def test_planning_cycle_only_calls_planner(
        self, claim_next, run_role, run_structured_role, complete_planning
    ) -> None:
        claim_next.return_value = {
            "workflow_id": "b88d292f-1453-40db-8d99-b50c5b39d956",
            "run_id": "79f228b5-16d1-4ec6-837f-a8e3c807ff45",
            "state": "planning",
            "role": "planner",
            "project": "douyin-listing-workbench",
            "summary": "build the feature",
            "context": {"requirement": "build the feature"},
            "pipeline_id": None,
        }
        run_role.return_value = {
            "role": "planner",
            "source": "chatgpt_plus_account",
            "status": "succeeded",
            "summary": "implementation plan",
            "risk_level": "medium",
            "tasks": [],
            "attempts": [],
        }

        self.assertTrue(run_automation_cycle())

        run_role.assert_called_once()
        run_structured_role.assert_not_called()
        complete_planning.assert_called_once()

    def test_review_and_risk_actions(self) -> None:
        self.assertEqual(action_for_result("review", "pass"), "review_pass")
        self.assertEqual(action_for_result("review", "rework"), "review_reject")
        self.assertEqual(action_for_result("risk", "clear"), "risk_clear")
        self.assertEqual(action_for_result("risk", "escalate"), "risk_block")

    def test_supervisor_is_recommendation_only(self) -> None:
        self.assertIsNone(action_for_result("awaiting_supervision", "approve"))

    def test_stage_output_requires_known_verdict_and_summary(self) -> None:
        output = validate_stage_output(
            "review",
            {"verdict": "PASS", "summary": "tests passed", "findings": [], "test_gaps": []},
        )
        self.assertEqual(output["verdict"], "pass")
        with self.assertRaises(ValueError):
            validate_stage_output("risk", {"verdict": "maybe", "summary": "unclear"})
        with self.assertRaises(ValueError):
            validate_stage_output("review", {"verdict": "pass", "summary": "", "findings": []})

    def test_stage_output_requires_list_fields(self) -> None:
        with self.assertRaises(ValueError):
            validate_stage_output("risk", {"verdict": "clear", "summary": "ok", "risks": "none"})


if __name__ == "__main__":
    unittest.main()
