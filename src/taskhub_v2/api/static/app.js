const stages = [
  ["intake", "需求", "自动"], ["planning", "规划", "自动"],
  ["implementation", "实施", "自动"],
  ["acceptance", "验收", "自动"], ["review", "审查", "自动"],
  ["risk", "风险", "自动"],
  ["supervision", "监督", "自动"],
  ["merging", "发布", "自动"], ["completed", "完成", "终态"],
];
let currentRun = null;
let currentRunState = null;
let executionPlanPage = 1;
let executionPlanRunId = null;
const executionPlanPageSize = 100;
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
let currentProjectPreflight = null;
let activeProject = null;
let projectProvisioningDefaults = {};

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
  name === "browser_acceptance" ? "acceptance" :
  name === "merge_blocked" ? "merging" : name
));
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
})[character]);

function renderPublicImageDownloads() {
  document.querySelectorAll("[data-public-image-downloads]").forEach((host) => {
    const requestedRole = host.dataset.imageRole;
    const groups = publicImageRegistries.map((registry) => `
      <section class="image-registry-group">
        <div><strong>${escapeHtml(registry.name)}</strong><span>${escapeHtml(registry.shortName)}</span></div>
        ${registry.images.filter(([role]) => !requestedRole || role.toLowerCase() === requestedRole)
          .map(([role, reference]) => `
        <div class="image-download-row">
          <span>${escapeHtml(role)}</span>
          <code>${escapeHtml(reference)}</code>
          <button type="button" class="secondary copy-image-reference" data-image-reference="${escapeHtml(reference)}" aria-label="复制 ${escapeHtml(role)} 镜像拉取命令" aria-live="polite">复制 pull</button>
        </div>`).join("")}
      </section>`).join("");
    const title = requestedRole === "node" ? "公开工作节点镜像" : "公开镜像下载";
    const description = requestedRole === "node"
      ? "初始化时由 Seed 拉取，创建本机节点时直接使用；国内网络可改用阿里云 ACR 地址。"
      : "两个镜像仓库均支持匿名拉取。国内网络优先使用阿里云 ACR。";
    host.innerHTML = `<details class="public-image-downloads">
      <summary><span><strong>${title}</strong><small>0.1.0-alpha · linux/amd64</small></span><span>GHCR · 阿里云 ACR</span></summary>
      <div class="image-download-content">
        <p>${description}</p>
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
  if (executionPlanRunId !== run.run_id) {
    executionPlanRunId = run.run_id;
    executionPlanPage = 1;
  }
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
    : run.stage === "browser_acceptance" ? "acceptance"
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
  const actionStages = {implementation_recovery: "implementation",
    acceptance_recovery: "acceptance",
    publication_recovery: "merging", revision_limit: "supervision",
    manual_intervention: "supervision", supervision_recovery: "supervision",
    risk_recovery: "risk"};
  action.dataset.stage = actionStages[pendingAction?.type] || normalizedStage;
  action.classList.toggle("hidden", !waiting);
  if (pendingAction?.type === "implementation_recovery") {
    byId("action-stage").textContent = "第 3 环 · 实施";
    byId("action-title").textContent = "实施已阻塞";
    byId("action-detail").textContent = run.blocking_reason?.detail || "实施节点需要处理";
    byId("approve").textContent = "重试";
    byId("reject").textContent = "取消任务";
  } else if (pendingAction?.type === "acceptance_recovery") {
    byId("action-stage").textContent = "第 4 环 · 验收";
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
  } else if (pendingAction?.type === "publication_recovery") {
    byId("action-stage").textContent = "第 8 环 · 发布";
    byId("action-title").textContent = "发布已阻塞";
    byId("action-detail").textContent = run.blocking_reason?.detail || "发布环境需要处理";
    byId("approve").textContent = "重新检查并发布";
    byId("revise").textContent = "返回实施解决冲突";
    byId("reject").textContent = "取消任务";
  } else if (pendingAction?.type === "revision_limit") {
    const missing = run.supervision?.missing_evidence || [];
    byId("action-stage").textContent = "第 7 环 · 监督";
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
    byId("action-stage").textContent = "第 7 环 · 监督";
    byId("action-title").textContent = "监督模型资源暂不可用";
    byId("action-detail").textContent = run.blocking_reason?.detail || "等待模型资源恢复后重试";
    byId("approve").textContent = "重试监督";
    byId("reject").textContent = "取消任务";
  } else if (pendingAction?.type === "manual_intervention") {
    byId("action-stage").textContent = "第 7 环 · 平台处置";
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
    "hidden", !["acceptance_recovery", "publication_recovery"].includes(pendingAction?.type)
      || !choices.includes("revise")
  );
  byId("manual").classList.toggle(
    "hidden", pendingAction?.type !== "revision_limit"
  );
  renderEvidence(run);
  refreshDeployment(run);
  loadExecutionPlan(run.run_id).catch(() => renderExecutionPlan(null));
  if (productizationEnabled) {
    window.loadRevisionCenter?.(run.project_id, run.run_id);
  }
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
  const analysis = data.analysis || {};
  const taskCounts = analysis.task_counts || {};
  const page = data.task_page || {page: 1, page_size: executionPlanPageSize, total: tasks.length};
  const counts = tasks.reduce((result, task) => {
    result[task.status] = (result[task.status] || 0) + 1;
    return result;
  }, {});
  const state = dagStateLabels[plan.status] || plan.status;
  byId("execution-plan-summary").textContent = `v${plan.version} · ${state} · ${page.total} 项任务`;
  byId("execution-plan-title").textContent = `${activeProject?.name || plan.project_id} · 执行计划`;
  byId("execution-plan-detail").textContent = `计划 ${plan.plan_id} · 绑定产品规格 v${plan.product_spec_version} 与项目契约 v${plan.project_contract_version}`;
  byId("execution-plan-state").textContent = state;
  byId("execution-plan-state").className = `card-state ${["completed", "active"].includes(plan.status) ? "ok" : plan.status === "failed" ? "bad" : "warn"}`;
  byId("execution-plan-facts").innerHTML = [
    ["任务总数", page.total], ["本页等待 / 就绪", `${counts.pending || 0} / ${counts.ready || 0}`],
    ["剩余任务", taskCounts.remaining ?? "—"],
    ["已完成", taskCounts.completed ?? counts.completed ?? 0],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
  const prediction = analysis.prediction || {};
  const cost = analysis.cost || {};
  const quality = analysis.quality || {};
  const bottlenecks = analysis.bottlenecks || [];
  byId("execution-analysis").innerHTML = analysis.prediction ? `
    <div class="execution-analysis-facts">
      <div><span>关键路径</span><strong>${prediction.critical_path_task_ids?.length || 0} 项 / ${prediction.critical_path_units || 0} 单位</strong></div>
      <div><span>最大并行宽度</span><strong>${prediction.max_parallel_width || 0}</strong></div>
      <div><span>预计剩余批次</span><strong>${prediction.estimated_remaining_batches || 0}</strong></div>
      <div><span>成本余额</span><strong>${cost.remaining_budget_units ?? "—"} 单位</strong></div>
      <div><span>失败尝试</span><strong>${quality.failed_attempts || 0}</strong></div>
      <div><span>证据完整率</span><strong>${Math.round((quality.evidence_completeness || 0) * 100)}%</strong></div>
    </div>
    <div class="execution-bottlenecks"><strong>主要瓶颈</strong>${bottlenecks.length
      ? `<ul>${bottlenecks.slice(0, 5).map((item) => `<li>${escapeHtml(item.type === "resource_lock" ? "资源锁" : "等待原因")}：${escapeHtml(item.key)} <span>${item.task_count} 项</span></li>`).join("")}</ul>`
      : "<p>当前未识别出重复资源争用或集中阻塞。</p>"}</div>`
    : '<p class="empty-state">尚无分析</p>';
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
  const first = page.total ? (page.page - 1) * page.page_size + 1 : 0;
  const last = Math.min(page.total, page.page * page.page_size);
  const pages = Math.max(1, Math.ceil(page.total / page.page_size));
  byId("execution-task-range").textContent = page.total ? `${first}–${last} / ${page.total}` : "";
  byId("execution-task-page").textContent = `第 ${page.page} / ${pages} 页`;
  byId("execution-task-previous").disabled = page.page <= 1;
  byId("execution-task-next").disabled = page.page >= pages;
  byId("execution-task-pagination").classList.toggle("hidden", pages <= 1);
}

async function loadExecutionPlan(runId) {
  if (!productizationEnabled || !runId) return renderExecutionPlan(null);
  const data = await request(`/api/runs/${encodeURIComponent(runId)}/execution-plan?page=${executionPlanPage}&page_size=${executionPlanPageSize}`);
  if (currentRunState?.run_id === runId) renderExecutionPlan(data);
}

function renderSchedulingPolicy(project) {
  const policy = project?.scheduling_policy || {concurrency_limit: 2, priority_weight: 1, run_cost_budget_units: 100};
  byId("scheduling-policy-detail").textContent = project ? `${project.name} · 新执行计划采用此策略` : "请选择项目";
  byId("scheduling-concurrency").value = policy.concurrency_limit;
  byId("scheduling-priority").value = policy.priority_weight;
  byId("scheduling-cost-budget").value = policy.run_cost_budget_units;
  byId("scheduling-policy-summary").textContent = project
    ? `并发 ${policy.concurrency_limit} · 权重 ${policy.priority_weight} · 预算 ${policy.run_cost_budget_units}`
    : "默认策略";
  byId("scheduling-policy-form").querySelectorAll("input, button").forEach((item) => {
    item.disabled = !project || !canPermission("projects:manage");
  });
}

async function saveSchedulingPolicy(event) {
  event.preventDefault();
  if (!currentProjectId) return;
  const message = byId("scheduling-policy-message");
  message.textContent = "正在保存";
  try {
    const updated = await request(`/api/projects/${encodeURIComponent(currentProjectId)}/scheduling-policy`, {
      method: "PUT",
      body: JSON.stringify({
        concurrency_limit: Number(byId("scheduling-concurrency").value),
        priority_weight: Number(byId("scheduling-priority").value),
        run_cost_budget_units: Number(byId("scheduling-cost-budget").value),
      }),
    });
    activeProject = updated;
    const index = registeredProjects.findIndex((item) => item.id === updated.id);
    if (index >= 0) registeredProjects[index] = updated;
    renderSchedulingPolicy(updated);
    message.textContent = "运行策略已保存，将用于之后生成的新计划";
  } catch (error) {
    message.textContent = error.message;
  }
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
    const responseText = await response.text();
    let payload = {};
    try {
      payload = responseText ? JSON.parse(responseText) : {};
    } catch (_error) {
      payload = {};
    }
    const rawDetail = payload.detail || `HTTP ${response.status}`;
    const detail = Array.isArray(rawDetail)
      ? rawDetail.map((item) => item?.msg || item?.detail || String(item)).join("；")
      : typeof rawDetail === "object" ? rawDetail.message || JSON.stringify(rawDetail) : rawDetail;
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
  renderSchedulingPolicy(active);
  await Promise.all([
    loadCurrentProjectContract(),
    loadCurrentProductSpec(),
    loadProjectPreflight(),
  ]);
  if (productizationEnabled) {
    await window.loadCapabilityCenter?.(
      currentProjectId,
      currentProductSpecDetail?.product_spec,
      currentProjectContract,
    );
  }
  if (productizationEnabled) {
    await window.loadRevisionCenter?.(currentProjectId, currentRun);
  } else {
    await window.loadRevisionCenter?.(null, null);
  }
  refreshStartAction();
  window.dispatchEvent(new CustomEvent("taskhub:projects", {detail: data.projects}));
}

async function loadProductizationStatus() {
  const state = await request("/api/productization/status");
  productizationEnabled = Boolean(state.enabled);
  byId("product-spec-disclosure").classList.toggle("hidden", !productizationEnabled);
  byId("project-contract-disclosure").classList.toggle("hidden", !productizationEnabled);
  byId("capability-disclosure").classList.toggle("hidden", !productizationEnabled);
}

function refreshStartAction() {
  const button = byId("start");
  const repositoryReady = Boolean(activeProject?.repository_ready);
  const preflightReady = Boolean(currentProjectPreflight?.ready);
  if (!productizationEnabled) {
    button.textContent = preflightReady ? "开始流程" : "先修复项目预检";
    button.disabled = !repositoryReady || !preflightReady;
    return;
  }
  const spec = currentProductSpecDetail?.product_spec;
  const contract = currentProjectContract;
  button.textContent = !spec ? (canPermission("projects:manage") ? "生成产品规格" : "等待项目负责人生成规格")
    : spec.status !== "approved" ? "等待规格批准"
      : contract?.status !== "active" ? "等待项目契约生效" : "按批准规格开始流程";
  button.disabled = !repositoryReady || !preflightReady
    || (!spec && !canPermission("projects:manage"))
    || Boolean(spec && spec.status !== "approved")
    || Boolean(spec?.status === "approved" && contract?.status !== "active");
}

function renderProjectPreflight(report) {
  currentProjectPreflight = report || null;
  const state = byId("project-preflight-state");
  const checks = byId("project-preflight-checks");
  const summary = byId("project-preflight-summary");
  if (!report) {
    state.textContent = "等待项目";
    state.className = "card-state warn";
    byId("project-preflight-detail").textContent = "选择项目后统一检查启动开发所需条件。";
    summary.innerHTML = "";
    checks.innerHTML = "";
    byId("refresh-project-preflight").disabled = true;
    refreshStartAction();
    return;
  }
  const values = report.summary || {};
  state.textContent = report.ready ? "可以启动" : `${values.failed || 0} 项阻塞`;
  state.className = `card-state ${report.ready ? "ok" : "bad"}`;
  byId("project-preflight-detail").textContent = report.ready
    ? "仓库、模型、执行与质量边界已满足。"
    : "请先处理阻塞项，再启动开发流程。";
  summary.innerHTML = [
    ["通过", values.passed || 0, "ok"],
    ["提醒", values.warnings || 0, "warn"],
    ["阻塞", values.failed || 0, "bad"],
  ].map(([label, value, tone]) => `<div class="${tone}"><strong>${value}</strong><span>${label}</span></div>`).join("");
  checks.innerHTML = (report.checks || []).map((item) => {
    const label = {passed: "通过", warning: "提醒", failed: "阻塞"}[item.status] || item.status;
    return `<article class="project-preflight-check ${escapeHtml(item.status)}">
      <span class="project-preflight-icon" aria-hidden="true">${item.status === "passed" ? "✓" : item.status === "warning" ? "!" : "×"}</span>
      <div><h3>${escapeHtml(item.title)}<small>${escapeHtml(label)}</small></h3>
        <p>${escapeHtml(item.detail)}</p>
        ${item.remediation ? `<p class="project-preflight-remediation">${escapeHtml(item.remediation)}</p>` : ""}
      </div>
      ${item.status === "failed" ? `<button type="button" class="secondary" data-preflight-target="${escapeHtml(item.target)}">去修复</button>` : ""}
    </article>`;
  }).join("");
  byId("refresh-project-preflight").disabled = false;
  refreshStartAction();
}

async function loadProjectPreflight() {
  if (!currentProjectId) {
    renderProjectPreflight(null);
    return;
  }
  byId("project-preflight-message").textContent = "正在检查项目启动条件";
  try {
    const report = await request(`/api/projects/${encodeURIComponent(currentProjectId)}/preflight`);
    renderProjectPreflight(report);
    byId("project-preflight-message").textContent = report.ready
      ? `预检通过 · ${new Date(report.checked_at).toLocaleTimeString()}`
      : "预检发现阻塞项";
  } catch (error) {
    currentProjectPreflight = null;
    byId("project-preflight-state").textContent = "检查失败";
    byId("project-preflight-state").className = "card-state bad";
    byId("project-preflight-message").textContent = error.message;
    refreshStartAction();
  }
}

function openPreflightTarget(target) {
  if (target === "model-services" || target === "nodes") {
    showPage("resources");
    const disclosure = byId(target === "model-services" ? "providers-disclosure" : "nodes-disclosure");
    disclosure.open = true;
    disclosure.scrollIntoView({behavior: "smooth", block: "start"});
    return;
  }
  const disclosure = byId(`${target}-disclosure`);
  if (disclosure) {
    disclosure.open = true;
    disclosure.scrollIntoView({behavior: "smooth", block: "start"});
  }
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
  await window.loadCapabilityCenter?.(
    currentProjectId, currentProductSpecDetail?.product_spec, currentProjectContract,
  );
  byId("project-contract-message").textContent = "契约草稿已生成，请核对后提交评审";
}

async function transitionProjectContract(action) {
  const contract = currentProjectContract;
  if (!contract) return;
  await request(`/api/projects/${encodeURIComponent(currentProjectId)}/project-contracts/${encodeURIComponent(contract.contract_id)}/versions/${contract.version}/${action}`, {method: "POST"});
  await loadCurrentProjectContract();
  await loadProjectPreflight();
  await window.loadCapabilityCenter?.(
    currentProjectId, currentProductSpecDetail?.product_spec, currentProjectContract,
  );
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
  await loadProjectPreflight();
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
  await window.loadCapabilityCenter?.(
    currentProjectId, currentProductSpecDetail?.product_spec, currentProjectContract,
  );
  byId("product-spec-message").textContent = detail.decision
    ? "规格草稿已生成，请集中完成待决策事项" : "规格草稿已生成，可以提交评审";
}

async function transitionProductSpec(action) {
  const spec = currentProductSpecDetail?.product_spec;
  if (!spec) return;
  await request(`/api/product-specs/${encodeURIComponent(spec.spec_id)}/versions/${spec.version}/${action}?project_id=${encodeURIComponent(currentProjectId)}`, {method: "POST"});
  await loadCurrentProductSpec();
  await window.loadCapabilityCenter?.(
    currentProjectId, currentProductSpecDetail?.product_spec, currentProjectContract,
  );
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
  await window.loadCapabilityCenter?.(
    currentProjectId, currentProductSpecDetail?.product_spec, currentProjectContract,
  );
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
  const formatCommands = (commands) => (commands || []).map((command) =>
    command.map((argument) => /[\s"'\\]/.test(argument) ? JSON.stringify(argument) : argument).join(" ")
  ).join("\n");
  byId("project-quality-tests").value = formatCommands(project?.test_commands);
  byId("project-quality-acceptance").value = formatCommands(project?.acceptance_commands);
  byId("project-quality-timeout").value = project?.test_timeout_seconds || 600;
  byId("project-quality-test-database").checked = Boolean(project?.test_database);
  const windowsSuite = project?.windows_test_suite;
  byId("project-quality-windows-commands").value = formatCommands(windowsSuite?.commands);
  byId("project-quality-windows-nodes").value = (windowsSuite?.node_ids || []).sort().join("\n");
  byId("project-quality-windows-artifacts").value = (windowsSuite?.artifact_paths || []).join("\n");
  byId("project-quality-windows-browser").checked = Boolean(windowsSuite?.required_capabilities?.includes("playwright"));
  const allowed = Boolean(project && canPermission("projects:manage"));
  for (const id of ["project-repository-url", "project-repository-remote", "project-repository-branch", "check-project-repository", "save-project-repository", "project-quality-tests", "project-quality-acceptance", "project-quality-timeout", "project-quality-test-database", "project-quality-windows-commands", "project-quality-windows-nodes", "project-quality-windows-artifacts", "project-quality-windows-browser", "save-project-quality"]) {
    byId(id).disabled = !allowed;
  }
  byId("project-repository-message").textContent = "";
  byId("project-quality-message").textContent = "";
}

async function saveProjectQuality() {
  if (!currentProjectId) return;
  const button = byId("save-project-quality");
  button.disabled = true;
  byId("project-quality-message").textContent = "正在保存质量配置";
  try {
    const project = await request(`/api/projects/${encodeURIComponent(currentProjectId)}/quality`, {
      method: "PUT",
      body: JSON.stringify({
        test_commands: byId("project-quality-tests").value,
        acceptance_commands: byId("project-quality-acceptance").value,
        test_timeout_seconds: Number(byId("project-quality-timeout").value),
        test_database: byId("project-quality-test-database").checked,
        windows_test_commands: byId("project-quality-windows-commands").value,
        windows_test_node_ids: byId("project-quality-windows-nodes").value,
        windows_test_artifact_paths: byId("project-quality-windows-artifacts").value,
        windows_test_browser: byId("project-quality-windows-browser").checked,
      }),
    });
    await loadProjects(project.id);
    byId("project-quality-message").textContent = "质量配置已保存，项目预检已刷新";
  } catch (error) {
    byId("project-quality-message").textContent = error.message;
  } finally {
    button.disabled = !canPermission("projects:manage");
  }
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
      remote_url: byId("project-remote-url").value.trim(),
      local_path: byId("project-local-path").value.trim(),
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

function applyProjectLocationDefaults() {
  const projectId = byId("project-id").value;
  if (!projectId || !projectProvisioningDefaults.authority_url_prefix) return;
  const remote = `${projectProvisioningDefaults.authority_url_prefix}/${projectId}.git`;
  const local = `${projectProvisioningDefaults.managed_repository_root}/${projectId}`;
  for (const [id, value] of [["project-remote-url", remote], ["project-local-path", local]]) {
    const input = byId(id);
    if (!input.value || input.value === input.dataset.suggestedValue) input.value = value;
    input.dataset.suggestedValue = value;
  }
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
      remote_url: byId("attach-project-remote-url").value.trim(),
      local_path: byId("attach-project-local-path").value.trim(),
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
    projectProvisioningDefaults = data.defaults || {};
    const service = projectProvisioningDefaults.authority_service || "尚未配置";
    byId("project-git-service").textContent = service;
    byId("attach-project-git-service").textContent = service;
    applyProjectLocationDefaults();
    select.innerHTML = data.repositories.length
      ? data.repositories.map((item) => `<option value="${escapeHtml(item.repository)}"
          data-name="${escapeHtml(item.name)}" data-branch="${escapeHtml(item.default_branch)}"
          data-remote-url="${escapeHtml(item.remote_url)}" data-local-path="${escapeHtml(item.local_path)}">
          ${escapeHtml(item.name)}${item.attached ? "（已接入）" : ""}
        </option>`).join("")
      : `<option value="">${data.discovery_error ? "仓库发现失败，可手工填写" : "Git 仓库中没有项目"}</option>`;
    select.disabled = data.repositories.length === 0;
    byId("attach-project").disabled = false;
    message.textContent = data.discovery_error || "";
    applySelectedRepository();
  } catch (error) {
    select.innerHTML = '<option value="">读取失败</option>';
    byId("attach-project").disabled = false;
    message.textContent = error.message;
  }
}

function openGitServiceSettings() {
  showPage("resources");
  const platform = byId("platform-disclosure");
  const configuration = byId("platform-config-disclosure");
  platform.open = true;
  configuration.open = true;
  window.setTimeout(() => {
    byId("platform-git-service-card").scrollIntoView({behavior: "smooth", block: "start"});
    byId("platform-git-host").focus();
  }, 100);
}

function applySelectedRepository() {
  const option = byId("attach-project-repository").selectedOptions[0];
  if (!option?.value) return;
  byId("attach-project-name").value = option.dataset.name;
  byId("attach-project-branch").value = option.dataset.branch || "main";
  byId("attach-project-remote-url").value = option.dataset.remoteUrl || "";
  byId("attach-project-local-path").value = option.dataset.localPath || "";
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
  for (const id of ["container-form", "configure-project-git-service", "configure-attach-git-service"]) {
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
  const endpoint = "resume";
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
  loadAvailableProjects();
});
byId("close-project-form").addEventListener("click", () => byId("project-form").classList.add("hidden"));
byId("show-attach-project-form").addEventListener("click", () => {
  byId("project-form").classList.add("hidden");
  byId("attach-project-form").classList.remove("hidden");
  loadAvailableProjects();
});
byId("configure-project-git-service").addEventListener("click", openGitServiceSettings);
byId("configure-attach-git-service").addEventListener("click", openGitServiceSettings);
byId("close-attach-project-form").addEventListener("click", () => byId("attach-project-form").classList.add("hidden"));
byId("project-form").addEventListener("submit", createProject);
byId("project-name").addEventListener("input", () => {
  byId("project-id").value = generatedProjectId(byId("project-name").value);
  applyProjectLocationDefaults();
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
byId("save-project-quality").addEventListener("click", saveProjectQuality);
byId("scheduling-policy-form").addEventListener("submit", saveSchedulingPolicy);
byId("execution-task-previous").addEventListener("click", () => {
  if (executionPlanPage <= 1 || !currentRun) return;
  executionPlanPage -= 1;
  loadExecutionPlan(currentRun).catch(() => {});
});
byId("execution-task-next").addEventListener("click", () => {
  if (!currentRun) return;
  executionPlanPage += 1;
  loadExecutionPlan(currentRun).catch(() => { executionPlanPage -= 1; });
});
byId("check-project-repository").addEventListener("click", checkProjectRepository);
byId("refresh-project-preflight").addEventListener("click", () => {
  loadProjectPreflight().catch(() => {});
});
byId("project-preflight-checks").addEventListener("click", (event) => {
  const button = event.target.closest("[data-preflight-target]");
  if (button) openPreflightTarget(button.dataset.preflightTarget);
});
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
