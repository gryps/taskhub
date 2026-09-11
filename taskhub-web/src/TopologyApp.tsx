import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
} from "@xyflow/react";
import {
  createDraft,
  getProjects,
  getRuntime,
  getTopologies,
  saveDraft,
  submitRequirement,
  transition,
} from "./api";
import { TopologyNodeCard, type TopologyNodeData } from "./TopologyNode";
import type {
  CanvasEdge,
  CanvasNode,
  EdgeKind,
  NodeKind,
  Topology,
} from "./types";

const nodeTypes = { topology: TopologyNodeCard };
const KINDS: { value: NodeKind; label: string }[] = [
  { value: "execution", label: "执行节点" },
  { value: "test", label: "测试节点" },
  { value: "preproduction", label: "预生产节点" },
  { value: "resource_pool", label: "资源池" },
];
const EDGE_LABELS: Record<EdgeKind, string> = {
  governed_by: "控制",
  executes_on: "执行",
  verified_by: "验证",
  accepted_by: "验收",
  fallback_to: "故障转移",
};

function flowNodes(
  topology: Topology | null,
  runtime: Awaited<ReturnType<typeof getRuntime>> | undefined,
): Node<TopologyNodeData>[] {
  const health = new Map(
    (runtime?.nodes || []).map((item) => [item.node_id, item]),
  );
  const assignments = new Map<string, number>();
  for (const task of runtime?.execution?.tasks || []) {
    if (task.assigned_node_id) {
      assignments.set(
        task.assigned_node_id,
        (assignments.get(task.assigned_node_id) || 0) + 1,
      );
    }
  }
  return (topology?.nodes || []).map((item) => ({
    id: item.node_id,
    type: "topology",
    position: item.position,
    data: {
      label: item.label,
      kind: item.node_type,
      resourceId: item.resource_id,
      memberIds: item.member_ids,
      runtime: health.get(item.resource_id),
      assignedTasks: assignments.get(item.resource_id) || 0,
    },
  }));
}
function flowEdges(topology: Topology | null): Edge[] {
  return (topology?.edges || []).map((item) => ({
    id: item.edge_id,
    source: item.source,
    target: item.target,
    label: EDGE_LABELS[item.edge_type],
    data: { kind: item.edge_type },
  }));
}
function inferEdge(source?: string, target?: string): EdgeKind {
  if (source?.startsWith("project:") && target?.startsWith("controller:"))
    return "governed_by";
  return target?.includes("test")
    ? "verified_by"
    : target?.includes("preproduction")
      ? "accepted_by"
      : "executes_on";
}

export function TopologyApp() {
  const queryClient = useQueryClient();
  const [projectId, setProjectId] = useState("");
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [message, setMessage] = useState("请选择项目");
  const [dirty, setDirty] = useState(false);
  const [tab, setTab] = useState<"canvas" | "list">("canvas");
  const [requirement, setRequirement] = useState("");
  const [menu, setMenu] = useState<{
    x: number;
    y: number;
    nodeId?: string;
  } | null>(null);
  const [viewport, setViewport] = useState({ x: 0, y: 0, zoom: 1 });
  const [nodes, setNodes, onNodesChange] = useNodesState<
    Node<TopologyNodeData>
  >([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const history = useRef<{ nodes: Node<TopologyNodeData>[]; edges: Edge[] }[]>(
    [],
  );
  const future = useRef<typeof history.current>([]);
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const topologies = useQuery({
    queryKey: ["topologies", projectId],
    queryFn: () => getTopologies(projectId),
    enabled: Boolean(projectId),
  });
  const runtime = useQuery({
    queryKey: ["runtime", projectId],
    queryFn: () => getRuntime(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 10_000,
  });
  const topology = useMemo(() => {
    const items = topologies.data?.topologies || [];
    return (
      items.find((item) => item.version === selectedVersion) || items[0] || null
    );
  }, [topologies.data, selectedVersion]);

  useEffect(() => {
    if (!projectId && projects.data?.projects[0])
      setProjectId(projects.data.projects[0].id);
  }, [projectId, projects.data]);
  useEffect(() => {
    setNodes(flowNodes(topology, runtime.data));
    setEdges(flowEdges(topology));
    setViewport(topology?.viewport || { x: 0, y: 0, zoom: 1 });
    setDirty(false);
    history.current = [];
    future.current = [];
  }, [topology?.content_digest, setNodes, setEdges]);
  useEffect(() => {
    const health = new Map(
      (runtime.data?.nodes || []).map((item) => [item.node_id, item]),
    );
    const assignments = new Map<string, number>();
    for (const task of runtime.data?.execution?.tasks || []) {
      if (task.assigned_node_id) {
        assignments.set(
          task.assigned_node_id,
          (assignments.get(task.assigned_node_id) || 0) + 1,
        );
      }
    }
    setNodes((items) =>
      items.map((item) => ({
        ...item,
        data: {
          ...item.data,
          runtime: health.get(item.data.resourceId),
          assignedTasks: assignments.get(item.data.resourceId) || 0,
        },
      })),
    );
  }, [runtime.data?.nodes, runtime.data?.execution?.tasks, setNodes]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) event.preventDefault();
    };
    addEventListener("beforeunload", warn);
    return () => removeEventListener("beforeunload", warn);
  }, [dirty]);

  const refresh = async () => {
    await queryClient.invalidateQueries({
      queryKey: ["topologies", projectId],
    });
    await queryClient.invalidateQueries({ queryKey: ["runtime", projectId] });
  };
  const mutation = useMutation({
    mutationFn: async (action: "draft" | "save" | "validate" | "activate") => {
      if (!projectId) throw new Error("请先选择项目");
      if (action === "draft") return createDraft(projectId);
      if (!topology) throw new Error("请先创建草稿版本");
      if (action === "save")
        return saveDraft(
          topology,
          toCanvasNodes(nodes, topology),
          toCanvasEdges(edges, topology),
          viewport,
        );
      return transition(topology, action);
    },
    onSuccess: async (result) => {
      setSelectedVersion(result.version);
      setMessage("操作已完成");
      await refresh();
    },
    onError: (error) => setMessage(error.message),
  });

  const remember = () => {
    history.current.push({
      nodes: structuredClone(nodes),
      edges: structuredClone(edges),
    });
    future.current = [];
  };
  const changed = () => setDirty(true);
  const connect = (connection: Connection) => {
    remember();
    const kind = inferEdge(connection.source, connection.target);
    setEdges((items) =>
      addEdge(
        {
          ...connection,
          id: `edge:${crypto.randomUUID()}`,
          label: EDGE_LABELS[kind],
          data: { kind },
        },
        items,
      ),
    );
    changed();
  };
  const addNode = (kind: NodeKind) => {
    remember();
    const id = `${kind}:${crypto.randomUUID().slice(0, 8)}`;
    setNodes((items) => [
      ...items,
      {
        id,
        type: "topology",
        position: { x: 220 + items.length * 35, y: 160 + items.length * 28 },
        data: {
          label: KINDS.find((item) => item.value === kind)?.label || kind,
          kind,
          resourceId: "",
          memberIds: [],
        },
      },
    ]);
    changed();
  };
  const deleteNode = (nodeId: string) => {
    remember();
    setNodes((items) => items.filter((item) => item.id !== nodeId));
    setEdges((items) =>
      items.filter((item) => item.source !== nodeId && item.target !== nodeId),
    );
    setMenu(null);
    changed();
  };
  const undo = () => {
    const state = history.current.pop();
    if (!state) return;
    future.current.push({ nodes, edges });
    setNodes(state.nodes);
    setEdges(state.edges);
    changed();
  };
  const redo = () => {
    const state = future.current.pop();
    if (!state) return;
    history.current.push({ nodes, edges });
    setNodes(state.nodes);
    setEdges(state.edges);
    changed();
  };
  const autoLayout = () => {
    remember();
    setNodes((items) =>
      items.map((item, index) => ({
        ...item,
        position: {
          x: 80 + (index % 3) * 300,
          y: 90 + Math.floor(index / 3) * 190,
        },
      })),
    );
    changed();
  };

  return (
    <main className="canvas-shell">
      <header className="canvas-header">
        <div>
          <a href="/">← TaskHub</a>
          <span className="eyebrow">PRODUCTION TOPOLOGY</span>
          <h1>项目生产画布</h1>
        </div>
        <div className="header-controls">
          <label>
            项目
            <select
              value={projectId}
              onChange={(event) => {
                setProjectId(event.target.value);
                setSelectedVersion(null);
              }}
            >
              {(projects.data?.projects || []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            版本
            <select
              value={topology?.version || ""}
              onChange={(event) =>
                setSelectedVersion(Number(event.target.value))
              }
            >
              {(topologies.data?.topologies || []).map((item) => (
                <option key={item.version} value={item.version}>
                  v{item.version} · {item.status}
                </option>
              ))}
            </select>
          </label>
        </div>
      </header>
      <section className="status-strip">
        <span className={`status ${topology?.status || "empty"}`}>
          {topology ? `v${topology.version} ${topology.status}` : "尚无拓扑"}
        </span>
        <span>{dirty ? "有未保存更改" : "所有更改已保存"}</span>
        <span>
          {runtime.data?.run
            ? `运行 ${runtime.data.run.status} · ${runtime.data.run.stage}`
            : "当前无运行任务"}
        </span>
        <span role="status">{message}</span>
      </section>
      <nav className="toolbar" aria-label="生产画布工具栏">
        <button onClick={() => mutation.mutate("draft")}>新建版本</button>
        {KINDS.map((item) => (
          <button
            key={item.value}
            disabled={topology?.status !== "draft"}
            onClick={() => addNode(item.value)}
          >
            ＋{item.label}
          </button>
        ))}
        <button onClick={undo} disabled={!history.current.length}>
          撤销
        </button>
        <button onClick={redo} disabled={!future.current.length}>
          重做
        </button>
        <button onClick={autoLayout}>自动布局</button>
        <button
          className="primary"
          disabled={!dirty || topology?.status !== "draft"}
          onClick={() => mutation.mutate("save")}
        >
          保存
        </button>
        <button
          disabled={dirty || topology?.status !== "draft"}
          onClick={() => mutation.mutate("validate")}
        >
          校验
        </button>
        <button
          disabled={topology?.status !== "validating"}
          onClick={() => mutation.mutate("activate")}
        >
          激活
        </button>
        <span className="view-tabs">
          <button
            className={tab === "canvas" ? "active" : ""}
            onClick={() => setTab("canvas")}
          >
            画布
          </button>
          <button
            className={tab === "list" ? "active" : ""}
            onClick={() => setTab("list")}
          >
            列表
          </button>
        </span>
      </nav>
      <section className="workspace">
        {tab === "canvas" ? (
          <div
            className="flow-wrap"
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.shiftKey && event.key === "F10")
                setMenu({ x: 24, y: 170 });
            }}
          >
            <ReactFlow
              key={topology?.content_digest || projectId}
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={(changes) => {
                onNodesChange(changes);
                if (changes.some((item) => item.type === "remove")) changed();
              }}
              onEdgesChange={(changes) => {
                onEdgesChange(changes);
                changed();
              }}
              onConnect={connect}
              onNodeDragStart={remember}
              onNodeDragStop={changed}
              defaultViewport={viewport}
              onMoveEnd={(_event, nextViewport) => {
                setViewport(nextViewport);
                if (_event) changed();
              }}
              onPaneClick={() => setMenu(null)}
              onPaneContextMenu={(event) => {
                event.preventDefault();
                setMenu({ x: event.clientX, y: event.clientY });
              }}
              onNodeContextMenu={(event, node) => {
                event.preventDefault();
                setMenu({
                  x: event.clientX,
                  y: event.clientY,
                  nodeId: node.id,
                });
              }}
              deleteKeyCode={["Backspace", "Delete"]}
              selectionOnDrag
              panOnScroll
            >
              <Background gap={22} />
              <Controls />
              <MiniMap pannable zoomable />
            </ReactFlow>
            {menu && (
              <div
                className="context-menu"
                role="menu"
                style={{ left: menu.x, top: menu.y }}
              >
                {menu.nodeId ? (
                  <button
                    role="menuitem"
                    onClick={() => deleteNode(menu.nodeId!)}
                  >
                    删除节点
                  </button>
                ) : (
                  KINDS.map((item) => (
                    <button
                      role="menuitem"
                      key={item.value}
                      onClick={() => {
                        addNode(item.value);
                        setMenu(null);
                      }}
                    >
                      添加{item.label}
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
        ) : (
          <TopologyList
            nodes={nodes}
            edges={edges}
            resources={runtime.data?.nodes || []}
            setNodes={setNodes}
            setEdges={setEdges}
            changed={changed}
          />
        )}
        <aside>
          <h2>运行叠加层</h2>
          <p className="muted">
            拓扑是长期配置；此处叠加最新交付运行，不改写活动版本。
          </p>
          <dl>
            <div>
              <dt>规格</dt>
              <dd>
                {runtime.data?.run?.product_spec_version
                  ? `v${runtime.data.run.product_spec_version}`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt>执行计划</dt>
              <dd>
                {runtime.data?.execution?.execution_plan
                  ? `v${runtime.data.execution.execution_plan.version}`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt>动态批次</dt>
              <dd>{runtime.data?.execution?.batches.length || 0}</dd>
            </div>
            <div>
              <dt>任务</dt>
              <dd>{runtime.data?.execution?.tasks.length || 0}</dd>
            </div>
            <div>
              <dt>健康节点</dt>
              <dd>
                {runtime.data?.nodes.filter((item) => item.status === "ok")
                  .length || 0}
              </dd>
            </div>
            <div>
              <dt>占用槽位</dt>
              <dd>
                {runtime.data?.nodes.reduce(
                  (sum, item) => sum + item.active,
                  0,
                ) || 0}
              </dd>
            </div>
          </dl>
          <div className="task-counts" aria-label="任务状态计数">
            {[
              "pending",
              "ready",
              "running",
              "verifying",
              "completed",
              "blocked",
            ].map((status) => (
              <span key={status}>
                {status}{" "}
                <strong>
                  {runtime.data?.execution?.tasks.filter(
                    (item) => item.status === status,
                  ).length || 0}
                </strong>
              </span>
            ))}
          </div>
          <h2>项目需求</h2>
          <textarea
            rows={5}
            value={requirement}
            onChange={(event) => setRequirement(event.target.value)}
            placeholder="输入新需求，提交后进入产品规格与审批流程"
          />
          <button
            className="primary wide"
            disabled={requirement.trim().length < 3}
            onClick={async () => {
              try {
                await submitRequirement(projectId, requirement);
                setRequirement("");
                setMessage("需求已提交，请返回开发流程完善产品规格");
              } catch (error) {
                setMessage((error as Error).message);
              }
            }}
          >
            提交需求
          </button>
          {topology?.findings.length ? (
            <>
              <h2>校验结果</h2>
              <ul className="findings">
                {topology.findings.map((item) => (
                  <li key={`${item.code}-${item.subject_id}`}>
                    {item.message}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </aside>
      </section>
    </main>
  );
}

function toCanvasNodes(
  nodes: Node<TopologyNodeData>[],
  source: Topology,
): CanvasNode[] {
  const existing = new Map(source.nodes.map((item) => [item.node_id, item]));
  return nodes.map((item) => ({
    node_id: item.id,
    node_type: item.data.kind as NodeKind,
    label: item.data.label,
    resource_id: item.data.resourceId,
    member_ids: item.data.memberIds,
    required_capabilities: existing.get(item.id)?.required_capabilities || [],
    position: item.position,
  }));
}
function toCanvasEdges(edges: Edge[], source: Topology): CanvasEdge[] {
  const existing = new Map(source.edges.map((item) => [item.edge_id, item]));
  return edges.map((item) => ({
    edge_id: item.id,
    source: item.source,
    target: item.target,
    edge_type: (item.data?.kind ||
      existing.get(item.id)?.edge_type ||
      "executes_on") as EdgeKind,
    priority: existing.get(item.id)?.priority || 100,
  }));
}

function TopologyList({
  nodes,
  edges,
  resources,
  setNodes,
  setEdges,
  changed,
}: {
  nodes: Node<TopologyNodeData>[];
  edges: Edge[];
  resources: { node_id: string; workloads: string[] }[];
  setNodes: (
    value:
      | Node<TopologyNodeData>[]
      | ((items: Node<TopologyNodeData>[]) => Node<TopologyNodeData>[]),
  ) => void;
  setEdges: (value: Edge[] | ((items: Edge[]) => Edge[])) => void;
  changed: () => void;
}) {
  return (
    <div className="list-editor">
      <h2>节点</h2>
      {nodes.map((node) => (
        <article className="list-card" key={node.id}>
          <label>
            名称
            <input
              value={node.data.label}
              onChange={(event) => {
                setNodes((items) =>
                  items.map((item) =>
                    item.id === node.id
                      ? {
                          ...item,
                          data: { ...item.data, label: event.target.value },
                        }
                      : item,
                  ),
                );
                changed();
              }}
            />
          </label>
          <span>{node.data.kind}</span>
          {node.data.kind === "resource_pool" ? (
            <label>
              池成员
              <select
                multiple
                value={node.data.memberIds}
                onChange={(event) => {
                  const memberIds = Array.from(
                    event.target.selectedOptions,
                    (item) => item.value,
                  );
                  setNodes((items) =>
                    items.map((item) =>
                      item.id === node.id
                        ? { ...item, data: { ...item.data, memberIds } }
                        : item,
                    ),
                  );
                  changed();
                }}
              >
                {resources.map((item) => (
                  <option key={item.node_id} value={item.node_id}>
                    {item.node_id}
                  </option>
                ))}
              </select>
            </label>
          ) : ["project", "controller"].includes(node.data.kind) ? (
            <span>{node.data.resourceId}</span>
          ) : (
            <label>
              绑定资源
              <select
                value={node.data.resourceId}
                onChange={(event) => {
                  setNodes((items) =>
                    items.map((item) =>
                      item.id === node.id
                        ? {
                            ...item,
                            data: {
                              ...item.data,
                              resourceId: event.target.value,
                            },
                          }
                        : item,
                    ),
                  );
                  changed();
                }}
              >
                <option value="">选择资源</option>
                {resources
                  .filter((item) =>
                    node.data.kind === "execution"
                      ? item.workloads.includes("coding")
                      : node.data.kind === "test"
                        ? item.workloads.includes("test")
                        : item.workloads.includes("acceptance"),
                  )
                  .map((item) => (
                    <option key={item.node_id} value={item.node_id}>
                      {item.node_id}
                    </option>
                  ))}
              </select>
            </label>
          )}
          <button
            onClick={() => {
              setNodes((items) => items.filter((item) => item.id !== node.id));
              setEdges((items) =>
                items.filter(
                  (item) => item.source !== node.id && item.target !== node.id,
                ),
              );
              changed();
            }}
          >
            删除
          </button>
        </article>
      ))}
      <h2>连线</h2>
      {edges.map((edge) => (
        <article className="list-card" key={edge.id}>
          <strong>
            {edge.source} → {edge.target}
          </strong>
          <label>
            语义
            <select
              value={String(edge.data?.kind || "executes_on")}
              onChange={(event) => {
                const kind = event.target.value as EdgeKind;
                setEdges((items) =>
                  items.map((item) =>
                    item.id === edge.id
                      ? { ...item, label: EDGE_LABELS[kind], data: { kind } }
                      : item,
                  ),
                );
                changed();
              }}
            >
              {Object.entries(EDGE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => {
              setEdges((items) => items.filter((item) => item.id !== edge.id));
              changed();
            }}
          >
            删除
          </button>
        </article>
      ))}
    </div>
  );
}
