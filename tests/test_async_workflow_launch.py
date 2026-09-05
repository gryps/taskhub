import unittest
from unittest.mock import MagicMock, patch

from app.integration import FirstWorkflowRequest, launch_first_workflow


class AsyncWorkflowLaunchTests(unittest.TestCase):
    @patch("app.integration.create_planning_workflow")
    @patch("app.integration.connect")
    def test_first_workflow_returns_queued_flow_without_calling_models(self, connect, create_workflow) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connect.return_value.__enter__.return_value = connection
        create_workflow.return_value = {"workflow_id": "flow-1", "state": "planning"}

        result = launch_first_workflow(
            "douyin-listing-workbench",
            FirstWorkflowRequest(requirement="add checkout validation"),
        )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["workflow"]["state"], "planning")
        create_workflow.assert_called_once_with("douyin-listing-workbench", "add checkout validation", None)


if __name__ == "__main__":
    unittest.main()
