import unittest

from fastapi import HTTPException

from app.contracts import handoff_contract, validate_task_input, validate_task_result
from app.taskhub import TaskCreate, insert_task


class HandoffContractTests(unittest.TestCase):
    def test_code_change_contract_is_strict(self):
        contract = handoff_contract("code.change", {"requirement": "add checkout"}, {})
        self.assertTrue(contract["strict"])
        self.assertEqual(contract["consumer"], "pipeline-worker")

    def test_code_change_requires_execute_mode_and_requirement(self):
        validate_task_input("code.change", {"requirement": "add checkout", "mode": "execute"})
        with self.assertRaises(ValueError):
            validate_task_input("code.change", {"requirement": "add checkout", "mode": "plan_only"})

    def test_code_change_result_requires_workspace_evidence(self):
        result = validate_task_result("code.change", {"will_modify_files": True})
        self.assertFalse(result["valid"])
        self.assertTrue(result["strict"])

    def test_h5_inspection_requires_human_confirmed_url_and_login(self):
        with self.assertRaises(ValueError):
            validate_task_input("h5.inspect", {})
        with self.assertRaises(ValueError):
            validate_task_input(
                "h5.inspect",
                {"url": "https://example.com", "allowed_host_confirmed": True},
            )
        validate_task_input(
            "h5.inspect",
            {
                "url": "https://example.com/path",
                "allowed_host_confirmed": True,
                "login_environment_confirmed": True,
            },
        )

    def test_h5_inspection_rejects_non_http_url(self):
        with self.assertRaises(ValueError):
            validate_task_input(
                "h5.inspect",
                {
                    "url": "file:///tmp/page.html",
                    "allowed_host_confirmed": True,
                    "login_environment_confirmed": True,
                },
            )

    def test_out_of_scope_business_tasks_are_rejected(self):
        for task_type in ("market.price.collect", "commerce.listing.draft"):
            with self.subTest(task_type=task_type), self.assertRaises(HTTPException) as raised:
                insert_task(None, TaskCreate(type=task_type, title="out of scope"), "test", "test")
            self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
