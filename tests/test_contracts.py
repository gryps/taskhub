import unittest

from app.contracts import handoff_contract, validate_task_input, validate_task_result


class HandoffContractTests(unittest.TestCase):
    def test_market_contract_is_strict(self):
        contract = handoff_contract("market.price.collect", {"keyword": "发簪", "count": 10}, {})
        self.assertTrue(contract["strict"])
        self.assertEqual(contract["consumer"], "worker-31-34-gui")

    def test_market_input_limits_count(self):
        validate_task_input("market.price.collect", {"keyword": "发簪", "count": 100})
        with self.assertRaises(ValueError):
            validate_task_input("market.price.collect", {"keyword": "发簪", "count": 101})

    def test_strict_result_rejects_missing_artifacts(self):
        result = validate_task_result("market.price.collect", {"status": "collected", "task_id": "one"})
        self.assertFalse(result["valid"])
        self.assertIn("missing result field: artifacts", result["errors"])

    def test_listing_only_accepts_non_publish_outcomes(self):
        accepted = validate_task_result("commerce.listing.draft", {"status": "draft_saved", "artifacts": []})
        rejected = validate_task_result("commerce.listing.draft", {"status": "published", "artifacts": []})
        self.assertTrue(accepted["valid"])
        self.assertFalse(rejected["valid"])


if __name__ == "__main__":
    unittest.main()
