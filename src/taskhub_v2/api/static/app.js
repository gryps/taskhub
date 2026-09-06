const stages = [
  ["intake", "需求", "自动"], ["planning", "规划", "自动"],
  ["plan_approval", "计划审批", "人工"], ["implementation", "实施", "自动"],
  ["review", "审查", "自动"], ["risk", "风险", "自动"],
  ["supervision", "监督", "自动"], ["merge_approval", "发布审批", "人工"],
  ["merging", "发布", "自动"], ["completed", "完成", "终态"],
];
let currentRun = localStorage.getItem("taskhub_run_id");
let currentProjectId = localStorage.getItem("taskhub_project_id");
let eventSource;
let pendingAction;
let deploymentTimer;

const byId = (id) => document.getElementById(id);
const stageIndex = (name) => stages.findIndex(([id]) => id === (
  name === "implementation_blocked" ? "implementation" :
  name === "merge_blocked" ? "merging" : name
));
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
})[character]);

function renderFlow(stage, status) {
  const active = stage ? stageIndex(stage) : -1;
  byId("flow").innerHTML = stages.map(([id, label, actor], index) => {
    const state = index < active || status === "completed"
      ? "done" : index === active ? "active" : "";
    return `<div class="step ${state}">
      <span class="step-number">${index + 1}</span>
      <strong>${label}</strong>
      <small class="${actor === "人工" ? "manual" : ""}">${actor}</small>
    </div>`;
  }).join("");
}

function cookie(name) {
  const item = document.cookie.split("; ").find((value) => value.startsWith(`${name}=`));
  return item ? decodeURIComponent(item.split("=").slice(1).join("=")) : "";
}

function render(run) {
  byId("run").classList.remove("hidden");
  byId("run-id").textContent = run.run_id;
  const statuses = {running: "运行中", waiting: "等待处理", completed: "已完成",
    rejected: "已终止", blocked: "已阻塞", failed: "失败"};
  byId("status").textContent = statuses[run.status] || run.status;
  renderFlow(run.stage, run.status);
  byId("timeline").innerHTML = run.timeline.map((item) => `
    <li><small>${item.stage}</small><strong>${item.title}</strong><span>${item.detail || item.actor}</span></li>
  `).join("");
  pendingAction = run.pending_action;
  const waiting = Boolean(pendingAction);
  byId("action").classList.toggle("hidden", !waiting);
  if (pendingAction?.type === "plan_approval") {
    byId("action-title").textContent = "需要你审批计划";
    byId("action-detail").textContent = run.plan?.summary || "规划已经完成";
    byId("approve").textContent = "批准计划";
    byId("reject").textContent = "拒绝";
  } else if (pendingAction?.type === "implementation_recovery") {
    byId("action-title").textContent = "实施已阻塞";
    byId("action-detail").textContent = run.blocking_reason?.detail || "实施节点需要处理";
    byId("approve").textContent = "重试";
    byId("reject").textContent = "取消任务";
  } else if (pendingAction?.type === "merge_approval") {
    byId("action-title").textContent = "需要你批准发布";
    byId("action-detail").textContent = run.supervision?.summary || "监督角色已批准代码变更";
    byId("approve").textContent = "合并到权威分支";
    byId("reject").textContent = "拒绝发布";
  } else if (pendingAction?.type === "publication_recovery") {
    byId("action-title").textContent = "发布已阻塞";
    byId("action-detail").textContent = run.blocking_reason?.detail || "发布环境需要处理";
    byId("approve").textContent = "重新检查并发布";
    byId("reject").textContent = "取消任务";
  } else if (pendingAction?.type === "revision_limit") {
    byId("action-title").textContent = "返工次数已达上限";
    byId("action-detail").textContent = run.supervision?.summary || "监督仍发现未解决的问题";
    byId("approve").textContent = "批准再返工一次";
    byId("reject").textContent = "终止任务";
  }
  renderEvidence(run);
  refreshDeployment(run);
}

function renderEvidence(run) {
  const plan = run.plan;
  byId("plan-detail").innerHTML = plan ? `
    <strong>${escapeHtml(plan.summary)}</strong>
    <ol>${plan.steps.map((step) => `<li>${escapeHtml(step)}</li>`).join("")}</ol>` : "尚未生成";
  const implementation = run.implementation;
  byId("change-detail").innerHTML = implementation ? `
    <strong>${escapeHtml(implementation.summary)}</strong>
    <span class="revision-count">返工 ${run.revision_count}/${run.max_revision_attempts}</span>
    <p>${implementation.changed_files.map((name) => `<code>${escapeHtml(name)}</code>`).join(" ") || "无文件记录"}</p>
    <p>施工节点：${escapeHtml(implementation.coding_node || "控制中心")}</p>
    <p>执行节点：${escapeHtml(implementation.execution_node || "控制中心")}</p>
    <p>${implementation.tests.map((test) => `${escapeHtml(test.command.join(" "))}: ${test.exit_code === 0 ? "通过" : "失败"}`).join(" · ")}</p>` : "尚未实施";
  const supervision = run.supervision;
  byId("decision-detail").innerHTML = supervision ? `
    <strong>${supervision.decision === "approve" ? "监督通过" : "监督拒绝"}</strong>
    <p>${escapeHtml(supervision.summary)}</p>` : "尚未裁决";
  const publication = run.publication;
  byId("publication-detail").innerHTML = publication ? `
    <strong>已发布到 ${escapeHtml(publication.authority_ref)}</strong>
    <p><code>${escapeHtml(publication.published_commit)}</code>${publication.rebased ? " · 已同步最新基线" : ""}</p>
    <p>发布验证节点：${escapeHtml(publication.execution_node || "控制中心")}</p>` : "尚未发布";
}

async function refreshDeployment(run) {
  clearTimeout(deploymentTimer);
  const panel = byId("deployment-action");
  if (!run.publication || run.status !== "completed") {
    panel.classList.add("hidden");
    return;
  }
  try {
    const state = await request(`/api/deployment/status?run_id=${encodeURIComponent(run.run_id)}`);
    panel.classList.toggle("hidden", !state.eligible);
    if (!state.eligible) return;
    const labels = {
      idle: "尚未部署", queued: "等待部署执行器", validating: "正在校验权威提交",
      testing: "正在执行部署前测试", deploying: "正在替换应用文件",
      restarting: "正在重启并检查服务", completed: "部署完成，服务运行正常",
      failed: "部署失败", rolled_back: "部署失败，已自动回滚",
      rollback_failed: "部署和自动回滚均失败", unknown: "部署状态未知",
    };
    const label = labels[state.status] || state.status;
    byId("deployment-detail").textContent = `${label}${state.detail ? `：${state.detail}` : ""}`;
    const activeStates = ["queued", "validating", "testing", "deploying", "restarting"];
    const active = activeStates.includes(state.status);
    byId("deploy-release").disabled = active;
    if (active) deploymentTimer = setTimeout(() => refreshDeployment(run), 1500);
  } catch (error) {
    panel.classList.remove("hidden");
    byId("deployment-detail").textContent = `正在等待控制中心恢复：${error.message}`;
    deploymentTimer = setTimeout(() => refreshDeployment(run), 2000);
  }
}

async function deployRelease() {
  const button = byId("deploy-release");
  button.disabled = true;
  byId("deployment-detail").textContent = "正在启动独立部署执行器";
  try {
    await request(`/api/deployment/${currentRun}`, {method: "POST"});
    const run = await request(`/api/runs/${currentRun}`);
    refreshDeployment(run);
  } catch (error) {
    byId("deployment-detail").textContent = error.message;
    button.disabled = false;
  }
}

async function request(path, options = {}) {
  const method = options.method || "GET";
  const headers = {"Content-Type": "application/json", ...(options.headers || {})};
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers["X-CSRF-Token"] = cookie("taskhub_v2_csrf");
  const response = await fetch(path, {...options, headers});
  if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
  return response.json();
}

function watch(runId) {
  eventSource?.close();
  eventSource = new EventSource(`/api/runs/${runId}/events`);
  eventSource.addEventListener("run", (event) => render(JSON.parse(event.data)));
}

async function loadProviders() {
  const data = await request("/api/providers");
  const ready = data.providers.filter((item) => item.configured).length;
  byId("provider-summary").textContent = `${ready}/${data.providers.length} 可用 · OpenAI 走代理 · 其他模型直连`;
  byId("providers").innerHTML = data.providers.map((item) => `
    <div class="provider">
      <strong>${escapeHtml(item.id)}</strong>
      <span class="${item.configured ? "ok" : "bad"}">${escapeHtml(item.status)}</span>
      <span>${escapeHtml(item.model)}</span>
      <span>${escapeHtml(item.kind === "api" ? item.api_key_mask : item.route)}</span>
    </div>
  `).join("");
}

async function loadProjects(preferredProjectId = currentProjectId) {
  const data = await request("/api/projects");
  let active = data.projects.find((project) => project.id === preferredProjectId);
  if (!active && data.projects.length > 0) active = data.projects[data.projects.length - 1];
  currentProjectId = active?.id || null;
  if (currentProjectId) localStorage.setItem("taskhub_project_id", currentProjectId);
  else localStorage.removeItem("taskhub_project_id");
  byId("active-project").textContent = active
    ? `当前项目：${active.name}`
    : "请先创建或接入项目";
  byId("start").disabled = !active;
}

async function createProject(event) {
  event.preventDefault();
  const button = byId("create-project");
  button.disabled = true;
  byId("project-message").textContent = "正在创建权威仓库和工作副本";
  try {
    const project = await request("/api/projects", {method: "POST", body: JSON.stringify({
      name: byId("project-name").value,
      project_id: byId("project-id").value,
      base_ref: byId("project-branch").value,
      test_commands: byId("project-tests").value,
    })});
    await loadProjects(project.id);
    byId("project-message").textContent = `${project.name} 已创建并设为当前项目`;
    byId("project-form").classList.add("hidden");
    byId("requirement").focus();
  } catch (error) {
    byId("project-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function generatedProjectId(name) {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 48);
  return slug.length >= 2 ? slug : `project-${Date.now().toString(36)}`;
}

function applyProjectPreset() {
  const commands = {
    general: "",
    python: "python3 -m pytest -q",
    node: "npm test",
    mixed: "python3 -m pytest -q\nnpm test",
  };
  byId("project-tests").value = commands[byId("project-type").value];
}

async function attachProject(event) {
  event.preventDefault();
  const message = byId("attach-project-message");
  message.textContent = "正在验证 Git 仓库";
  try {
    const project = await request("/api/projects/attach", {method: "POST", body: JSON.stringify({
      name: byId("attach-project-name").value,
      repository: byId("attach-project-repository").value,
      base_ref: byId("attach-project-branch").value,
      test_commands: byId("attach-project-tests").value,
    })});
    await loadProjects(project.id);
    message.textContent = `${project.name} 已接入并设为当前项目`;
    byId("attach-project-form").classList.add("hidden");
    byId("requirement").focus();
  } catch (error) {
    message.textContent = error.message;
  }
}

async function loadAvailableProjects() {
  const select = byId("attach-project-repository");
  const message = byId("attach-project-message");
  select.disabled = true;
  byId("attach-project").disabled = true;
  select.innerHTML = '<option value="">正在读取 Git 仓库</option>';
  message.textContent = "";
  try {
    const data = await request("/api/projects/available");
    select.innerHTML = data.repositories.length
      ? data.repositories.map((item) => `<option value="${escapeHtml(item.repository)}"
          data-name="${escapeHtml(item.name)}" data-branch="${escapeHtml(item.default_branch)}">
          ${escapeHtml(item.name)}${item.attached ? "（已接入）" : ""}
        </option>`).join("")
      : '<option value="">Git 仓库中没有项目</option>';
    select.disabled = data.repositories.length === 0;
    byId("attach-project").disabled = data.repositories.length === 0;
    applySelectedRepository();
  } catch (error) {
    select.innerHTML = '<option value="">读取失败</option>';
    message.textContent = error.message;
  }
}

function applySelectedRepository() {
  const option = byId("attach-project-repository").selectedOptions[0];
  if (!option?.value) return;
  byId("attach-project-name").value = option.dataset.name;
  byId("attach-project-branch").value = option.dataset.branch || "main";
}

async function loadNodes() {
  const data = await request("/api/nodes");
  const ready = data.nodes.filter((item) => item.status === "ok").length;
  byId("node-summary").textContent = `${ready}/${data.nodes.length} 在线`;
  byId("nodes").innerHTML = data.nodes.map((item) => `
    <div class="provider">
      <strong>${escapeHtml(item.node_id)}</strong>
      <span class="${item.status === "ok" ? "ok" : "bad"}">${escapeHtml(item.status)}</span>
      <span>${escapeHtml(item.kind)} · ${item.active}/${item.slots}</span>
      <span>${(item.workloads || []).map(escapeHtml).join(" · ")} · 优先级 ${item.priority}</span>
      <span>${item.status === "ok"
        ? Object.entries(item.capabilities || {}).filter(([, value]) => value).map(([name]) => escapeHtml(name)).join(" · ")
        : escapeHtml(item.detail || "节点不可达")}</span>
      ${Object.entries(item.provider_health || {}).map(([provider, state]) =>
        `<span class="bad">${escapeHtml(provider)}：${escapeHtml(state.reason || state.status)}</span>`
      ).join("")}
    </div>
  `).join("");
}

async function login() {
  byId("login-message").textContent = "";
  try {
    await request("/api/auth/login", {
      method: "POST", body: JSON.stringify({token: byId("admin-token").value}),
    });
    byId("admin-token").value = "";
    await bootstrap();
  } catch (error) {
    byId("login-message").textContent = error.message;
  }
}

async function bootstrap() {
  const state = await request("/api/auth/status");
  byId("login").classList.toggle("hidden", state.authenticated);
  byId("workspace").classList.toggle("hidden", !state.authenticated);
  byId("logout").classList.toggle("hidden", !state.authenticated);
  if (!state.authenticated) return;
  await Promise.all([loadProviders(), loadProjects(), loadNodes()]);
  if (currentRun) {
    request(`/api/runs/${currentRun}`).then((run) => { render(run); watch(currentRun); }).catch(() => {});
  }
}

byId("start").addEventListener("click", async () => {
  const button = byId("start");
  button.disabled = true;
  byId("message").textContent = "";
  try {
    const run = await request("/api/runs", {method: "POST", body: JSON.stringify({
      project_id: currentProjectId,
      requirement: byId("requirement").value,
    })});
    currentRun = run.run_id;
    localStorage.setItem("taskhub_run_id", currentRun);
    render(run);
    watch(currentRun);
  } catch (error) {
    byId("message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

async function decide(decision) {
  const planApproval = pendingAction?.type === "plan_approval";
  const endpoint = planApproval ? "approval" : "resume";
  const recovery = ["implementation_recovery", "publication_recovery", "revision_limit"].includes(pendingAction?.type);
  const resolved = recovery ? (decision === "approve" ? "retry" : "cancel") : decision;
  const run = await request(`/api/runs/${currentRun}/${endpoint}`, {
    method: "POST", body: JSON.stringify({decision: resolved, comment: ""}),
  });
  render(run);
}
byId("approve").addEventListener("click", () => decide("approve"));
byId("reject").addEventListener("click", () => decide("reject"));
byId("deploy-release").addEventListener("click", deployRelease);

request("/api/health").then(() => { byId("health").textContent = "服务正常"; });
byId("login-button").addEventListener("click", login);
byId("admin-token").addEventListener("keydown", (event) => { if (event.key === "Enter") login(); });
byId("logout").addEventListener("click", async () => { await request("/api/auth/logout", {method: "POST"}); await bootstrap(); });
byId("refresh-providers").addEventListener("click", () => loadProviders());
byId("refresh-nodes").addEventListener("click", () => loadNodes());
byId("show-project-form").addEventListener("click", () => {
  byId("attach-project-form").classList.add("hidden");
  byId("project-form").classList.remove("hidden");
});
byId("close-project-form").addEventListener("click", () => byId("project-form").classList.add("hidden"));
byId("show-attach-project-form").addEventListener("click", () => {
  byId("project-form").classList.add("hidden");
  byId("attach-project-form").classList.remove("hidden");
  loadAvailableProjects();
});
byId("close-attach-project-form").addEventListener("click", () => byId("attach-project-form").classList.add("hidden"));
byId("project-form").addEventListener("submit", createProject);
byId("project-name").addEventListener("input", () => {
  byId("project-id").value = generatedProjectId(byId("project-name").value);
});
byId("project-type").addEventListener("change", applyProjectPreset);
byId("attach-project-form").addEventListener("submit", attachProject);
byId("attach-project-repository").addEventListener("change", applySelectedRepository);
renderFlow();
bootstrap().catch((error) => { byId("login-message").textContent = error.message; });
