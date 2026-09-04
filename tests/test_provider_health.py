import os
import unittest
from unittest.mock import patch

from app.provider_health import before_attempt, provider_health, record_failure, record_success, reset_provider_health


class ProviderHealthTests(unittest.TestCase):
    def tearDown(self):
        reset_provider_health()

    def test_recovery_requires_consecutive_successes(self):
        with patch.dict(os.environ, {"PROVIDER_COOLDOWN_SECONDS": "10", "PROVIDER_RECOVERY_SUCCESSES": "2"}):
            record_failure("plus", "quota_exceeded", now=100)
            allowed, event = before_attempt("plus", now=111)
            self.assertTrue(allowed)
            self.assertEqual(event["event"], "recovery_probe")
            first = record_success("plus", now=112)
            self.assertEqual(first["status"], "recovering")
            allowed, _ = before_attempt("plus", now=113)
            self.assertTrue(allowed)
            second = record_success("plus", now=114)
            self.assertEqual(second["event"], "recovered_to_preferred")
            self.assertEqual(provider_health()[0]["status"], "healthy")

    def test_repeated_probe_failure_uses_exponential_backoff(self):
        with patch.dict(os.environ, {"PROVIDER_COOLDOWN_SECONDS": "10"}):
            first = record_failure("pro", "timeout", now=100)
            before_attempt("pro", now=111)
            second = record_failure("pro", "timeout", now=112)
        self.assertTrue(first["retry_at"].endswith("00:01:50+00:00"))
        self.assertTrue(second["retry_at"].endswith("00:02:12+00:00"))


if __name__ == "__main__":
    unittest.main()
