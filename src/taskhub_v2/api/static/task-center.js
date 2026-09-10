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

function expandableCellText(value, limit = 20, emphasis = false) {
  const text = String(value ?? "");
  const characters = Array.from(text);
  const textClass = emphasis ? " task-title" : "";
  if (characters.length <= limit) {
    return `<span class="cell-text${textClass}">${escapeHtml(text)}</span>`;
  }
  return `<div class="cell-expandable">
    <span class="cell-preview${textClass}">${escapeHtml(characters.slice(0, limit).join(""))}…</span>
    <span class="cell-full${textClass} hidden">${escapeHtml(text)}</span>
    <button type="button" class="cell-expand" aria-expanded="false">⌄ 展开</button>
  </div>`;
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
    byId("task-rows").innerHTML = page.items.length ? page.items.map((task) => `
      <tr tabindex="0" data-run-id="${escapeHtml(task.run_id)}">
        <td>${expandableCellText(task.requirement_summary, 15, true)}<small>${escapeHtml(task.run_id)}</small></td>
        <td>${escapeHtml(task.project_id)}</td><td>${escapeHtml(task.production_line)}</td>
        <td>${escapeHtml(stageLabels[task.stage] || task.stage)}</td>
        <td><span class="task-status status-${task.status}">${escapeHtml(statusLabels[task.status] || task.status)}</span></td>
        <td class="task-attention">${expandableCellText(taskDetailText(task))}</td>
        <td>${new Date(task.updated_at).toLocaleString()}</td>
      </tr>`).join("") : '<tr class="empty-row"><td colspan="7"><strong>没有符合条件的任务</strong><small>调整筛选条件，或前往“开发流程”创建新任务。</small></td></tr>';
    byId("task-rows").querySelectorAll("tr[data-run-id]").forEach((row) => {
      const open = () => openTask(row.dataset.runId);
      row.addEventListener("click", (event) => {
        if (!event.target.closest(".cell-expand")) open();
      });
      row.addEventListener("keydown", (event) => {
        if (event.target === row && event.key === "Enter") open();
      });
    });
    byId("task-rows").querySelectorAll(".cell-expand").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        const container = button.closest(".cell-expandable");
        const expanded = button.getAttribute("aria-expanded") === "true";
        container.querySelector(".cell-preview").classList.toggle("hidden", !expanded);
        container.querySelector(".cell-full").classList.toggle("hidden", expanded);
        button.setAttribute("aria-expanded", String(!expanded));
        button.textContent = expanded ? "⌄ 展开" : "⌃ 收起";
      });
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
  const onboarding = page === "onboarding";
  byId("onboarding-page").classList.toggle("hidden", !onboarding);
  byId("task-center").classList.toggle("hidden", !tasks);
  byId("workflow-page").classList.toggle("hidden", tasks || resources || onboarding);
  byId("resource-page").classList.toggle("hidden", !resources);
  byId("workflow-page").classList.toggle("task-detail", page === "detail");
  byId("nav-tasks").classList.toggle("nav-active", tasks || page === "detail");
  byId("nav-workflow").classList.toggle("nav-active", page === "workflow");
  byId("nav-resources").classList.toggle("nav-active", resources);
  byId("nav-tasks").setAttribute("aria-current", tasks || page === "detail" ? "page" : "false");
  byId("nav-workflow").setAttribute("aria-current", page === "workflow" ? "page" : "false");
  byId("nav-resources").setAttribute("aria-current", resources ? "page" : "false");
  document.title = `${onboarding ? "首次启动向导" : resources ? "系统配置" : page === "workflow" ? "开发流程" : page === "detail" ? "任务详情" : "任务中心"} · TaskHub`;
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
