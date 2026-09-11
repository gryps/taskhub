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
let registeredProjects = [];
let passwordSetupRequired = false;
let currentAuth = null;
let permissionObserver = null;
let productizationEnabled = false;
let currentProductSpecDetail = null;
let currentProjectContract = null;
let activeProject = null;

const publicImageRegistries = [
  {
    name: "GitHub Container Registry",
    shortName: "GHCR",
    images: [
      ["Seed", "ghcr.io/gryps/taskhub-seed:0.1.0-alpha"],
      ["Node", "ghcr.io/gryps/taskhub-node:0.1.0-alpha"],
    ],
  },
  {
    name: "阿里云容器镜像服务",
    shortName: "杭州 ACR",
    images: [
      ["Seed", "crpi-kgqnka7pmz9f3sml.cn-hangzhou.personal.cr.aliyuncs.com/taskhub-v2/taskhub-seed:0.1.0-alpha"],
      ["Node", "crpi-kgqnka7pmz9f3sml.cn-hangzhou.personal.cr.aliyuncs.com/taskhub-v2/taskhub-node:0.1.0-alpha"],
    ],
  },
];

const byId = (id) => document.getElementById(id);
const canPermission = (permission) => {
  const permissions = new Set(currentAuth?.permissions || []);
  return permissions.has("*") || permissions.has(permission);
};
window.taskhubCan = canPermission;
const stageIndex = (name) => stages.findIndex(([id]) => id === (
  name === "implementation_blocked" ? "implementation" :
  name === "acceptance_blocked" ? "acceptance" :
  name === "merge_blocked" ? "merging" : name
));
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
})[character]);

function renderPublicImageDownloads() {
  const groups = publicImageRegistries.map((registry) => `
    <section class="image-registry-group">
      <div><strong>${escapeHtml(registry.name)}</strong><span>${escapeHtml(registry.shortName)}</span></div>
      ${registry.images.map(([role, reference]) => `
        <div class="image-download-row">
          <span>${escapeHtml(role)}</span>
          <code>${escapeHtml(reference)}</code>
          <button type="button" class="secondary copy-image-reference" data-image-reference="${escapeHtml(reference)}" aria-label="复制 ${escapeHtml(role)} 镜像拉取命令" aria-live="polite">复制 pull</button>
        </div>`).join("")}
    </section>`).join("");
  document.querySelectorAll("[data-public-image-downloads]").forEach((host) => {
    host.innerHTML = `<details class="public-image-downloads">
      <summary><span><strong>公开镜像下载</strong><small>0.1.0-alpha · linux/amd64</small></span><span>GHCR · 阿里云 ACR</span></summary>
      <div class="image-download-content">
        <p>两个镜像仓库均支持匿名拉取。国内网络优先使用阿里云 ACR。</p>
        <div class="image-registry-grid">${groups}</div>
      </div>
    </details>`;
  });
}

async function copyImageReference(button) {
  const command = `docker pull ${button.dataset.imageReference}`;
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(command);
  } else {
    const input = document.createElement("textarea");
    input.value = command;
    input.setAttribute("readonly", "");
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    input.remove();
  }
  button.textContent = "已复制";
  window.setTimeout(() => { button.textContent = "复制 pull"; }, 1600);
}

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
  byId("status").className = `badge task-status status-${run.status}`;
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
    manual_intervention: "supervision", supervision_recovery: "supervision",
    risk_recovery: "risk"};
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
  } else if (["supervision_recovery", "risk_recovery"].includes(pendingAction?.type)) {
    byId("action-stage").textContent = "第 8 环 · 监督";
    byId("action-title").textContent = "监督模型资源暂不可用";
    byId("action-detail").textContent = run.blocking_reason?.detail || "等待模型资源恢复后重试";
    byId("approve").textContent = "重试监督";
    byId("reject").textContent = "取消任务";
  } else if (pendingAction?.type === "manual_intervention") {
    byId("action-stage").textContent = "第 8 环 · 平台处置";
    byId("action-title").textContent = "修复平台、节点环境或配置后重试，禁止人工代改业务项目";
    byId("action-detail").textContent = "平台将重新检测基础资源，并自动执行部署与验收";
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
  const managedEvidence = Boolean(
    registeredProjects.find((project) => project.id === run.project_id)?.test_environment
  );
  byId("add-evidence").classList.toggle(
    "hidden", managedEvidence
      || !["revision_limit", "manual_intervention"].includes(pendingAction?.type)
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
  loadExecutionPlan(run.run_id).catch(() => renderExecutionPlan(null));
}

const dagStateLabels = {draft: "草稿", validating: "验证中", active: "已激活",
  completed: "已完成", superseded: "已替代", planned: "待运行", running: "运行中",
  failed: "失败", pending: "等待", ready: "就绪", assigned: "已分配",
  verifying: "验证中", blocked: "阻塞"};

function renderExecutionPlan(data) {
  const disclosure = byId("execution-plan-disclosure");
  const plan = data?.execution_plan;
  disclosure.classList.toggle("hidden", !productizationEnabled || !plan);
  if (!plan) return;
  const tasks = data.tasks || [];
  const batches = data.batches || [];
  const counts = tasks.reduce((result, task) => {
    result[task.status] = (result[task.status] || 0) + 1;
    return result;
  }, {});
  const state = dagStateLabels[plan.status] || plan.status;
  byId("execution-plan-summary").textContent = `v${plan.version} · ${state} · ${tasks.length} 项任务`;
  byId("execution-plan-title").textContent = `${activeProject?.name || plan.project_id} · 执行计划`;
  byId("execution-plan-detail").textContent = `计划 ${plan.plan_id} · 绑定产品规格 v${plan.product_spec_version} 与项目契约 v${plan.project_contract_version}`;
  byId("execution-plan-state").textContent = state;
  byId("execution-plan-state").className = `card-state ${["completed", "active"].includes(plan.status) ? "ok" : plan.status === "failed" ? "bad" : "warn"}`;
  byId("execution-plan-facts").innerHTML = [
    ["任务总数", tasks.length], ["等待 / 就绪", `${counts.pending || 0} / ${counts.ready || 0}`],
    ["运行 / 验证", `${(counts.assigned || 0) + (counts.running || 0)} / ${counts.verifying || 0}`],
    ["完成 / 阻塞", `${counts.completed || 0} / ${counts.blocked || 0}`],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
  byId("execution-batches").innerHTML = batches.length ? batches.map((batch) => `
    <article class="execution-batch-card">
      <header><strong>批次 ${batch.sequence}</strong><span class="dag-state dag-${escapeHtml(batch.status)}">${escapeHtml(dagStateLabels[batch.status] || batch.status)}</span></header>
      <p>${batch.task_ids.length} 项任务 · ${escapeHtml(batch.task_ids.join("、"))}</p>
      <small>${Object.keys(batch.assignments || {}).length ? `节点：${escapeHtml(Object.values(batch.assignments).join("、"))}` : "等待节点分配"}</small>
      ${Object.keys(batch.selection_reasons || {}).length ? `<small class="batch-selection-reason">${escapeHtml(Object.values(batch.selection_reasons).join("；"))}</small>` : ""}
    </article>`).join("") : '<p class="empty-state">尚无批次</p>';
  byId("execution-tasks").innerHTML = tasks.length ? tasks.map((task) => {
    const reasons = task.waiting_reasons || data.latest_snapshot?.waiting_reasons?.[task.task_id] || [];
    return `<article class="execution-task-card">
      <header><div><strong>${escapeHtml(task.title)}</strong><code>${escapeHtml(task.task_id)}</code></div><span class="dag-state dag-${escapeHtml(task.status)}">${escapeHtml(dagStateLabels[task.status] || task.status)}</span></header>
      <dl><div><dt>依赖</dt><dd>${escapeHtml((task.depends_on || []).join("、") || "无")}</dd></div><div><dt>节点</dt><dd>${escapeHtml(task.assigned_node_id || "待分配")}</dd></div><div><dt>资源锁</dt><dd>${escapeHtml((task.resource_locks || []).join("、") || "无")}</dd></div></dl>
      ${reasons.length ? `<p class="execution-waiting">${escapeHtml(reasons.join("；"))}</p>` : ""}
    </article>`;
  }).join("") : '<p class="empty-state">尚无任务</p>';
}

async function loadExecutionPlan(runId) {
  if (!productizationEnabled || !runId) return renderExecutionPlan(null);
  const data = await request(`/api/runs/${encodeURIComponent(runId)}/execution-plan`);
  if (currentRunState?.run_id === runId) renderExecutionPlan(data);
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
  if (!response.ok) {
    const detail = (await response.json()).detail || `HTTP ${response.status}`;
    if (response.status === 401 && !path.startsWith("/api/auth/")) {
      byId("login").classList.remove("hidden");
      byId("workspace").classList.add("hidden");
      byId("logout").classList.add("hidden");
      byId("login-message").textContent = "会话已超时，请重新登录";
    }
    throw new Error(detail);
  }
  return response.json();
}

function watch(runId) {
  eventSource?.close();
  eventSource = new EventSource(`/api/runs/${runId}/events`);
  eventSource.addEventListener("run", (event) => render(JSON.parse(event.data)));
}

async function loadProjects(preferredProjectId = currentProjectId) {
  const data = await request("/api/projects");
  registeredProjects = data.projects;
  let active = data.projects.find((project) => project.id === preferredProjectId);
  if (!active && data.projects.length > 0) active = data.projects[data.projects.length - 1];
  currentProjectId = active?.id || null;
  activeProject = active || null;
  if (currentProjectId) localStorage.setItem("taskhub_project_id", currentProjectId);
  else localStorage.removeItem("taskhub_project_id");
  const projectSelect = byId("workflow-project");
  projectSelect.innerHTML = data.projects.length
    ? data.projects.map((project) =>
      `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)}</option>`).join("")
    : '<option value="">暂无项目</option>';
  projectSelect.value = currentProjectId || "";
  projectSelect.disabled = data.projects.length === 0;
  byId("active-project").textContent = active
    ? `当前运行、代码仓库和预生产配置均使用“${active.name}”`
    : "请先创建或接入项目";
  renderProjectRepository(active);
  await Promise.all([loadCurrentProjectContract(), loadCurrentProductSpec()]);
  refreshStartAction();
  window.dispatchEvent(new CustomEvent("taskhub:projects", {detail: data.projects}));
}

async function loadProductizationStatus() {
  const state = await request("/api/productization/status");
  productizationEnabled = Boolean(state.enabled);
  byId("product-spec-disclosure").classList.toggle("hidden", !productizationEnabled);
  byId("project-contract-disclosure").classList.toggle("hidden", !productizationEnabled);
}

function refreshStartAction() {
  const button = byId("start");
  const repositoryReady = Boolean(activeProject?.repository_ready);
  if (!productizationEnabled) {
    button.textContent = "开始流程";
    button.disabled = !repositoryReady;
    return;
  }
  const spec = currentProductSpecDetail?.product_spec;
  const contract = currentProjectContract;
  button.textContent = !spec ? (canPermission("projects:manage") ? "生成产品规格" : "等待项目负责人生成规格")
    : spec.status !== "approved" ? "等待规格批准"
      : contract?.status !== "active" ? "等待项目契约生效" : "按批准规格开始流程";
  button.disabled = !repositoryReady || (!spec && !canPermission("projects:manage"))
    || Boolean(spec && spec.status !== "approved")
    || Boolean(spec?.status === "approved" && contract?.status !== "active");
}

function renderProjectContract(contract) {
  currentProjectContract = contract || null;
  const labels = {draft: "草稿", in_review: "待批准", active: "已生效",
    superseded: "已废弃", rejected: "已拒绝"};
  const state = byId("project-contract-state");
  if (!contract) {
    byId("project-contract-summary").textContent = "尚未生成";
    byId("project-contract-title").textContent = "当前项目契约";
    byId("project-contract-detail").textContent = "选择适合的项目模板并生成契约草稿。";
    state.textContent = "未生成";
    state.className = "card-state warn";
    byId("project-contract-source").textContent = "未绑定仓库版本";
    byId("project-contract-profile").disabled = false;
    byId("project-contract-facts").innerHTML = "";
    byId("create-project-contract").classList.remove("hidden");
    for (const id of ["review-project-contract", "activate-project-contract",
      "revise-project-contract", "run-project-contract-gate"]) byId(id).classList.add("hidden");
    refreshStartAction();
    return;
  }
  const profileNames = {"fullstack-web": "全栈 Web", "backend-api": "后端 API",
    "frontend-spa": "前端单页应用", "python-service": "Python 服务",
    "worker-service": "后台工作服务"};
  byId("project-contract-summary").textContent = `v${contract.version} · ${labels[contract.status] || contract.status}`;
  byId("project-contract-title").textContent = `${activeProject?.name || contract.project_id} · 项目契约`;
  byId("project-contract-detail").textContent = contract.inferred
    ? "已依据现有仓库扫描生成，批准前请核对架构与质量命令。"
    : "已依据标准项目模板生成。";
  state.textContent = labels[contract.status] || contract.status;
  state.className = `card-state ${contract.status === "active" ? "ok" : contract.status === "rejected" ? "bad" : "warn"}`;
  byId("project-contract-profile").value = contract.profile_id;
  byId("project-contract-profile").disabled = true;
  byId("project-contract-source").textContent = contract.repository_commit
    ? `仓库 ${contract.repository_commit.slice(0, 12)} · 契约 ${contract.contract_id}`
    : `契约 ${contract.contract_id}`;
  const commandCount = Object.values(contract.commands || {}).flat().length;
  byId("project-contract-facts").innerHTML = [
    ["项目模板", profileNames[contract.profile_id] || contract.profile_id],
    ["技术栈", [...(contract.languages || []), ...(contract.frameworks || [])].join(" · ") || "未声明"],
    ["模块", `${(contract.modules || []).length} 个`],
    ["质量命令", `${commandCount} 条`],
    ["机器文档", ".taskhub/project.yaml 等 3 份"],
    ["审批信息", contract.approved_by ? `${contract.approved_by} · ${contract.approved_at}` : "尚未批准"],
  ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  byId("create-project-contract").classList.add("hidden");
  byId("review-project-contract").classList.toggle("hidden", contract.status !== "draft");
  byId("activate-project-contract").classList.toggle("hidden", contract.status !== "in_review");
  byId("revise-project-contract").classList.toggle("hidden", contract.status !== "active");
  byId("run-project-contract-gate").classList.toggle("hidden", contract.status !== "active");
  applyPermissions();
  refreshStartAction();
}

async function loadCurrentProjectContract() {
  if (!productizationEnabled || !currentProjectId) {
    renderProjectContract(null);
    return;
  }
  const detail = await request(`/api/projects/${encodeURIComponent(currentProjectId)}/project-contract`);
  renderProjectContract(detail.project_contract);
}

async function createProjectContract(forceRevision = false) {
  if (!currentProjectId) return;
  byId("project-contract-message").textContent = forceRevision ? "正在创建修订版本" : "正在生成契约草稿";
  await request(`/api/projects/${encodeURIComponent(currentProjectId)}/project-contracts/draft`, {
    method: "POST",
    body: JSON.stringify({
      profile_id: currentProjectContract?.profile_id || byId("project-contract-profile").value,
      inferred: false,
      force_revision: forceRevision,
    }),
  });
  byId("project-contract-disclosure").open = true;
  await loadCurrentProjectContract();
  byId("project-contract-message").textContent = "契约草稿已生成，请核对后提交评审";
}

async function transitionProjectContract(action) {
  const contract = currentProjectContract;
  if (!contract) return;
  await request(`/api/projects/${encodeURIComponent(currentProjectId)}/project-contracts/${encodeURIComponent(contract.contract_id)}/versions/${contract.version}/${action}`, {method: "POST"});
  await loadCurrentProjectContract();
}

async function runProjectContractGate() {
  const host = byId("project-contract-gate");
  host.classList.remove("hidden");
  host.textContent = "正在执行结构、架构、迁移、凭据与交付物门禁…";
  const report = await request(`/api/projects/${encodeURIComponent(currentProjectId)}/project-contract/gate`, {
    method: "POST", body: JSON.stringify({execute_commands: true, strict: true}),
  });
  const failed = (report.findings || []).filter((item) => item.status === "failed");
  host.className = `project-contract-gate ${report.status === "passed" ? "ok" : "bad"}`;
  host.textContent = report.status === "passed"
    ? `门禁通过 · 已检查 ${(report.findings || []).length} 项`
    : `门禁未通过 · ${failed.slice(0, 3).map((item) => item.summary).join("；")}`;
}

function productSpecList(title, values) {
  const items = (values || []).filter(Boolean);
  return `<section class="product-spec-section"><h4>${escapeHtml(title)}</h4>${items.length
    ? `<ul>${items.map((item) => `<li>${escapeHtml(typeof item === "string" ? item : JSON.stringify(item))}</li>`).join("")}</ul>`
    : "<p>未声明</p>"}</section>`;
}

function renderProductSpec(detail, versions = []) {
  currentProductSpecDetail = detail?.product_spec ? detail : null;
  const spec = currentProductSpecDetail?.product_spec;
  const disclosure = byId("product-spec-disclosure");
  const versionSelect = byId("product-spec-version");
  const labels = {draft: "草稿", in_review: "待批准", approved: "已批准",
    superseded: "已废弃", rejected: "已拒绝"};
  if (!spec) {
    byId("product-spec-summary").textContent = "尚未生成";
    byId("product-spec-title").textContent = "当前项目产品规格";
    byId("product-spec-detail").textContent = "输入开发需求后先生成产品规格。";
    byId("product-spec-state").textContent = "未生成";
    byId("product-spec-state").className = "card-state warn";
    byId("product-spec-source").textContent = "未绑定需求";
    byId("product-spec-sections").innerHTML = "";
    versionSelect.innerHTML = '<option value="">暂无版本</option>';
    versionSelect.disabled = true;
    byId("product-decision-form").classList.add("hidden");
    for (const id of ["review-product-spec", "approve-product-spec", "revise-product-spec",
      "product-spec-revision-field", "show-product-spec-diff"]) byId(id).classList.add("hidden");
    refreshStartAction();
    return;
  }
  disclosure.classList.remove("hidden");
  byId("product-spec-summary").textContent = `v${spec.version} · ${labels[spec.status] || spec.status}`;
  byId("product-spec-title").textContent = `${activeProject?.name || spec.project_id} · 产品规格`;
  byId("product-spec-detail").textContent = spec.summary || spec.goals?.[0] || "产品规格草稿";
  const state = byId("product-spec-state");
  state.textContent = labels[spec.status] || spec.status;
  state.className = `card-state ${spec.status === "approved" ? "ok" : spec.status === "rejected" ? "bad" : "warn"}`;
  byId("product-spec-source").textContent = `规格 ${spec.spec_id} · 来源需求 ${(spec.source_requirement_ids || []).join("、")}`;
  versionSelect.innerHTML = versions.map((item) =>
    `<option value="${item.version}">v${item.version} · ${escapeHtml(labels[item.status] || item.status)}</option>`).join("");
  versionSelect.value = String(spec.version);
  versionSelect.disabled = versions.length < 2;
  byId("product-spec-sections").innerHTML = [
    productSpecList("规格需求快照", Object.values(spec.source_requirement_snapshots || {})),
    productSpecList("产品目标", spec.goals), productSpecList("目标用户", spec.personas),
    productSpecList("本次范围", spec.in_scope), productSpecList("排除范围", spec.out_of_scope),
    productSpecList("功能要求", spec.functional_requirements),
    productSpecList("非功能要求", spec.non_functional_requirements),
    productSpecList("交付要求", spec.delivery_requirements),
    productSpecList("验收标准", spec.acceptance_criteria),
    productSpecList("风险", spec.risks), productSpecList("假设", spec.assumptions),
  ].join("");
  const decision = (detail.decisions || []).find((item) => item.status === "pending");
  const decisionForm = byId("product-decision-form");
  decisionForm.classList.toggle("hidden", !decision);
  decisionForm.dataset.decisionId = decision?.decision_id || "";
  byId("product-decision-questions").innerHTML = decision ? decision.questions.map((question) =>
    `<label>${escapeHtml(question.prompt)}<small>${escapeHtml(question.reason)}</small><textarea required maxlength="4000" data-question-key="${escapeHtml(question.key)}"></textarea></label>`).join("") : "";
  const mayManage = canPermission("projects:manage");
  byId("review-product-spec").classList.toggle("hidden", spec.status !== "draft" || Boolean(decision));
  byId("approve-product-spec").classList.toggle("hidden", spec.status !== "in_review");
  byId("revise-product-spec").classList.toggle("hidden", spec.status !== "approved");
  byId("product-spec-revision-field").classList.toggle("hidden", spec.status !== "approved" || !mayManage);
  byId("show-product-spec-diff").classList.toggle("hidden", spec.version <= 1);
  byId("product-spec-diff").classList.add("hidden");
  byId("product-spec-message").textContent = decision ? "请先完成产品级待决策事项" : "";
  applyPermissions();
  refreshStartAction();
}

async function loadCurrentProductSpec() {
  if (!productizationEnabled || !currentProjectId) {
    renderProductSpec(null);
    return;
  }
  const detail = await request(`/api/product-specs/current?project_id=${encodeURIComponent(currentProjectId)}`);
  const spec = detail.product_spec;
  if (!spec) {
    renderProductSpec(null);
    return;
  }
  const versions = await request(`/api/product-specs/${encodeURIComponent(spec.spec_id)}/versions?project_id=${encodeURIComponent(currentProjectId)}`);
  renderProductSpec(detail, versions.product_specs);
}

async function createProductSpecDraft() {
  const originalText = byId("requirement").value.trim();
  if (originalText.length < 3) throw new Error("请先填写至少 3 个字符的开发需求");
  const detail = await request("/api/requirements", {method: "POST", body: JSON.stringify({
    project_id: currentProjectId, original_text: originalText, attachments: [],
  })});
  byId("product-spec-disclosure").open = true;
  await loadCurrentProductSpec();
  byId("product-spec-message").textContent = detail.decision
    ? "规格草稿已生成，请集中完成待决策事项" : "规格草稿已生成，可以提交评审";
}

async function transitionProductSpec(action) {
  const spec = currentProductSpecDetail?.product_spec;
  if (!spec) return;
  await request(`/api/product-specs/${encodeURIComponent(spec.spec_id)}/versions/${spec.version}/${action}?project_id=${encodeURIComponent(currentProjectId)}`, {method: "POST"});
  await loadCurrentProductSpec();
}

async function resolveProductDecision(event) {
  event.preventDefault();
  const form = byId("product-decision-form");
  if (!form.reportValidity()) return;
  const answers = {};
  form.querySelectorAll("[data-question-key]").forEach((item) => { answers[item.dataset.questionKey] = item.value.trim(); });
  await request(`/api/projects/${encodeURIComponent(currentProjectId)}/product-decisions/${encodeURIComponent(form.dataset.decisionId)}/resolve`, {method: "POST", body: JSON.stringify({answers})});
  await loadCurrentProductSpec();
  byId("product-spec-message").textContent = "待决策事项已保存，可以提交评审";
}

async function createProductSpecRevision() {
  const spec = currentProductSpecDetail?.product_spec;
  const reason = byId("product-spec-revision-reason").value.trim();
  if (!spec || reason.length < 3) {
    byId("product-spec-message").textContent = "请填写至少 3 个字符的修订原因";
    return;
  }
  await request(`/api/product-specs/${encodeURIComponent(spec.spec_id)}/versions/${spec.version}/revisions?project_id=${encodeURIComponent(currentProjectId)}`, {method: "POST", body: JSON.stringify({reason})});
  byId("product-spec-revision-reason").value = "";
  await loadCurrentProductSpec();
  byId("product-spec-message").textContent = "修订草稿已创建，原批准版本保持不变";
}

async function showProductSpecDiff() {
  const spec = currentProductSpecDetail?.product_spec;
  if (!spec || spec.version <= 1) return;
  const result = await request(`/api/product-specs/${encodeURIComponent(spec.spec_id)}/diff?project_id=${encodeURIComponent(currentProjectId)}&from_version=${spec.version - 1}&to_version=${spec.version}`);
  const host = byId("product-spec-diff");
  host.innerHTML = `<h4>v${spec.version - 1} → v${spec.version} 字段差异</h4><dl>${result.changes.map((item) =>
    `<div><dt>${escapeHtml(item.field)}</dt><dd>${escapeHtml(JSON.stringify(item.before))} → ${escapeHtml(JSON.stringify(item.after))}</dd></div>`).join("") || "<div><dd>没有内容变化</dd></div>"}</dl>`;
  host.classList.remove("hidden");
}

function renderProjectRepository(project) {
  const settings = project?.repository_settings;
  const ready = Boolean(project?.repository_ready && settings?.ready);
  byId("project-repository-card-title").textContent = project
    ? `${project.name} · 代码仓库` : "当前项目仓库";
  byId("project-repository-detail").textContent = settings?.detail || "请选择项目";
  byId("project-repository-summary").textContent = !project
    ? "没有已接入项目" : ready ? `${settings.provider} · ${settings.base_ref}` : "仓库需要修复";
  const state = byId("project-repository-state");
  state.textContent = !project ? "未配置" : ready ? "已配置" : "异常";
  state.className = `card-state ${ready ? "ok" : project ? "bad" : "warn"}`;
  byId("project-repository-provider").textContent = settings?.provider || "—";
  byId("project-repository-auth").textContent = settings?.credential_mode === "seed_ssh"
    ? "Seed SSH 运行身份" : settings ? "Seed Git 运行身份" : "—";
  byId("project-repository-url").value = settings?.remote_url || "";
  byId("project-repository-remote").value = settings?.remote_name || "origin";
  byId("project-repository-branch").value = settings?.base_ref || "main";
  byId("project-repository-path").value = settings?.local_path || "";
  const allowed = Boolean(project && canPermission("projects:manage"));
  for (const id of ["project-repository-url", "project-repository-remote", "project-repository-branch", "check-project-repository", "save-project-repository"]) {
    byId(id).disabled = !allowed;
  }
  byId("project-repository-message").textContent = "";
}

async function saveProjectRepository(event) {
  event.preventDefault();
  if (!currentProjectId || !byId("project-repository-form").reportValidity()) return;
  const button = byId("save-project-repository");
  button.disabled = true;
  byId("project-repository-message").textContent = "正在验证并保存仓库配置";
  try {
    const project = await request(`/api/projects/${encodeURIComponent(currentProjectId)}/repository`, {
      method: "PUT",
      body: JSON.stringify({
        remote_url: byId("project-repository-url").value.trim(),
        remote_name: byId("project-repository-remote").value.trim(),
        base_ref: byId("project-repository-branch").value.trim(),
      }),
    });
    await loadProjects(project.id);
    byId("project-repository-message").textContent = "仓库配置已保存，连接验证通过";
  } catch (error) {
    byId("project-repository-message").textContent = error.message;
  } finally {
    button.disabled = !canPermission("projects:manage");
  }
}

async function checkProjectRepository() {
  if (!currentProjectId) return;
  const button = byId("check-project-repository");
  button.disabled = true;
  byId("project-repository-message").textContent = "正在检测仓库、分支和远端连接";
  try {
    const result = await request(`/api/projects/${encodeURIComponent(currentProjectId)}/repository/check`, {method: "POST"});
    byId("project-repository-state").textContent = "连接正常";
    byId("project-repository-state").className = "card-state ok";
    byId("project-repository-detail").textContent = result.detail;
    byId("project-repository-message").textContent = `验证通过 · ${result.commit.slice(0, 12)}`;
  } catch (error) {
    byId("project-repository-state").textContent = "连接失败";
    byId("project-repository-state").className = "card-state bad";
    byId("project-repository-message").textContent = error.message;
  } finally {
    button.disabled = !canPermission("projects:manage");
  }
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
      test_database: byId("project-test-database").checked,
      profile_id: byId("project-type").value,
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
    "fullstack-web": "python3 -m pytest -q\nnpm test",
    "backend-api": "python3 -m pytest -q",
    "frontend-spa": "npm test",
    "python-service": "python3 -m pytest -q",
    "worker-service": "python3 -m pytest -q",
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
      test_database: byId("attach-project-test-database").checked,
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
  const message = byId("login-message");
  const button = byId("login-button");
  message.textContent = "";
  let path = "/api/auth/login";
  let payload = {username: byId("login-username").value.trim() || "admin", token: byId("admin-token").value};
  if (passwordSetupRequired) {
    const password = byId("new-admin-password").value;
    if (password.length < 10) {
      message.textContent = "管理员密码至少需要 10 个字符";
      return;
    }
    if (password !== byId("confirm-admin-password").value) {
      message.textContent = "两次输入的管理员密码不一致";
      return;
    }
    path = "/api/auth/setup";
    payload = {bootstrap_token: byId("bootstrap-token").value, password};
  }
  button.disabled = true;
  button.textContent = passwordSetupRequired ? "正在设置" : "正在登录";
  try {
    await request(path, {method: "POST", body: JSON.stringify(payload)});
    byId("admin-token").value = "";
    byId("bootstrap-token").value = "";
    byId("new-admin-password").value = "";
    byId("confirm-admin-password").value = "";
    await bootstrap();
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = passwordSetupRequired ? "设置密码并登录" : "安全登录";
  }
}

function renderAuthentication(state) {
  passwordSetupRequired = Boolean(state.setup_required);
  byId("password-setup").classList.toggle("hidden", !passwordSetupRequired);
  byId("login-password-field").classList.toggle("hidden", passwordSetupRequired);
  byId("login-credentials").classList.toggle("hidden", passwordSetupRequired);
  byId("login-title").textContent = passwordSetupRequired ? "设置管理员密码" : "登录控制台";
  byId("login-description").textContent = passwordSetupRequired
    ? "首次使用需要创建管理员密码，完成后将直接进入控制台。"
    : "管理 AI 软件交付任务、运行节点与验收证据。";
  byId("login-button").textContent = passwordSetupRequired ? "设置密码并登录" : "安全登录";
}

function applyPermissions() {
  document.querySelectorAll("[data-permission]").forEach((item) => {
    item.classList.toggle("permission-hidden", !canPermission(item.dataset.permission));
  });
  const projectManager = canPermission("projects:manage");
  for (const id of ["show-project-form", "show-attach-project-form", "project-form", "attach-project-form", "test-environment-form"]) {
    byId(id)?.classList.toggle("permission-hidden", !projectManager);
  }
  byId("deploy-release")?.classList.toggle("permission-hidden", !canPermission("release:manage"));
  const infrastructureManager = canPermission("infrastructure:manage");
  document.body.classList.toggle("resource-readonly", !infrastructureManager);
  for (const id of ["host-form", "host-rebuild-form", "container-form"]) {
    byId(id)?.classList.toggle("permission-hidden", !infrastructureManager);
  }
  document.querySelectorAll("#model-services-form input, #model-services-form select, #model-services-form textarea, #platform-settings-form input, #platform-settings-form select, #platform-settings-form textarea").forEach((item) => {
    item.disabled = !infrastructureManager;
  });
}

async function bootstrap() {
  const state = await request("/api/auth/status");
  currentAuth = state;
  renderAuthentication(state);
  byId("login").classList.toggle("hidden", state.authenticated);
  byId("workspace").classList.toggle("hidden", !state.authenticated);
  byId("logout").classList.toggle("hidden", !state.authenticated);
  byId("session-identity").classList.toggle("hidden", !state.authenticated);
  byId("session-identity").textContent = state.authenticated ? `${state.actor} · ${{administrator: "管理员", project_owner: "项目负责人", developer: "开发人员", auditor: "只读审计"}[state.role] || state.role}` : "";
  if (!state.authenticated) return;
  applyPermissions();
  permissionObserver?.disconnect();
  permissionObserver = new MutationObserver(applyPermissions);
  permissionObserver.observe(byId("workspace"), {childList: true, subtree: true});
  await loadProductizationStatus();
  await loadProjects();
  const onboardingOpened = await window.loadOnboarding?.();
  if (!onboardingOpened) window.loadTaskCenter?.();
}

byId("start").addEventListener("click", async () => {
  const button = byId("start");
  button.disabled = true;
  byId("message").textContent = "";
  try {
    if (productizationEnabled && !currentProductSpecDetail?.product_spec) {
      await createProductSpecDraft();
      byId("message").textContent = "产品规格草稿已生成；批准后才能开始实施";
      return;
    }
    const spec = currentProductSpecDetail?.product_spec;
    if (productizationEnabled && spec?.status !== "approved") {
      throw new Error("产品规格尚未批准，不能开始实施");
    }
    if (productizationEnabled && currentProjectContract?.status !== "active") {
      throw new Error("项目契约尚未批准生效，不能开始实施");
    }
    const run = await request("/api/runs", {method: "POST", body: JSON.stringify({
      project_id: currentProjectId,
      requirement: byId("requirement").value,
      production_line: byId("production-line").value || "default",
      product_spec_id: spec?.spec_id || null,
      product_spec_version: spec?.version || null,
      project_contract_id: currentProjectContract?.contract_id || null,
      project_contract_version: currentProjectContract?.version || null,
    })});
    currentRun = run.run_id;
    render(run);
    watch(currentRun);
    window.showTaskDetail?.(run);
  } catch (error) {
    byId("message").textContent = error.message;
  } finally {
    refreshStartAction();
  }
});

async function decide(decision) {
  const planApproval = pendingAction?.type === "plan_approval";
  const endpoint = planApproval ? "approval" : "resume";
  const recovery = ["implementation_recovery", "acceptance_recovery", "publication_recovery", "supervision_recovery", "risk_recovery", "revision_limit", "manual_intervention"].includes(pendingAction?.type);
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

request("/api/health").then(() => {
  byId("health").innerHTML = '<i aria-hidden="true"></i>服务正常';
}).catch(() => {
  byId("health").classList.add("health-error");
  byId("health").innerHTML = '<i aria-hidden="true"></i>服务异常';
});
byId("login-button").addEventListener("click", login);
document.addEventListener("click", (event) => {
  const button = event.target.closest(".copy-image-reference");
  if (button) copyImageReference(button).catch(() => { button.textContent = "复制失败"; });
});
["login-username", "admin-token", "bootstrap-token", "new-admin-password", "confirm-admin-password"].forEach((id) => {
  byId(id).addEventListener("keydown", (event) => { if (event.key === "Enter") login(); });
});
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
byId("create-project-contract").addEventListener("click", () => {
  createProjectContract(false).catch((error) => { byId("project-contract-message").textContent = error.message; });
});
byId("review-project-contract").addEventListener("click", () => {
  transitionProjectContract("review").catch((error) => { byId("project-contract-message").textContent = error.message; });
});
byId("activate-project-contract").addEventListener("click", () => {
  transitionProjectContract("activate").catch((error) => { byId("project-contract-message").textContent = error.message; });
});
byId("revise-project-contract").addEventListener("click", () => {
  createProjectContract(true).catch((error) => { byId("project-contract-message").textContent = error.message; });
});
byId("run-project-contract-gate").addEventListener("click", () => {
  runProjectContractGate().catch((error) => {
    const host = byId("project-contract-gate");
    host.className = "project-contract-gate bad";
    host.textContent = error.message;
  });
});
byId("attach-project-form").addEventListener("submit", attachProject);
byId("attach-project-repository").addEventListener("change", applySelectedRepository);
byId("project-repository-form").addEventListener("submit", saveProjectRepository);
byId("check-project-repository").addEventListener("click", checkProjectRepository);
byId("product-decision-form").addEventListener("submit", (event) => {
  resolveProductDecision(event).catch((error) => { byId("product-spec-message").textContent = error.message; });
});
byId("review-product-spec").addEventListener("click", () => {
  transitionProductSpec("review").catch((error) => { byId("product-spec-message").textContent = error.message; });
});
byId("approve-product-spec").addEventListener("click", () => {
  transitionProductSpec("approve").catch((error) => { byId("product-spec-message").textContent = error.message; });
});
byId("revise-product-spec").addEventListener("click", () => {
  createProductSpecRevision().catch((error) => { byId("product-spec-message").textContent = error.message; });
});
byId("show-product-spec-diff").addEventListener("click", () => {
  showProductSpecDiff().catch((error) => { byId("product-spec-message").textContent = error.message; });
});
byId("product-spec-version").addEventListener("change", async (event) => {
  const spec = currentProductSpecDetail?.product_spec;
  if (!spec) return;
  try {
    const detail = await request(`/api/product-specs/${encodeURIComponent(spec.spec_id)}/versions/${event.target.value}?project_id=${encodeURIComponent(currentProjectId)}`);
    const versions = await request(`/api/product-specs/${encodeURIComponent(spec.spec_id)}/versions?project_id=${encodeURIComponent(currentProjectId)}`);
    renderProductSpec(detail, versions.product_specs);
  } catch (error) {
    byId("product-spec-message").textContent = error.message;
  }
});
byId("workflow-project").addEventListener("change", (event) => {
  loadProjects(event.target.value).catch((error) => {
    byId("active-project").textContent = error.message;
  });
});
renderFlow();
renderPublicImageDownloads();
bootstrap().catch((error) => { byId("login-message").textContent = error.message; });
