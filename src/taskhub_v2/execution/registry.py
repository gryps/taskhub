import json
from pathlib import Path

from taskhub_v2.domain.models import NodeDefinition


class NodeRegistry:
    def __init__(self, path: str):
        self.path = Path(path)

    def list(self) -> list[NodeDefinition]:
        if not self.path.is_file():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        nodes = [NodeDefinition.model_validate(item) for item in payload.get("nodes", [])]
        ids = [node.id for node in nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node IDs must be unique")
        return nodes
