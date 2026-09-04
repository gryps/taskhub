import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.monitoring import alert_fingerprint, collect_database_alerts, env_count, env_seconds


class FakeCursor:
    def __init__(self, result_sets):
        self.result_sets = iter(result_sets)
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _query, _params=None):
        self.rows = next(self.result_sets)

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, result_sets):
        self.result_sets = result_sets

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return FakeCursor(self.result_sets)


class MonitoringTests(unittest.TestCase):
    def test_fingerprint_is_stable_and_occurrence_sensitive(self):
        first = alert_fingerprint("TASK_FAILED", "task", "123", "one")
        self.assertEqual(first, alert_fingerprint("TASK_FAILED", "task", "123", "one"))
        self.assertNotEqual(first, alert_fingerprint("TASK_FAILED", "task", "123", "two"))

    def test_invalid_environment_values_use_defaults(self):
        with patch.dict(os.environ, {"TEST_SECONDS": "bad", "TEST_COUNT": "bad"}):
            self.assertEqual(env_seconds("TEST_SECONDS", 300), 300)
            self.assertEqual(env_count("TEST_COUNT", 4), 4)

    def test_collects_signal_loss_retry_and_role_exhaustion(self):
        now = datetime(2026, 9, 5, tzinfo=timezone.utc)
        old = now - timedelta(hours=1)
        task = {
            "id": "task-1", "title": "implement checkout", "state": "running",
            "worker_id": "worker-a", "target_worker_id": "worker-a", "retry_count": 3,
            "created_at": old, "updated_at": old, "claimed_at": old,
            "heartbeat_at": old, "lease_expires_at": old,
        }
        workflow = {
            "id": "workflow-1", "project": "shop", "state": "implementation",
            "summary": "checkout", "active_role": "coder", "updated_at": old,
        }
        role_run = {
            "id": "run-1", "workflow_id": "workflow-1", "state": "review", "role": "reviewer",
            "status": "failed", "attempt_count": 3, "provider": "deepseek_api", "model": "deepseek",
            "error": {"reason": "quota_exceeded"}, "started_at": old,
            "completed_at": old, "next_retry_at": None, "output": {},
        }
        connection = FakeConnection([[task], [workflow], [role_run], []])
        with patch("app.monitoring.connect", return_value=connection), patch("app.monitoring.provider_health", return_value=[]):
            alerts = collect_database_alerts(now)
        codes = {item["code"] for item in alerts}
        self.assertIn("TASK_SIGNAL_LOST", codes)
        self.assertIn("TASK_RETRY_LIMIT", codes)
        self.assertIn("WORKFLOW_STALE", codes)
        self.assertIn("ROLE_RETRY_EXHAUSTED", codes)
        self.assertTrue(all(not item["acknowledged"] for item in alerts))

    def test_acknowledgement_is_merged_into_active_alert(self):
        now = datetime(2026, 9, 5, tzinfo=timezone.utc)
        old = now - timedelta(hours=1)
        task = {
            "id": "task-2", "title": "blocked", "state": "blocked", "worker_id": None,
            "target_worker_id": None, "retry_count": 0, "created_at": old, "updated_at": old,
            "claimed_at": None, "heartbeat_at": None, "lease_expires_at": None,
        }
        fingerprint = alert_fingerprint("TASK_BLOCKED", "task", "task-2", old)
        acknowledgement = {"fingerprint": fingerprint, "acknowledged_by": "admin", "acknowledged_at": now}
        connection = FakeConnection([[task], [], [], [acknowledgement]])
        with patch("app.monitoring.connect", return_value=connection), patch("app.monitoring.provider_health", return_value=[]):
            alerts = collect_database_alerts(now)
        self.assertEqual(len(alerts), 1)
        self.assertTrue(alerts[0]["acknowledged"])
        self.assertEqual(alerts[0]["acknowledged_by"], "admin")

    def test_successful_slow_fallback_is_visible(self):
        now = datetime(2026, 9, 5, tzinfo=timezone.utc)
        run = {
            "id": "run-2", "workflow_id": "workflow-2", "state": "risk", "role": "risk",
            "status": "succeeded", "attempt_count": 1, "provider": "minimax_api", "model": "minimax",
            "error": {}, "started_at": now - timedelta(minutes=5), "completed_at": now,
            "next_retry_at": None,
            "output": {"attempts": [{"provider": "gpt_api", "reason": "quota_exceeded"}, {"provider": "minimax_api"}]},
        }
        connection = FakeConnection([[], [], [run], []])
        with patch("app.monitoring.connect", return_value=connection), patch("app.monitoring.provider_health", return_value=[]):
            alerts = collect_database_alerts(now)
        self.assertEqual(
            {item["code"] for item in alerts},
            {"ROLE_FALLBACK_USED", "ROLE_RUN_SLOW", "PROVIDER_QUOTA_EVENT"},
        )


if __name__ == "__main__":
    unittest.main()
