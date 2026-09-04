import unittest

from app.workflow_executor import action_for_result, validate_stage_output


class WorkflowExecutorTests(unittest.TestCase):
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
