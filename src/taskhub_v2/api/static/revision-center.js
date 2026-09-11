let revisionProjectId = null;
let revisionPlan = null;

function revisionById(id) { return document.getElementById(id); }
function revisionEscape(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}
function revisionCookie(name) {
  const value = document.cookie.split("; ").find((item) => item.startsWith(`${name}=`));
  return value ? decodeURIComponent(value.split("=").slice(1).join("=")) : "";
}
async function revisionRequest(path, options = {}) {
  const method = options.method || "GET";
  const headers = {"Content-Type": "application/json", ...(options.headers || {})};
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers["X-CSRF-Token"] = revisionCookie("taskhub_v2_csrf");
  }
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

function revisionStateLabel(status) {
  return ({proposed: "待批准", approved: "已批准", applied: "已应用", rejected: "已拒绝"})[status] || status;
}

function renderRevisionPlan(data) {
  revisionPlan = data?.execution_plan || null;
  const tasks = data?.tasks || [];
  revisionById("change-request-plan").textContent = revisionPlan
    ? `${revisionPlan.plan_id} · v${revisionPlan.version} · ${tasks.length} 个任务`
    : "请先选择一个已生成执行计划的运行";
  revisionById("create-change-request").disabled = !revisionPlan;
  revisionById("change-request-tasks").innerHTML = tasks.length
    ? tasks.map((task) => `<label><input type="checkbox" name="revision-task" value="${revisionEscape(task.task_id)}"> <span><strong>${revisionEscape(task.title)}</strong><small>${revisionEscape(task.task_id)} · ${revisionEscape(task.status)}</small></span></label>`).join("")
    : '<span class="muted">执行计划加载后显示</span>';
}

function renderChangeRequests(items) {
  revisionById("change-request-summary").textContent = items.length
    ? `${items.length} 条 · ${revisionStateLabel(items[0].status)}` : "尚无变更请求";
  const mayAct = typeof canPermission === "function" && canPermission("delivery:execute");
  revisionById("change-request-list").innerHTML = items.length ? items.map((item) => {
    const diff = item.plan_diff || {};
    const actions = !mayAct ? "" : item.status === "proposed"
      ? `<button data-revision-action="approve" data-request-id="${revisionEscape(item.change_request_id)}">批准</button><button class="secondary" data-revision-action="reject" data-request-id="${revisionEscape(item.change_request_id)}">拒绝</button>`
      : item.status === "approved"
        ? `<button data-revision-action="apply" data-request-id="${revisionEscape(item.change_request_id)}">应用计划 v${revisionEscape(diff.to_version)}</button><button class="secondary" data-revision-action="reject" data-request-id="${revisionEscape(item.change_request_id)}">拒绝</button>` : "";
    return `<article class="management-card revision-card"><header><div><h3>${revisionEscape(item.reason)}</h3><p>${revisionEscape(item.change_request_id)} · ${revisionEscape(item.source_event)}</p></div><span class="card-state ${item.status === "rejected" ? "bad" : item.status === "applied" ? "ready" : "warn"}">${revisionStateLabel(item.status)}</span></header><dl class="revision-facts"><div><dt>计划变化</dt><dd>v${revisionEscape(diff.from_version)} → v${revisionEscape(diff.to_version)}</dd></div><div><dt>重新执行</dt><dd>${(diff.rerun || []).length}</dd></div><div><dt>复用成果</dt><dd>${(diff.reuse || []).length}</dd></div><div><dt>回归任务</dt><dd>${(item.regression_scope || []).length}</dd></div></dl><details class="revision-impact"><summary>查看受影响子图和原因</summary><div><strong>重新执行</strong><p>${(diff.rerun || []).map(revisionEscape).join("、") || "无"}</p><strong>复用</strong><p>${(diff.reuse || []).map(revisionEscape).join("、") || "无"}</p></div></details>${actions ? `<footer class="management-card-actions">${actions}</footer>` : ""}</article>`;
  }).join("") : '<p class="empty-state">尚无变更请求</p>';
}

async function loadRevisionCenter(projectId, runId = null) {
  revisionProjectId = projectId;
  const disclosure = revisionById("change-request-disclosure");
  disclosure.classList.toggle("hidden", !projectId);
  if (!projectId) return;
  const [requests, plan] = await Promise.all([
    revisionRequest(`/api/change-requests?project_id=${encodeURIComponent(projectId)}`),
    runId ? revisionRequest(`/api/runs/${encodeURIComponent(runId)}/execution-plan`).catch(() => null) : Promise.resolve(null),
  ]);
  renderRevisionPlan(plan);
  renderChangeRequests(requests.change_requests || []);
}
window.loadRevisionCenter = loadRevisionCenter;

revisionById("change-request-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = revisionById("change-request-message");
  if (!revisionProjectId || !revisionPlan) return;
  message.textContent = "正在计算受影响子图…";
  try {
    await revisionRequest("/api/change-requests", {method: "POST", body: JSON.stringify({
      project_id: revisionProjectId,
      plan_id: revisionPlan.plan_id,
      plan_version: revisionPlan.version,
      reason: revisionById("change-request-reason").value.trim(),
      changed_paths: revisionById("change-request-paths").value.split("\n").map((item) => item.trim()).filter(Boolean),
      affected_task_ids: [...document.querySelectorAll('[name="revision-task"]:checked')].map((item) => item.value),
    })});
    message.textContent = "影响分析已生成，等待批准";
    event.target.reset();
    await loadRevisionCenter(revisionProjectId, currentRun);
  } catch (error) { message.textContent = error.message; }
});

revisionById("change-request-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-revision-action]");
  if (!button || !revisionProjectId) return;
  const message = revisionById("change-request-message");
  button.disabled = true;
  try {
    await revisionRequest(`/api/change-requests/${encodeURIComponent(button.dataset.requestId)}/${button.dataset.revisionAction}?project_id=${encodeURIComponent(revisionProjectId)}`, {method: "POST"});
    message.textContent = button.dataset.revisionAction === "apply" ? "新执行计划已激活" : "变更请求已更新";
    await loadRevisionCenter(revisionProjectId, currentRun);
  } catch (error) { message.textContent = error.message; button.disabled = false; }
});
