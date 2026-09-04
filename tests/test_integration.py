import os
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.integration import OnboardRequest, WorkspaceTemplate, _safe_source_command, read_source_snapshot


class IntegrationTests(unittest.TestCase):
    def test_source_command_quotes_safe_absolute_path(self):
        command = _safe_source_command("/srv/projects/store front")
        self.assertIn("'/srv/projects/store front'", command)
        self.assertIn("status --porcelain=v1", command)

    def test_source_command_rejects_parent_traversal(self):
        with self.assertRaises(HTTPException):
            _safe_source_command("/srv/projects/../secret")

    @patch("app.integration.subprocess.run")
    def test_read_source_snapshot_parses_dirty_repository(self, run: Mock):
        run.return_value = Mock(
            returncode=0,
            stdout="true\n322dbc1d700ab602ed76c89000352374199c8298\nmain\n M app.py\n?? test.txt\n",
            stderr="",
        )
        with patch.dict(os.environ, {"TASKHUB_PROJECT_SOURCE_HOSTS": "192.168.31.17"}):
            result = read_source_snapshot("192.168.31.17", "/srv/project")
        self.assertTrue(result["dirty"])
        self.assertEqual(result["change_count"], 2)
        self.assertEqual(result["branch"], "main")
        self.assertEqual(len(result["status_hash"]), 64)

    def test_onboarding_accepts_two_isolated_lines(self):
        request = OnboardRequest(
            slug="storefront", name="Storefront", source_host="192.168.31.17", source_root="/srv/storefront",
            workspaces=[
                WorkspaceTemplate(name="A", worker_id="worker-a", workspace_id="storefront-a", workspace_path="/srv/a", branch="taskhub/a"),
                WorkspaceTemplate(name="B", worker_id="worker-b", workspace_id="storefront-b", workspace_path="/srv/b", branch="taskhub/b"),
            ],
        )
        self.assertEqual({item.worker_id for item in request.workspaces}, {"worker-a", "worker-b"})
        self.assertNotEqual(request.workspaces[0].branch, request.workspaces[1].branch)


if __name__ == "__main__":
    unittest.main()
