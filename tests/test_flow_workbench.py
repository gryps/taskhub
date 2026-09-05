import unittest
from pathlib import Path


class FlowWorkbenchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text()

    def test_flow_workbench_is_the_default_entry(self) -> None:
        self.assertIn('data-view="flowdesk" class="active">流程工作台</button>', self.html)
        self.assertIn('<div id="flowdesk">', self.html)

    def test_flow_workbench_shows_every_business_stage(self) -> None:
        for label in ("规划编排", "计划审批", "开发实施", "审查验收", "风险报告", "监督裁决", "流程完成"):
            self.assertIn(f'label: "{label}"', self.html)

    def test_flow_workbench_exposes_human_actions(self) -> None:
        for action in ("处理计划审批", "批准完成", "要求返工", "恢复处理"):
            self.assertIn(action, self.html)
        self.assertIn("/first-workflow", self.html)

    def test_plan_approval_stays_in_the_flow_workbench(self) -> None:
        self.assertIn("data-flow-review-task=", self.html)
        self.assertIn("批准执行", self.html)
        self.assertIn("取消本流程", self.html)
        self.assertIn("计划内容确认无误，同意进入开发实施。", self.html)
        self.assertNotIn('data-flow-open-task="${escapeHtml(approvalTask.id)}"', self.html)

    def test_gui_plan_requires_url_host_and_login_confirmation(self) -> None:
        self.assertIn('data-flow-h5-url=', self.html)
        self.assertIn('data-flow-h5-host=', self.html)
        self.assertIn('data-flow-h5-login=', self.html)
        self.assertIn('approval_plan: editedPlan', self.html)

    def test_blocked_gui_task_can_be_completed_and_retried_inline(self) -> None:
        self.assertIn('id="flowRetryGuiUrl"', self.html)
        self.assertIn('id="flowRetryGuiHost"', self.html)
        self.assertIn('id="flowRetryGuiLogin"', self.html)
        self.assertIn("submitFlowTaskRetry", self.html)
        self.assertIn("恢复失败任务", self.html)

    def test_plan_only_implementation_can_be_backfilled_inline(self) -> None:
        self.assertIn("补齐剩余施工", self.html)
        self.assertIn("/backfill-implementation", self.html)
        self.assertIn("submitFlowBackfill", self.html)

    def test_failed_quality_gate_can_be_sent_to_automatic_rework(self) -> None:
        self.assertIn("由 TaskHub 自动返工", self.html)
        self.assertIn("/auto-recover", self.html)
        self.assertIn("submitFlowAutoRecover", self.html)


if __name__ == "__main__":
    unittest.main()
