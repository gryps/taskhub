import unittest

from app.workflow_graph import resolve_workflow_transition


class WorkflowGraphTests(unittest.TestCase):
    def test_happy_path(self) -> None:
        state = "awaiting_plan_approval"
        for action, expected in (
            ("approve_plan", "implementation"),
            ("implementation_done", "review"),
            ("review_pass", "risk"),
            ("risk_clear", "awaiting_supervision"),
            ("approve_release", "completed"),
        ):
            result = resolve_workflow_transition(state, action)
            self.assertEqual(result["next_state"], expected)
            state = expected

    def test_pause_and_resume_preserve_stage(self) -> None:
        paused = resolve_workflow_transition("review", "pause")
        self.assertEqual(paused["next_state"], "paused")
        self.assertEqual(paused["next_resume_state"], "review")
        resumed = resolve_workflow_transition("paused", "resume", paused["next_resume_state"])
        self.assertEqual(resumed["next_state"], "review")

    def test_rework_increments_iteration(self) -> None:
        result = resolve_workflow_transition("review", "review_reject")
        self.assertEqual(result["next_state"], "implementation")
        self.assertTrue(result["increments_iteration"])

    def test_invalid_transition_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_workflow_transition("implementation", "approve_release")


if __name__ == "__main__":
    unittest.main()
