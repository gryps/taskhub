import { describe, expect, it } from "vitest";
import type { Edge, Node } from "@xyflow/react";

import {
  EDGE_LABELS,
  flowEdges,
  inferEdge,
  toCanvasEdges,
  toCanvasNodes,
} from "./TopologyApp";
import type { TopologyNodeData } from "./TopologyNode";
import type { Topology } from "./types";

const topology: Topology = {
  topology_id: "topology-1",
  project_id: "project-1",
  version: 1,
  status: "draft",
  nodes: [
    {
      node_id: "execution:1",
      node_type: "execution",
      label: "执行节点",
      resource_id: "worker-1",
      member_ids: [],
      required_capabilities: ["coding", "git"],
      position: { x: 10, y: 20 },
    },
  ],
  edges: [
    {
      edge_id: "edge:1",
      source: "project:1",
      target: "execution:1",
      edge_type: "executes_on",
      priority: 20,
    },
  ],
  viewport: { x: 0, y: 0, zoom: 1 },
  findings: [],
  content_digest: "digest-1",
};

describe("production topology transformations", () => {
  it("infers typed edges from stable node identities", () => {
    expect(inferEdge("project:1", "controller:1")).toBe("governed_by");
    expect(inferEdge("controller:1", "test:1")).toBe("verified_by");
    expect(inferEdge("controller:1", "preproduction:1")).toBe("accepted_by");
    expect(inferEdge("controller:1", "execution:1")).toBe("executes_on");
  });

  it("maps persisted edges to readable flow edges", () => {
    expect(flowEdges(topology)).toEqual([
      expect.objectContaining({
        id: "edge:1",
        label: EDGE_LABELS.executes_on,
        data: { kind: "executes_on" },
      }),
    ]);
  });

  it("preserves server-owned node capabilities while saving layout changes", () => {
    const nodes: Node<TopologyNodeData>[] = [
      {
        id: "execution:1",
        type: "topology",
        position: { x: 120, y: 240 },
        data: {
          label: "主要执行节点",
          kind: "execution",
          resourceId: "worker-1",
          memberIds: [],
        },
      },
    ];

    expect(toCanvasNodes(nodes, topology)).toEqual([
      expect.objectContaining({
        node_id: "execution:1",
        label: "主要执行节点",
        required_capabilities: ["coding", "git"],
        position: { x: 120, y: 240 },
      }),
    ]);
  });

  it("preserves server-owned edge priority when the client edits geometry", () => {
    const edges: Edge[] = [
      {
        id: "edge:1",
        source: "project:1",
        target: "execution:1",
      },
    ];

    expect(toCanvasEdges(edges, topology)).toEqual([
      {
        edge_id: "edge:1",
        source: "project:1",
        target: "execution:1",
        edge_type: "executes_on",
        priority: 20,
      },
    ]);
  });
});
