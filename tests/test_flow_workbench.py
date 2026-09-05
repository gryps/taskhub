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


if __name__ == "__main__":
    unittest.main()
