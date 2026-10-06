import json
import os
from pathlib import Path
from threading import RLock

from taskhub_v2.domain.models import NodeDefinition


class NodeRegistry:
    def __init__(self, path: str):
        self.path = Path(path)
        self.lock = RLock()

    def list(self) -> list[NodeDefinition]:
        if not self.path.is_file():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        nodes = [NodeDefinition.model_validate(item) for item in payload.get("nodes", [])]
        ids = [node.id for node in nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node IDs must be unique")
        return nodes

    def upsert(self, node: NodeDefinition) -> None:
        with self.lock:
            self._write([item for item in self.list() if item.id != node.id] + [node])

    def remove(self, node_id: str) -> None:
        with self.lock:
            self._write([item for item in self.list() if item.id != node_id])

    def _write(self, nodes: list[NodeDefinition]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"nodes": [item.model_dump(mode="json") for item in nodes]},
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
