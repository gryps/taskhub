from __future__ import annotations

import json

from taskhub_v2.domain.models import NodeDefinition


class NodeInventoryMixin:
    def _registered_nodes(self) -> list[NodeDefinition]:
        if not self.nodes_file.is_file():
            return []
        payload = json.loads(self.nodes_file.read_text(encoding="utf-8"))
        return [NodeDefinition.model_validate(item) for item in payload.get("nodes", [])]

    def _write_nodes(self, nodes: list[NodeDefinition]):
        self.nodes_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.nodes_file.with_suffix(".tmp")
        payload = {
            "nodes": [
                {**node.model_dump(mode="json"), "workloads": sorted(node.workloads)}
                for node in nodes
            ]
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.nodes_file)

    def _register(self, node: NodeDefinition):
        nodes = [item for item in self._registered_nodes() if item.id != node.id]
        nodes.append(node)
        self._write_nodes(nodes)

    def _unregister(self, node_id: str):
        self._write_nodes([node for node in self._registered_nodes() if node.id != node_id])

    def register_node(self, node: NodeDefinition) -> None:
        with self.lock:
            self._register(node)

    def unregister_node(self, node_id: str) -> None:
        with self.lock:
            self._unregister(node_id)

    def node_exists(self, node_id: str) -> bool:
        with self.lock:
            return any(node.id == node_id for node in self._registered_nodes())
