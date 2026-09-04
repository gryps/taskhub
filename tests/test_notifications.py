import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from app.notifications import (
    deliver_due_alerts,
    mask_webhook_url,
    notification_config,
    run_notification_cycle,
    save_notification_config,
    send_webhook,
    webhook_payload,
    NotificationConfigRequest,
)


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.executions.append((query, params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows=None):
        self.cursor_instance = FakeCursor(rows)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.alert = {
            "fingerprint": "a" * 64,
            "code": "TASK_BLOCKED",
            "severity": "critical",
            "category": "task",
            "title": "任务已阻塞",
            "message": "checkout",
            "entity_type": "task",
            "entity_id": "task-1",
            "detected_at": "2026-09-05T00:00:00Z",
            "detail": {},
        }

    def test_masks_webhook_secret_and_query(self):
        masked = mask_webhook_url("https://example.test/hooks/secret-token?access_token=hidden")
        self.assertEqual(masked, "https://example.test/hooks/********")

    def test_channel_payloads(self):
        self.assertEqual(webhook_payload("wecom", self.alert)["msgtype"], "markdown")
        self.assertEqual(webhook_payload("dingtalk", self.alert)["markdown"]["title"], "任务已阻塞")
        generic = webhook_payload("generic", self.alert)
        self.assertEqual(generic["source"], "gryps-taskhub")
        self.assertEqual(generic["alert"]["code"], "TASK_BLOCKED")

    def test_disabled_cycle_does_not_access_database(self):
        with patch.dict(os.environ, {"TASKHUB_NOTIFICATION_ENABLED": "false"}), patch(
            "app.notifications.queue_active_alerts"
        ) as queue:
            result = run_notification_cycle()
        self.assertFalse(result["ready"])
        queue.assert_not_called()

    def test_send_webhook_ignores_non_json_generic_response(self):
        response = MagicMock()
        response.status_code = 204
        response.content = b""
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        with patch("app.notifications.httpx.Client", return_value=client) as client_class:
            self.assertEqual(send_webhook("generic", "https://example.test/hook", self.alert), 204)
        client_class.assert_called_once_with(timeout=10, trust_env=False)
        response.raise_for_status.assert_called_once()

    def test_failed_delivery_is_persisted_for_retry(self):
        delivery = {
            "id": "delivery-1",
            "alert": self.alert,
        }
        select_connection = FakeConnection([delivery])
        update_connection = FakeConnection()
        config = {
            "channel": "generic",
            "max_attempts": 3,
            "retry_seconds": 120,
        }
        with patch.dict(os.environ, {"TASKHUB_NOTIFICATION_WEBHOOK_URL": "https://example.test/hook"}), patch(
            "app.notifications.connect", side_effect=[select_connection, update_connection]
        ), patch("app.notifications.send_webhook", side_effect=httpx.ConnectError("offline")):
            result = deliver_due_alerts(config)
        self.assertEqual(result, {"selected": 1, "delivered": 0, "failed": 1})
        update_params = update_connection.cursor_instance.executions[0][1]
        self.assertEqual(update_params[0], "failed")
        self.assertIn("ConnectError", update_params[1])
        self.assertTrue(update_connection.committed)

    def test_save_config_preserves_masked_url(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("TASKHUB_NOTIFICATION_WEBHOOK_URL=https://example.test/hooks/original\n", encoding="utf-8")
            with patch.dict(os.environ, {"LANGGRAPH_ENV_FILE": str(env_file)}, clear=False), patch(
                "app.notifications.schedule_control_restart"
            ) as restart:
                result = save_notification_config(NotificationConfigRequest(
                    enabled=True,
                    channel="wecom",
                    webhook_url="https://example.test/hooks/********",
                    minimum_severity="warning",
                    poll_seconds=60,
                    retry_seconds=180,
                    max_attempts=4,
                    restart=False,
                ))
            restart.assert_not_called()
            self.assertTrue(result["config"]["ready"])
            self.assertEqual(result["config"]["channel"], "wecom")
            self.assertIn("TASKHUB_NOTIFICATION_WEBHOOK_URL=https://example.test/hooks/original", env_file.read_text())


if __name__ == "__main__":
    unittest.main()
