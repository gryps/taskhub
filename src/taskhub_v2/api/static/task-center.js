const statusLabels = {running: "运行中", waiting: "等待人工处理", blocked: "已阻塞",
  failed: "失败", completed: "已完成", rejected: "已终止"};
const stageLabels = Object.fromEntries(stages.map(([id, label]) => [id, label]));
Object.assign(stageLabels, {implementation_blocked: "实施（阻塞）", merge_blocked: "发布（阻塞）",
  browser_acceptance: "浏览器验收", supervision: "监督", rejected: "已终止", failed: "失败"});

function taskDetailText(task) {
  if (task.blocking_reason) return task.blocking_reason.detail || task.blocking_reason.code;
  if (task.pending_action) return task.pending_action.title || task.pending_action.type;
  return "—";
}

let taskPage = 1;
let taskRequest = 0;

function taskQuery() {
  const params = new URLSearchParams();
  [["project_id", "filter-project"], ["production_line", "filter-line"],
    ["status", "filter-status"], ["stage", "filter-stage"]].forEach(([key, id]) => {
    if (byId(id).value) params.set(key, byId(id).value);
  });
  params.set("page", taskPage);
  return params.toString();
}

async function loadTaskCenter() {
  const requestId = ++taskRequest;
  try {
    const page = await request(`/api/runs?${taskQuery()}`);
    if (requestId !== taskRequest) return;
    byId("task-message").textContent = `共 ${page.total} 个任务 · 第 ${page.page} / ${Math.max(1, Math.ceil(page.total / page.page_size))} 页`;
    byId("previous-tasks").disabled = page.page <= 1;
    byId("next-tasks").disabled = page.page * page.page_size >= page.total;
    byId("task-rows").innerHTML = page.items.map((task) => `
      <tr tabindex="0" data-run-id="${escapeHtml(task.run_id)}">
        <td><strong>${escapeHtml(task.requirement_summary)}</strong><small>${escapeHtml(task.run_id)}</small></td>
        <td>${escapeHtml(task.project_id)}</td><td>${escapeHtml(task.production_line)}</td>
        <td>${escapeHtml(stageLabels[task.stage] || task.stage)}</td>
        <td><span class="task-status status-${task.status}">${escapeHtml(statusLabels[task.status] || task.status)}</span></td>
        <td class="task-attention">${escapeHtml(taskDetailText(task))}</td>
        <td>${new Date(task.updated_at).toLocaleString()}</td>
      </tr>`).join("");
    byId("task-rows").querySelectorAll("tr").forEach((row) => {
      const open = () => openTask(row.dataset.runId);
      row.addEventListener("click", open);
      row.addEventListener("keydown", (event) => { if (event.key === "Enter") open(); });
    });
  } catch (error) { byId("task-message").textContent = error.message; }
}

async function openTask(runId) {
  const run = await request(`/api/runs/${encodeURIComponent(runId)}`);
  showTaskDetail(run);
}

function showTaskDetail(run) {
  currentRun = run.run_id;
  showPage("detail");
  render(run);
  watch(run.run_id);
  byId("flow").scrollIntoView({behavior: "smooth", block: "start"});
}

function showPage(page) {
  const tasks = page === "tasks";
  const resources = page === "resources";
  byId("task-center").classList.toggle("hidden", !tasks);
  byId("workflow-page").classList.toggle("hidden", tasks || resources);
  byId("resource-page").classList.toggle("hidden", !resources);
  byId("workflow-page").classList.toggle("task-detail", page === "detail");
  byId("nav-tasks").classList.toggle("nav-active", tasks || page === "detail");
  byId("nav-workflow").classList.toggle("nav-active", page === "workflow");
  byId("nav-resources").classList.toggle("nav-active", resources);
  if (tasks) { eventSource?.close(); loadTaskCenter(); }
  if (resources) { eventSource?.close(); window.loadResources?.(); }
}

const stageFilter = byId("filter-stage");
stages.forEach(([id, label]) => stageFilter.insertAdjacentHTML("beforeend",
  `<option value="${id}">${label}</option>`));
[["filter-project", "input"], ["filter-line", "input"], ["filter-status", "change"],
  ["filter-stage", "change"]].forEach(([id, event]) => byId(id).addEventListener(event, () => { taskPage = 1; loadTaskCenter(); }));
byId("refresh-tasks").addEventListener("click", loadTaskCenter);
byId("nav-tasks").addEventListener("click", () => showPage("tasks"));
byId("nav-workflow").addEventListener("click", () => showPage("workflow"));
byId("nav-resources").addEventListener("click", () => showPage("resources"));
window.loadTaskCenter = loadTaskCenter;
window.showTaskDetail = showTaskDetail;

byId("previous-tasks").addEventListener("click", () => { taskPage--; loadTaskCenter(); });
byId("next-tasks").addEventListener("click", () => { taskPage++; loadTaskCenter(); });
