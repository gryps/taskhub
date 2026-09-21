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
let exceptionPage = 1;
let exceptionRequest = 0;
let evidencePage = 1;
let evidenceRequest = 0;

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
  loadExceptionCenter().catch((error) => {
    byId("exception-message").textContent = error.message;
  });
  loadEvidenceCenter().catch((error) => {
    byId("evidence-message").textContent = error.message;
  });
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

const evidenceStateLabels = {verified: "证据完整", collecting: "持续采集", missing: "存在缺口", failed: "验证失败"};
const evidenceGateLabels = {implementation: "实施记录", acceptance: "验收证据", supervision: "监督结论", publication: "发布记录"};

async function loadEvidenceCenter() {
  const requestId = ++evidenceRequest;
  const params = new URLSearchParams({page: evidencePage, page_size: 10});
  const projectId = byId("filter-project").value.trim();
  if (projectId) params.set("project_id", projectId);
  const report = await request(`/api/evidence?${params}`);
  if (requestId !== evidenceRequest) return;
  const summary = report.summary || {};
  const attention = (summary.missing || 0) + (summary.failed || 0);
  byId("evidence-center-state").textContent = attention ? `${attention} 项需补齐` : (report.total ? "链路正常" : "暂无任务");
  byId("evidence-center-state").className = `card-state ${attention ? "bad" : report.total ? "ok" : "warn"}`;
  byId("evidence-summary").innerHTML = [
    ["完整", summary.verified || 0, "ok"],
    ["采集中", summary.collecting || 0, "warn"],
    ["有缺口", summary.missing || 0, "bad"],
    ["失败", summary.failed || 0, "critical"],
  ].map(([label, value, tone]) => `<div class="${tone}"><strong>${value}</strong><span>${label}</span></div>`).join("");
  byId("evidence-items").innerHTML = report.items.length ? report.items.map((item) => {
    const counts = item.counts || {};
    const completeness = Math.round((item.completeness || 0) * 100);
    const integrity = item.integrity === null ? "暂无制品" : `${Math.round(item.integrity * 100)}%`;
    const missing = [
      ...(item.missing_evidence || []).map((entry) => entry.label),
      ...(item.missing_gates || []).map((gate) => evidenceGateLabels[gate] || gate),
      ...(item.invalid_artifacts || []).map((name) => `摘要未验证：${name}`),
    ];
    const lineage = (item.lineage || []).map((entry) => `<li><span class="evidence-line-state ${entry.status}">${entry.status === "passed" ? "通过" : "失败"}</span><strong>${escapeHtml(entry.source)}</strong><span>${escapeHtml(entry.summary)}</span>${entry.commit ? `<code>${escapeHtml(entry.commit)}</code>` : ""}</li>`).join("");
    const artifacts = (item.artifacts || []).map((artifact) => `<li><span class="evidence-artifact-state ${artifact.verified ? "verified" : "invalid"}">${artifact.verified ? "已校验" : "未校验"}</span>${artifact.download_url ? `<a href="${escapeHtml(artifact.download_url)}" target="_blank" rel="noopener">${escapeHtml(artifact.name)}</a>` : `<span>${escapeHtml(artifact.name)}</span>`}<code>${escapeHtml(artifact.sha256.slice(0, 12))}</code></li>`).join("");
    return `<article class="evidence-item state-${escapeHtml(item.state)}">
      <header><div><span>${escapeHtml(item.project_id)}</span><strong>${escapeHtml(item.requirement)}</strong></div><span class="evidence-state">${escapeHtml(evidenceStateLabels[item.state] || item.state)}</span></header>
      <div class="evidence-metrics"><span><strong>${completeness}%</strong> 流程完整度</span><span><strong>${counts.passed_tests || 0}/${counts.tests || 0}</strong> 测试通过</span><span><strong>${counts.passed_evidence || 0}/${counts.evidence || 0}</strong> 验收通过</span><span><strong>${integrity}</strong> 制品完整性</span></div>
      ${missing.length ? `<p class="evidence-gaps">缺口：${escapeHtml(missing.join("、"))}</p>` : ""}
      <details><summary>查看来源链路与摘要</summary>${lineage ? `<ol class="evidence-lineage">${lineage}</ol>` : '<p class="empty-state">尚未形成证据来源。</p>'}${artifacts ? `<ul class="evidence-artifacts">${artifacts}</ul>` : ""}</details>
      <footer><small>${new Date(item.updated_at).toLocaleString()}</small><button type="button" class="secondary" data-evidence-run="${escapeHtml(item.run_id)}">打开任务证据</button></footer>
    </article>`;
  }).join("") : '<p class="empty-state evidence-empty">当前没有可汇总的任务证据。</p>';
  byId("evidence-message").textContent = `第 ${report.page} / ${Math.max(1, Math.ceil(report.total / report.page_size))} 页`;
  byId("previous-evidence").disabled = report.page <= 1;
  byId("next-evidence").disabled = report.page * report.page_size >= report.total;
  byId("evidence-items").querySelectorAll("[data-evidence-run]").forEach((button) => {
    button.addEventListener("click", () => openTask(button.dataset.evidenceRun));
  });
}

async function loadExceptionCenter() {
  const requestId = ++exceptionRequest;
  const params = new URLSearchParams({page: exceptionPage, page_size: 20});
  const projectId = byId("filter-project").value.trim();
  if (projectId) params.set("project_id", projectId);
  const report = await request(`/api/exceptions?${params}`);
  if (requestId !== exceptionRequest) return;
  const summary = report.summary || {};
  const active = report.total > 0;
  byId("exception-center-state").textContent = active ? `${report.total} 项待处理` : "无异常";
  byId("exception-center-state").className = `card-state ${active ? "bad" : "ok"}`;
  byId("exception-summary").innerHTML = [
    ["等待决策", summary.waiting || 0, "warn"],
    ["流程阻塞", summary.blocked || 0, "bad"],
    ["执行失败", summary.failed || 0, "critical"],
    ["本页可恢复", summary.visible_recoverable || 0, "ok"],
  ].map(([label, value, tone]) => `<div class="${tone}"><strong>${value}</strong><span>${label}</span></div>`).join("");
  byId("exception-items").innerHTML = report.items.length ? report.items.map((item) => `
    <article class="exception-item severity-${escapeHtml(item.severity)}" tabindex="0" data-exception-run="${escapeHtml(item.run_id)}">
      <div class="exception-item-heading"><span>${escapeHtml(item.category_label)}</span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(statusLabels[item.status] || item.status)}</small></div>
      <p>${escapeHtml(item.detail)}</p>
      <footer><span>建议：${escapeHtml(item.recommended_action)}</span><button type="button" class="secondary">打开并处理</button></footer>
    </article>`).join("") : '<p class="empty-state exception-empty">当前没有等待、阻塞或失败任务。</p>';
  byId("exception-message").textContent = `第 ${report.page} / ${Math.max(1, Math.ceil(report.total / report.page_size))} 页`;
  byId("previous-exceptions").disabled = report.page <= 1;
  byId("next-exceptions").disabled = report.page * report.page_size >= report.total;
  byId("exception-items").querySelectorAll("[data-exception-run]").forEach((item) => {
    const open = () => openTask(item.dataset.exceptionRun);
    item.addEventListener("click", open);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter") open();
    });
  });
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
  ["filter-stage", "change"]].forEach(([id, event]) => byId(id).addEventListener(event, () => {
    taskPage = 1;
    exceptionPage = 1;
    evidencePage = 1;
    loadTaskCenter();
  }));
byId("refresh-tasks").addEventListener("click", loadTaskCenter);
byId("nav-tasks").addEventListener("click", () => showPage("tasks"));
byId("nav-workflow").addEventListener("click", () => showPage("workflow"));
byId("nav-resources").addEventListener("click", () => showPage("resources"));
window.loadTaskCenter = loadTaskCenter;
window.showTaskDetail = showTaskDetail;

byId("previous-tasks").addEventListener("click", () => { taskPage--; loadTaskCenter(); });
byId("next-tasks").addEventListener("click", () => { taskPage++; loadTaskCenter(); });
byId("previous-exceptions").addEventListener("click", () => {
  if (exceptionPage > 1) exceptionPage -= 1;
  loadExceptionCenter();
});
byId("next-exceptions").addEventListener("click", () => {
  exceptionPage += 1;
  loadExceptionCenter();
});
byId("previous-evidence").addEventListener("click", () => {
  if (evidencePage > 1) evidencePage -= 1;
  loadEvidenceCenter();
});
byId("next-evidence").addEventListener("click", () => {
  evidencePage += 1;
  loadEvidenceCenter();
});
