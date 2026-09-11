export type NodeKind =
  | "project"
  | "controller"
  | "execution"
  | "test"
  | "preproduction"
  | "resource_pool";
export type EdgeKind =
  | "governed_by"
  | "executes_on"
  | "verified_by"
  | "accepted_by"
  | "fallback_to";

export interface CanvasNode {
  node_id: string;
  node_type: NodeKind;
  label: string;
  resource_id: string;
  member_ids: string[];
  required_capabilities: string[];
  position: { x: number; y: number };
}

export interface CanvasEdge {
  edge_id: string;
  source: string;
  target: string;
  edge_type: EdgeKind;
  priority: number;
}

export interface Topology {
  topology_id: string;
  project_id: string;
  version: number;
  status: "draft" | "validating" | "invalid" | "active" | "superseded";
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  viewport: { x: number; y: number; zoom: number };
  findings: {
    code: string;
    message: string;
    level: string;
    subject_id: string;
  }[];
  content_digest: string;
}

export interface Project {
  id: string;
  name: string;
}

export interface Runtime {
  topology: Topology | null;
  run: null | {
    run_id: string;
    requirement: string;
    stage: string;
    status: string;
    product_spec_id?: string;
    product_spec_version?: number;
  };
  execution: null | {
    execution_plan: null | { plan_id: string; version: number };
    batches: { batch_id: string; status: string }[];
    tasks: {
      task_id: string;
      title: string;
      status: string;
      assigned_node_id: string;
    }[];
  };
  nodes: {
    node_id: string;
    status: string;
    slots: number;
    active: number;
    workloads: string[];
  }[];
}
