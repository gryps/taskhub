import type {
  CanvasEdge,
  CanvasNode,
  Project,
  Runtime,
  Topology,
} from "./types";

function cookie(name: string) {
  const item = document.cookie
    .split("; ")
    .find((value) => value.startsWith(`${name}=`));
  return item ? decodeURIComponent(item.split("=").slice(1).join("=")) : "";
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method || "GET";
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers["X-CSRF-Token"] = cookie("taskhub_v2_csrf");
  }
  const response = await fetch(path, {
    ...init,
    headers: { ...headers, ...(init.headers || {}) },
  });
  if (response.status === 401) {
    location.assign("/");
    throw new Error("登录会话已失效");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

export const getProjects = () => api<{ projects: Project[] }>("/api/projects");
export const getTopologies = (project: string) =>
  api<{ topologies: Topology[] }>(`/api/projects/${project}/topologies`);
export const getRuntime = (project: string) =>
  api<Runtime>(`/api/projects/${project}/topology-runtime`);
export const createDraft = (project: string) =>
  api<Topology>(`/api/projects/${project}/topologies/draft`, {
    method: "POST",
  });
export const saveDraft = (
  topology: Topology,
  nodes: CanvasNode[],
  edges: CanvasEdge[],
  viewport: Topology["viewport"],
) =>
  api<Topology>(
    `/api/projects/${topology.project_id}/topologies/${topology.topology_id}/versions/${topology.version}`,
    {
      method: "PUT",
      body: JSON.stringify({
        nodes,
        edges,
        viewport,
        expected_digest: topology.content_digest,
      }),
    },
  );
export const transition = (
  topology: Topology,
  action: "validate" | "activate",
) =>
  api<Topology>(
    `/api/projects/${topology.project_id}/topologies/${topology.topology_id}/versions/${topology.version}/${action}`,
    { method: "POST" },
  );
export const submitRequirement = (projectId: string, text: string) =>
  api("/api/requirements", {
    method: "POST",
    body: JSON.stringify({
      project_id: projectId,
      original_text: text,
      attachments: [],
    }),
  });
