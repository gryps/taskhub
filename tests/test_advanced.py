import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app.advanced import decide_governance, quality_score, stable_context_pack_hash, validate_project_location
from app.taskhub import least_loaded_worker, worker_pool


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.params = None

    def execute(self, _sql, params):
        self.params = params

    def fetchone(self):
        return self.row


class AdvancedStageTests(unittest.TestCase):
    def test_project_authority_requires_allowlisted_host(self):
        with patch.dict(os.environ, {"TASKHUB_PROJECT_SOURCE_HOSTS": "192.168.31.17"}):
            self.assertEqual(
                validate_project_location("demo-project", "192.168.31.17", "/srv/demo"),
                ("demo-project", "192.168.31.17", "/srv/demo"),
            )
            with self.assertRaises(HTTPException):
                validate_project_location("demo-project", "example.com", "/srv/demo")

    def test_project_authority_rejects_relative_or_parent_path(self):
        with self.assertRaises(HTTPException):
            validate_project_location("demo-project", "192.168.31.17", "srv/demo")
        with self.assertRaises(HTTPException):
            validate_project_location("demo-project", "192.168.31.17", "/srv/../secret")

    def test_dynamic_pool_preserves_configured_preference(self):
        with patch.dict(os.environ, {"TASKHUB_IMPLEMENTATION_WORKERS": "worker-b,worker-a"}):
            self.assertEqual(worker_pool("code.change"), ["worker-b", "worker-a"])
            cursor = FakeCursor({"worker_id": "worker-a"})
            self.assertEqual(least_loaded_worker(cursor, "code.change"), "worker-a")
            self.assertEqual(cursor.params, (["worker-b", "worker-a"],))

    def test_completed_validated_workflow_passes_quality_gate(self):
        score, verdict = quality_score({
            "task_count": 4, "succeeded": 4, "failed_or_blocked": 0,
            "handoff_count": 2, "validated_handoffs": 2, "workflow_state": "completed",
        })
        self.assertEqual((score, verdict), (100, "pass"))

    def test_failed_workflow_cannot_pass_quality_gate(self):
        score, verdict = quality_score({
            "task_count": 4, "succeeded": 3, "failed_or_blocked": 1,
            "handoff_count": 1, "validated_handoffs": 0, "workflow_state": "review",
        })
        self.assertLess(score, 60)
        self.assertEqual(verdict, "fail")

    def test_publish_always_requires_human(self):
        policy = {"autonomy_enabled": True, "allowed_actions": ["publish"], "max_risk": "medium"}
        self.assertEqual(decide_governance(policy, "publish", "low")[0], "human_required")

    def test_allowlisted_low_risk_action_can_run(self):
        policy = {"autonomy_enabled": True, "allowed_actions": ["refresh_context"], "max_risk": "low"}
        self.assertEqual(decide_governance(policy, "refresh_context", "low")[0], "allowed")
        self.assertEqual(decide_governance(policy, "refresh_context", "medium")[0], "human_required")

    def test_context_hash_accepts_database_timestamps(self):
        pack = {"snapshot": {"created_at": datetime(2026, 9, 5, tzinfo=timezone.utc)}}
        self.assertEqual(stable_context_pack_hash(pack), stable_context_pack_hash(pack))
        self.assertEqual(len(stable_context_pack_hash(pack)), 64)


if __name__ == "__main__":
    unittest.main()
