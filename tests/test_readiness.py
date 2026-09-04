import unittest

from app.readiness import assess_workers, simulate_dual_pipeline


class PipelineReadinessTests(unittest.TestCase):
    def test_two_workers_keep_pipeline_isolation_under_load(self):
        result = simulate_dual_pipeline(1000)
        self.assertTrue(result["passed"])
        self.assertEqual(result["completed"], 1000)
        self.assertEqual(result["pipeline_isolation_violations"], 0)

    def test_worker_loss_stops_its_pipeline_without_cross_claim(self):
        result = simulate_dual_pipeline(1000, fail_after=20)
        self.assertTrue(result["passed"])
        self.assertGreater(result["remaining"], 0)
        self.assertEqual(result["pipeline_isolation_violations"], 0)

    def test_capability_readiness_is_explicit(self):
        workers = [
            {"status": "ok", "worker_id": "worker-31-31-implementation-a", "task_types": "code.change"},
            {"status": "ok", "worker_id": "worker-31-31-implementation", "task_types": "code.change"},
            {"status": "ok", "worker_id": "worker-31-24-quality", "task_types": "test.run"},
            {"status": "ok", "worker_id": "worker-31-34-gui", "task_types": "h5.inspect,market.price.collect"},
        ]
        result = assess_workers(workers)
        self.assertTrue(result["dual_pipeline_ready"])
        self.assertTrue(result["market_collection_ready"])
        self.assertFalse(result["listing_draft_ready"])


if __name__ == "__main__":
    unittest.main()
