import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.operations import escalate_alerts, operations_policy


class OperationsPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 5, tzinfo=timezone.utc)
        self.alert = {
            "fingerprint": "a" * 64, "code": "TASK_FAILED", "severity": "warning",
            "title": "failed", "message": "task", "entity_type": "task", "entity_id": "one",
            "detected_at": self.now - timedelta(hours=2), "acknowledged": False, "detail": {},
        }

    def test_old_alert_gets_distinct_escalation_fingerprint(self):
        result = escalate_alerts([self.alert], {
            "warning_escalation_seconds": 1800, "critical_escalation_seconds": 3600,
        }, self.now)[0]
        self.assertEqual(result["escalation_level"], 2)
        self.assertEqual(result["severity"], "critical")
        self.assertNotEqual(result["fingerprint"], self.alert["fingerprint"])
        self.assertEqual(result["source_fingerprint"], self.alert["fingerprint"])

    def test_acknowledged_alert_does_not_escalate(self):
        alert = {**self.alert, "acknowledged": True}
        self.assertEqual(escalate_alerts([alert], {
            "warning_escalation_seconds": 60, "critical_escalation_seconds": 120,
        }, self.now)[0]["escalation_level"], 0)

    def test_policy_defaults_to_disabled_dry_run(self):
        with patch.dict(os.environ, {}, clear=True):
            policy = operations_policy()
        self.assertFalse(policy["enabled"])
        self.assertEqual(policy["mode"], "dry_run")


if __name__ == "__main__":
    unittest.main()
