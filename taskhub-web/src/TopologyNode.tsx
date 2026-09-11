import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";

export type TopologyNodeData = {
  label: string;
  kind: string;
  resourceId: string;
  memberIds: string[];
  runtime?: { status: string; slots: number; active: number };
  assignedTasks?: number;
};
export type FlowTopologyNode = Node<TopologyNodeData, "topology">;

const KIND_LABELS: Record<string, string> = {
  project: "项目",
  controller: "控制节点",
  execution: "执行节点",
  test: "测试节点",
  preproduction: "预生产",
  resource_pool: "资源池",
};

export function TopologyNodeCard({
  data,
  selected,
}: NodeProps<FlowTopologyNode>) {
  const load = data.runtime
    ? `${data.runtime.active}/${data.runtime.slots}`
    : "—";
  return (
    <article
      className={`topology-node kind-${data.kind} ${selected ? "selected" : ""}`}
    >
      <Handle type="target" position={Position.Left} />
      <header>
        <span>{KIND_LABELS[data.kind] || data.kind}</span>
        <i className={`health ${data.runtime?.status || "unknown"}`} />
      </header>
      <strong>{data.label}</strong>
      <small>
        {data.resourceId || data.memberIds.join(", ") || "尚未绑定资源"}
      </small>
      {data.runtime && (
        <footer>
          <span>槽位 {load}</span>
          <span>任务 {data.assignedTasks || 0}</span>
          <span>{data.runtime.status === "ok" ? "在线" : "异常"}</span>
        </footer>
      )}
      <Handle type="source" position={Position.Right} />
    </article>
  );
}
