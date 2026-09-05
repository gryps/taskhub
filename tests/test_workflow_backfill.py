import unittest

from app.taskhub import backfill_requirement, remaining_plan_only_code_tasks


class WorkflowBackfillTests(unittest.TestCase):
    def test_only_plan_only_tasks_without_real_success_remain(self) -> None:
        tasks = [
            {"id": "plan-1", "type": "code.change", "title": "实现配对", "state": "succeeded", "result": {"mode": "plan_only"}},
            {"id": "plan-2", "type": "code.change", "title": "实现租约", "state": "succeeded", "result": {"mode": "plan_only"}},
            {"id": "real-1", "type": "code.change", "title": "真实施工：实现配对", "state": "succeeded", "result": {"mode": "execute"}},
            {"id": "fix", "type": "code.change", "title": "真实施工：修复测试", "state": "succeeded", "result": {"mode": "execute"}},
        ]

        remaining = remaining_plan_only_code_tasks(tasks)

        self.assertEqual([task["id"] for task in remaining], ["plan-2"])

    def test_explicit_plan_task_link_marks_task_complete(self) -> None:
        tasks = [
            {"id": "plan-1", "type": "code.change", "title": "旧标题", "state": "succeeded", "result": {"mode": "plan_only"}},
            {
                "id": "real-1",
                "type": "code.change",
                "title": "补齐施工：新标题",
                "state": "succeeded",
                "result": {"mode": "execute"},
                "metadata": {"backfill_plan_task_id": "plan-1"},
            },
        ]

        self.assertEqual(remaining_plan_only_code_tasks(tasks), [])

    def test_requirement_preserves_original_scope_and_safety_boundary(self) -> None:
        value = backfill_requirement(
            {"context": {"requirement": "完成 EXE-002"}},
            {"title": "实现租约"},
        )

        self.assertIn("实现租约", value)
        self.assertIn("完成 EXE-002", value)
        self.assertIn("不得访问生产系统", value)
        self.assertIn("不得读取或上传凭据", value)


if __name__ == "__main__":
    unittest.main()
