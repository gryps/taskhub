const stages = [
  ["intake", "需求", "自动"], ["planning", "规划", "自动"],
  ["plan_approval", "计划审批", "人工"], ["implementation", "实施", "自动"],
  ["acceptance", "验收", "自动"], ["review", "审查", "自动"],
  ["browser_acceptance", "浏览器验收", "自动"],
  ["risk", "风险", "自动"],
  ["supervision", "监督", "自动"], ["merge_approval", "发布审批", "人工"],
  ["merging", "发布", "自动"], ["completed", "完成", "终态"],
];
let currentRun = null;
let currentRunState = null;
let retryCountdownTimer = null;
let currentProjectId = localStorage.getItem("taskhub_project_id");
let eventSource;
let pendingAction;
let deploymentTimer;

const byId = (id) => document.getElementById(id);
const stageIndex = (name) => stages.findIndex(([id]) => id === (
  name === "implementation_blocked" ? "implementation" :
  name === "acceptance_blocked" ? "acceptance" :
  name === "merge_blocked" ? "merging" : name
));
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
})[character]);

function renderFlow(stage, status, backendSteps = null) {
  const active = stage ? stageIndex(stage) : -1;
  byId("flow").innerHTML = stages.map(([id, label, actor], index) => {
    const currentState = status === "blocked" ? "blocked"
      : status === "waiting" ? "manual-wait" : "active";
    const backendState = backendSteps?.[index]?.state;
    const states = {completed: "done", current: "active", waiting_manual: "manual-wait",
      blocked: "blocked", not_started: "not-started"};
    const state = backendState ? states[backendState] : index < active || status === "completed"
      ? "done" : index === active ? currentState : "not-started";
    return `<div class="step ${state}" data-step-id="${id}">
      <span class="step-number">${index + 1}</span>
      <strong>${label}</strong>
      <small class="${actor === "人工" ? "manual" : ""}">${stepStateLabel(state, actor)}</small>
    </div>`;
  }).join("");
}

function stepStateLabel(state, actor) {
  return {done: "已完成", active: "进行中", "manual-wait": "待你处理",
    blocked: "已阻塞", "not-started": actor === "人工" ? "人工确认" : "未开始"}[state];
}

function cookie(name) {
  const item = document.cookie.split("; ").find((value) => value.startsWith(`${name}=`));
  return item ? decodeURIComponent(item.split("=").slice(1).join("=")) : "";
}

function render(run) {
  currentRunState = run;
  if (retryCountdownTimer) clearInterval(retryCountdownTimer);
  retryCountdownTimer = null;
  byId("run").classList.remove("hidden");
  byId("run-id").textContent = run.run_id;
  const statuses = {running: "运行中", waiting: "待处理", completed: "已完成",
    rejected: "已终止", blocked: "已阻塞", failed: "失败"};
  byId("status").textContent = statuses[run.status] || run.status;
  pendingAction = run.pending_action;
  const orphanPanel = byId("orphan-action");
  orphanPanel.classList.toggle("hidden", !run.project_missing || Boolean(run.archived_at));
  const terminal = ["completed", "rejected", "failed"].includes(run.status);
  byId("archive-task").classList.toggle(
    "hidden", Boolean(run.archived_at) || (!run.project_missing && !terminal)
  );
  byId("replay-stage").classList.toggle(
    "hidden",
    Boolean(run.archived_at) || run.status !== "running" || Boolean(pendingAction)
      || !(run.next_nodes || []).length
  );
  byId("approve").disabled = run.project_missing || Boolean(run.archived_at);
  if (run.project_missing && !run.archived_at) populateRebindProjects();
  renderFlow(run.stage, run.status, run.workflow_steps);
  const normalizedStage = run.stage === "implementation_blocked" ? "implementation"
    : run.stage === "acceptance_blocked" ? "acceptance"
    : run.stage === "merge_blocked" ? "merging" : run.stage;
  byId("current-stage").textContent = stages.find(([id]) => id === normalizedStage)?.[1] || run.stage;
  const completed = (run.workflow_steps || []).filter((item) => item.state === "completed").length;
  byId("flow-progress").textContent = `${run.status === "completed" ? stages.length : completed}/${stages.length}`;
  byId("revision-summary").textContent = `${run.revision_count}/${run.max_revision_attempts}`;
  byId("timeline-summary").textContent = `${run.timeline.length} 条`;
  byId("timeline").innerHTML = run.timeline.map((item) => `
    <li><small>${escapeHtml(stageName(item.stage))}</small><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.detail || item.actor)}</span></li>
  `).join("");
  const waiting = Boolean(pendingAction || run.blocking_reason);
  const reason = run.blocking_reason;
  byId("blocking-metadata").classList.toggle("hidden", !reason);
  byId("blocking-code").textContent = reason?.code || "—";
  byId("blocking-node").textContent = reason?.responsible_node || "—";
  byId("blocking-model").textContent = reason?.model || "—";
  byId("blocking-action").textContent = reason?.recommended_action || "—";
  const countdown = byId("retry-countdown");
  countdown.classList.add("hidden");
  const retryAfter = Number(reason?.retry_after_seconds || 0);
  if (pendingAction?.type === "publication_recovery" && retryAfter > 0) {
    let remaining = retryAfter;
    countdown.classList.remove("hidden");
    countdown.textContent = `${remaining} 秒后自动重试`;
    retryCountdownTimer = setInterval(async () => {
      remaining -= 1;
      countdown.textContent = `${Math.max(remaining, 0)} 秒后自动重试`;
      if (remaining <= 0) {
        clearInterval(retryCountdownTimer);
        retryCountdownTimer = null;
        try { await decide("approve"); } catch (error) { countdown.textContent = error.message; }
      }
    }, 1000);
  }
  const action = byId("action");
  byId("acceptance-submit").classList.toggle(
    "hidden", pendingAction?.type !== "manual_intervention"
  );
  const actionStages = {plan_approval: "plan_approval", implementation_recovery: "implementation",
    acceptance_recovery: "acceptance", merge_approval: "merge_approval",
    publication_recovery: "merging", revision_limit: "supervision",
    manual_intervention: "supervision", supervision_recovery: "supervision"};
  action.dataset.stage = actionStages[pendingAction?.type] || normalizedStage;
  action.classList.toggle("hidden", !waiting);
  if (pendingAction?.type === "plan_approval") {
    byId("action-stage").textContent = "第 3 环 · 计划审批";
    byId("action-title").textContent = "需要你审批计划";
    byId("action-detail").textContent = run.plan?.summary || "规划已经完成";
    byId("approve").textContent = "批准计划";
    byId("reject").textContent = "拒绝";
  } else if (pendingAction?.type === "implementation_recovery") {
    byId("action-stage").textContent = "第 4 环 · 实施";
    byId("action-title").textContent = "实施已阻塞";
    byId("action-detail").textContent = run.blocking_reason?.detail || "实施节点需要处理";
    byId("approve").textContent = "重试";
    byId("reject").textContent = "取消任务";
  } else if (pendingAction?.type === "acceptance_recovery") {
    byId("action-stage").textContent = "第 5 环 · 验收";
    const browserEvidence = run.blocking_reason?.code === "browser_evidence_missing";
    const contractMissing = run.blocking_reason?.code === "acceptance_contract_missing";
    byId("action-title").textContent = browserEvidence
      ? "浏览器验收需要重新执行"
      : contractMissing ? "浏览器验收契约缺失" : "验收执行已阻塞";
    const limitReached = run.revision_count >= run.max_revision_attempts;
    const limitNotice = limitReached ? " 已达到返工上限，退回实施将授权额外返工一次。" : "";
    byId("action-detail").textContent = `${run.blocking_reason?.detail || "验收节点需要处理"}${limitNotice}`;
    byId("approve").textContent = browserEvidence
      ? "自动执行浏览器验收" : "重新执行验收";
    byId("revise").textContent = limitReached ? "批准额外返工" : "退回实施";
    byId("reject").textContent = "取消任务";
  } else if (pendingAction?.type === "merge_approval") {
    byId("action-stage").textContent = "第 9 环 · 发布审批";
    byId("action-title").textContent = "需要你批准发布";
    byId("action-detail").textContent = run.supervision?.summary || "监督角色已批准代码变更";
    byId("approve").textContent = "合并到权威分支";
    byId("reject").textContent = "拒绝发布";
  } else if (pendingAction?.type === "publication_recovery") {
    byId("action-stage").textContent = "第 10 环 · 发布";
    byId("action-title").textContent = "发布已阻塞";
    byId("action-detail").textContent = run.blocking_reason?.detail || "发布环境需要处理";
    byId("approve").textContent = "重新检查并发布";
    byId("reject").textContent = "取消任务";
  } else if (pendingAction?.type === "revision_limit") {
    const missing = run.supervision?.missing_evidence || [];
    byId("action-stage").textContent = "第 8 环 · 监督";
    byId("action-title").textContent = missing.length
      ? "关键验收证据不足" : "返工次数已达上限";
    byId("action-detail").textContent = [
      run.supervision?.summary || "监督仍发现未解决的问题",
      ...(run.supervision?.reasons || []),
    ].join("\n");
    byId("approve").textContent = "批准再返工一次";
    if (missing.length) byId("approve").textContent = "重新采集验收证据";
    byId("reject").textContent = "终止任务";
  } else if (pendingAction?.type === "supervision_recovery") {
    byId("action-stage").textContent = "第 8 环 · 监督";
    byId("action-title").textContent = "监督模型资源暂不可用";
    byId("action-detail").textContent = run.blocking_reason?.detail || "等待模型资源恢复后重试";
    byId("approve").textContent = "重试监督";
    byId("reject").textContent = "取消任务";
  } else if (pendingAction?.type === "manual_intervention") {
    byId("action-stage").textContent = "第 8 环 · 平台处置";
    byId("action-title").textContent = "修复平台、节点环境或配置后重试，禁止人工代改业务项目";
    byId("action-detail").textContent = "完成候选环境操作后，提交真实验收证据以重新审查";
    byId("approve").textContent = "返回自动返工";
    byId("reject").textContent = "终止任务";
  }
  if (run.blocking_reason && !pendingAction) {
    byId("action-stage").textContent = `当前环节 · ${stageName(run.stage)}`;
    byId("action-title").textContent = "任务无法继续";
    byId("action-detail").textContent = `${run.blocking_reason.detail || run.blocking_reason.code}。当前无可用恢复操作`;
  }
  const choices = pendingAction?.choices || [];
  const resourceActions = ["implementation_recovery", "acceptance_recovery",
    "publication_recovery", "revision_limit", "manual_intervention"];
  byId("configure-resources").classList.toggle(
    "hidden", !resourceActions.includes(pendingAction?.type)
  );
  byId("add-evidence").classList.toggle(
    "hidden", !["revision_limit", "manual_intervention"].includes(pendingAction?.type)
  );
  byId("approve").classList.toggle("hidden", !choices.some((choice) =>
    ["approve", "retry", "recheck"].includes(choice)) && !(
      pendingAction?.type === "revision_limit" && run.supervision?.missing_evidence?.length
    ));
  byId("reject").classList.toggle("hidden", !choices.some((choice) =>
    ["reject", "cancel"].includes(choice)));
  byId("revise").classList.toggle(
    "hidden", pendingAction?.type !== "acceptance_recovery" || !choices.includes("revise")
  );
  byId("manual").classList.toggle(
    "hidden", pendingAction?.type !== "revision_limit"
  );
  renderEvidence(run);
  refreshDeployment(run);
}

function stageName(stage) {
  const normalized = stage === "implementation_blocked" ? "implementation"
    : stage === "acceptance_blocked" ? "acceptance"
    : stage === "merge_blocked" ? "merging" : stage;
  return stages.find(([id]) => id === normalized)?.[1] || stage;
}

function renderEvidence(run) {
  const plan = run.plan;
  byId("plan-summary").textContent = plan ? `${plan.steps.length} 个步骤` : "尚未生成";
  byId("plan-detail").innerHTML = plan ? `
    <strong>${escapeHtml(plan.summary)}</strong>
    <ol>${plan.steps.map((step) => `<li>${escapeHtml(step)}</li>`).join("")}</ol>` : "尚未生成";
  const implementation = run.implementation;
  byId("change-summary").textContent = implementation
    ? `${implementation.changed_files.length} 个文件 · ${implementation.tests.filter((test) => test.exit_code === 0).length}/${implementation.tests.length} 测试通过`
    : "尚未实施";
  byId("change-detail").innerHTML = implementation ? `
    <strong>${escapeHtml(implementation.summary)}</strong>
    <span class="revision-count">返工 ${run.revision_count}/${run.max_revision_attempts}</span>
    <p>${implementation.changed_files.map((name) => `<code>${escapeHtml(name)}</code>`).join(" ") || "无文件记录"}</p>
    <p>施工节点：${escapeHtml(implementation.coding_node || "控制中心")}</p>
    <p>执行节点：${escapeHtml(implementation.execution_node || "控制中心")}</p>
    <p>${implementation.tests.map((test) => `${escapeHtml(test.command.join(" "))}: ${test.exit_code === 0 ? "通过" : "失败"}`).join(" · ")}</p>` : "尚未实施";
  const acceptance = run.acceptance;
  byId("acceptance-summary").textContent = acceptance
    ? `${acceptance.evidence.filter((item) => item.status === "passed").length}/${acceptance.evidence.length} 通过`
    : "尚未验收";
  byId("acceptance-detail").innerHTML = acceptance ? acceptance.evidence.map((item) => `
    <p><strong>${item.status === "passed" ? "通过" : "失败"} · ${escapeHtml(item.id)}</strong><br>
    ${escapeHtml(item.summary)}<br><small>来源：${escapeHtml(item.source)}</small><br>
    ${(item.artifacts || []).map((artifact) => {
      const name = artifact.uri.split("/").pop();
      return `<a href="/api/runs/${encodeURIComponent(run.run_id)}/artifacts/${encodeURIComponent(name)}" target="_blank" rel="noopener">${escapeHtml(name)} · SHA256 ${escapeHtml(artifact.sha256)}</a>`;
    }).join("<br>")}</p>`).join("") : "尚未验收";
  const supervision = run.supervision;
  byId("decision-summary").textContent = supervision
    ? (supervision.decision === "approve" ? "已通过" : "需返工") : "尚未裁决";
  byId("decision-detail").innerHTML = supervision ? `
    <strong>${supervision.decision === "approve" ? "监督通过" : "监督拒绝"}</strong>
    <p>${escapeHtml(supervision.summary)}</p>` : "尚未裁决";
  const modelRuns = run.model_runs || [];
  const roleNames = {planner: "规划", coder: "施工", reviewer: "审查",
    risk: "风险", supervisor: "监督"};
  byId("model-run-summary").textContent = `${modelRuns.length} 次`;
  byId("model-run-detail").innerHTML = modelRuns.length ? modelRuns.map((item) => {
    const model = item.model === "account_default" ? "Codex 账号默认模型" : item.model;
    const fallback = item.failed_providers?.length
      ? `<small>已跳过：${escapeHtml(item.failed_providers.join(" · "))}</small>` : "";
    return `<p><strong>${escapeHtml(roleNames[item.role] || item.role)} · ${escapeHtml(model)}</strong><br>
      <span>${escapeHtml(item.provider)} · ${(item.duration_ms / 1000).toFixed(1)} 秒</span><br>${fallback}</p>`;
  }).join("") : "尚未调用";
  const publication = run.publication;
  byId("publication-summary").textContent = publication ? "已发布" : "尚未发布";
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
      acceptance_commands: byId("project-acceptance").value,
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
      acceptance_commands: byId("attach-project-acceptance").value,
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
  await loadProjects();
  window.loadTaskCenter?.();
}

byId("start").addEventListener("click", async () => {
  const button = byId("start");
  button.disabled = true;
  byId("message").textContent = "";
  try {
    const run = await request("/api/runs", {method: "POST", body: JSON.stringify({
      project_id: currentProjectId,
      requirement: byId("requirement").value,
      production_line: byId("production-line").value || "default",
    })});
    currentRun = run.run_id;
    render(run);
    watch(currentRun);
    window.showTaskDetail?.(run);
  } catch (error) {
    byId("message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

async function decide(decision) {
  const planApproval = pendingAction?.type === "plan_approval";
  const endpoint = planApproval ? "approval" : "resume";
  const recovery = ["implementation_recovery", "acceptance_recovery", "publication_recovery", "supervision_recovery", "revision_limit", "manual_intervention"].includes(pendingAction?.type);
  const resolved = recovery
    ? (decision === "manual" ? "manual" : decision === "approve"
      ? (pendingAction?.type === "revision_limit"
        && currentRunState?.supervision?.missing_evidence?.length ? "recheck" : "retry")
      : decision === "revise" ? "revise" : "cancel")
    : decision;
  const run = await request(`/api/runs/${currentRun}/${endpoint}`, {
    method: "POST", body: JSON.stringify({decision: resolved, comment: ""}),
  });
  render(run);
}

async function populateRebindProjects() {
  const data = await request("/api/projects");
  byId("rebind-project").innerHTML = data.projects.map((project) =>
    `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)}</option>`).join("");
  byId("rebind-task").disabled = data.projects.length === 0;
}

async function archiveTask() {
  await request(`/api/runs/${currentRun}/archive`, {method: "POST"});
  eventSource?.close();
  showPage("tasks");
}

async function rebindTask() {
  const run = await request(`/api/runs/${currentRun}/rebind`, {method: "POST",
    body: JSON.stringify({project_id: byId("rebind-project").value})});
  byId("orphan-message").textContent = "项目已重新绑定，可以重试";
  render(run);
}

async function replayStage() {
  const button = byId("replay-stage");
  button.disabled = true;
  try {
    const run = await request(`/api/runs/${currentRun}/replay`, {method: "POST"});
    render(run);
  } catch (error) {
    byId("message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function submitAcceptance(event) {
  event.preventDefault();
  const summary = byId("acceptance-note").value.trim();
  const kind = byId("acceptance-kind").value;
  const source = byId("acceptance-source").value.trim();
  const run = await request(`/api/runs/${currentRun}/acceptance`, {
    method: "POST",
    body: JSON.stringify({evidence: [{
      id: `${kind}-${Date.now()}`,
      kind,
      status: "passed",
      source,
      summary,
    }]}),
  });
  render(run);
}
byId("approve").addEventListener("click", () => decide("approve"));
byId("manual").addEventListener("click", () => decide("manual"));
byId("revise").addEventListener("click", () => decide("revise"));
byId("reject").addEventListener("click", () => decide("reject"));
byId("acceptance-submit").addEventListener("submit", submitAcceptance);
byId("deploy-release").addEventListener("click", deployRelease);
byId("archive-task").addEventListener("click", archiveTask);
byId("replay-stage").addEventListener("click", replayStage);
byId("rebind-task").addEventListener("click", rebindTask);
byId("configure-resources").addEventListener("click", () => showPage("resources"));
byId("add-evidence").addEventListener("click", () => {
  byId("acceptance-submit").classList.remove("hidden");
  byId("acceptance-source").focus();
});

request("/api/health").then(() => { byId("health").textContent = "服务正常"; });
byId("login-button").addEventListener("click", login);
byId("admin-token").addEventListener("keydown", (event) => { if (event.key === "Enter") login(); });
byId("logout").addEventListener("click", async () => { await request("/api/auth/logout", {method: "POST"}); await bootstrap(); });
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
