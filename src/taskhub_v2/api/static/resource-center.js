function usageMetric(metric) {
  if (metric.used_percent !== undefined) {
    const used = Math.max(0, Math.min(100, Number(metric.used_percent)));
    const reset = metric.resets_at
      ? ` · ${new Date(metric.resets_at * 1000).toLocaleString()} 重置` : "";
    const remaining = metric.remaining_percent !== undefined
      ? ` · 剩余 ${Number(metric.remaining_percent)}%` : "";
    return `<div class="usage-metric">
      <div><span>${escapeHtml(metric.label)}</span><strong>${used}%</strong></div>
      <progress max="100" value="${used}">${used}%</progress>
      <small>已使用 ${used}%${escapeHtml(remaining)}${escapeHtml(reset)}</small>
    </div>`;
  }
  return `<div class="balance-metric"><span>${escapeHtml(metric.label)}</span>
    <strong>${escapeHtml(metric.value)} ${escapeHtml(metric.unit || "")}</strong></div>`;
}

function roleModelsView(item) {
  const roleNames = {planner: "规划", coder: "施工", reviewer: "审查",
    risk: "风险", supervisor: "监督"};
  const models = Object.entries(item.role_models || {});
  if (!models.length) return escapeHtml(item.model);
  return models.map(([role, model]) => {
    const display = model === "account_default" ? "Codex 账号默认模型" : model;
    return `<span class="role-model"><small>${escapeHtml(roleNames[role] || role)}</small>${escapeHtml(display)}</span>`;
  }).join("");
}

function billingView(billing) {
  if (!billing || billing.status !== "available") {
    return `<span class="usage-unavailable">${escapeHtml(billing?.detail || "暂无计费数据")}</span>`;
  }
  return (billing.metrics || []).map(usageMetric).join("") ||
    '<span class="usage-unavailable">暂无计费数据</span>';
}

function checkStatusClass(status) {
  return status === "pass" ? "ok" : status === "warn" ? "warn" : "bad";
}

function checkStatusLabel(status) {
  return {pass: "通过", warn: "注意", fail: "失败"}[status] || status;
}

function renderCheckRows(title, checks) {
  return `<div class="resource-row check-group"><strong>${escapeHtml(title)}</strong>
    <span>${checks.filter((item) => item.status === "pass").length}/${checks.length} 通过</span>
    <span></span><span></span></div>${checks.map((item) => `
      <div class="resource-row check-row">
        <strong>${escapeHtml(item.name)}<small>${escapeHtml(item.category)}</small></strong>
        <span class="${checkStatusClass(item.status)}">${escapeHtml(checkStatusLabel(item.status))}</span>
        <span>${escapeHtml(item.detail)}<small>${escapeHtml(item.expected || "")}</small></span>
        <span>${escapeHtml(item.recommendation || item.actual || "—")}</span>
      </div>`).join("")}`;
}

function prerequisiteAction(action) {
  if (!action?.command) return "—";
  const command = escapeHtml(action.command);
  return `<div class="prerequisite-action"><code>${command}</code>
    <button type="button" class="icon-button copy-prerequisite"
      data-command="${command}" title="复制${escapeHtml(action.label)}命令"
      aria-label="复制${escapeHtml(action.label)}命令">⧉</button></div>`;
}

async function loadSystemConfig() {
  byId("system-summary").textContent = "正在读取系统版本、组件与前置条件";
  try {
    const data = await request("/api/system/config");
    const controllerStatus = checkStatusLabel(data.status);
    const nodeChecks = (data.nodes || []).flatMap((node) => node.system?.checks || []);
    const failed = [...data.controller.checks, ...nodeChecks]
      .filter((item) => item.status === "fail").length;
    byId("system-summary").textContent = `控制器 ${controllerStatus} · ${failed} 个失败项`;
    const nodeRows = (data.nodes || []).map((node) => {
      const title = `${node.node_id} · ${node.kind} · ${node.status}`;
      const checks = node.system?.checks || [];
      return checks.length
        ? renderCheckRows(title, checks)
        : `<div class="resource-row check-group"><strong>${escapeHtml(title)}</strong>
          <span class="warn">未上报</span><span></span><span>${escapeHtml(node.detail || "节点版本不支持系统检测")}</span></div>`;
    }).join("");
    byId("system-checks").innerHTML = `<div class="resource-row resource-header">
      <span>检测项</span><span>结果</span><span>当前状态</span><span>建议 / 路径</span>
    </div>${renderCheckRows(`${data.controller.host} · ${data.controller.role}`, data.controller.checks)}${nodeRows}`;
  } catch (error) {
    byId("system-summary").textContent = error.message;
  }
}

async function loadProviders() {
  byId("provider-summary").textContent = "正在读取模型状态与计费信息";
  try {
    const data = await request("/api/providers");
    const ready = data.providers.filter((item) => item.configured).length;
    byId("provider-summary").textContent = `${ready}/${data.providers.length} 可用 · OpenAI 走代理 · 其他模型直连`;
    byId("providers").innerHTML = `<div class="resource-row resource-header">
      <span>模型资源</span><span>状态</span><span>模型 / 路由</span><span>计费信息</span>
    </div>${data.providers.map((item) => `
      <div class="resource-row">
        <strong>${escapeHtml(item.id)}</strong>
        <span class="${item.configured ? "ok" : "bad"}">${escapeHtml(item.status)}</span>
        <span class="model-cell">${roleModelsView(item)}<small>网络：${escapeHtml(item.route)}</small></span>
        <div class="usage-cell">${billingView(item.billing)}</div>
      </div>`).join("")}`;
  } catch (error) {
    byId("provider-summary").textContent = error.message;
  }
}

async function loadNodes() {
  try {
    const data = await request("/api/nodes");
    const ready = data.nodes.filter((item) => item.status === "ok").length;
    byId("node-summary").textContent = `${ready}/${data.nodes.length} 在线`;
    byId("nodes").innerHTML = `<div class="resource-row node-row resource-header">
      <span>执行节点</span><span>状态</span><span>负载</span><span>能力</span>
    </div>${data.nodes.map((item) => `
      <div class="resource-row node-row">
        <strong>${escapeHtml(item.node_id)}</strong>
        <span class="${item.status === "ok" ? "ok" : "bad"}">${escapeHtml(item.status)}</span>
        <span>${escapeHtml(item.kind)} · ${item.active}/${item.slots}<small>${(item.workloads || []).map(escapeHtml).join(" · ")}</small></span>
        <span>${item.status === "ok"
          ? Object.entries(item.capabilities || {}).filter(([, value]) => value).map(([name]) => escapeHtml(name)).join(" · ")
          : escapeHtml(item.detail || "节点不可达")}</span>
      </div>`).join("")}`;
  } catch (error) {
    byId("node-summary").textContent = error.message;
  }
}

function formatBytes(value) {
  if (value === null || value === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = Number(value);
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount.toFixed(unit > 2 ? 1 : 0)} ${units[unit]}`;
}

function loadMetric(label, percent, detail) {
  const available = percent !== null && percent !== undefined;
  const value = available ? Math.max(0, Math.min(100, Number(percent))) : 0;
  return `<div class="usage-metric">
    <div><span>${escapeHtml(label)}</span><strong>${available ? `${value}%` : "—"}</strong></div>
    <progress max="100" value="${value}">${value}%</progress>
    <small>${escapeHtml(detail)}</small>
  </div>`;
}

async function loadNodeLoad() {
  byId("load-summary").textContent = "正在读取近 5 分钟 CPU、内存和磁盘峰值";
  try {
    const data = await request("/api/nodes");
    const online = data.nodes.filter((item) => item.status === "ok");
    const busy = online.filter((item) => Number(item.load?.cpu_percent || 0) >= 85).length;
    byId("load-summary").textContent = `${online.length}/${data.nodes.length} 在线 · ${busy} 个高负载 · 5 分钟峰值`;
    byId("node-load").innerHTML = `<div class="resource-row load-row resource-header">
      <span>节点</span><span>CPU</span><span>内存</span><span>磁盘</span>
    </div>${data.nodes.map((item) => {
      const load = item.load || {};
      const offline = item.status !== "ok";
      return `<div class="resource-row load-row">
        <strong>${escapeHtml(item.node_id)}<small>${escapeHtml(offline ? item.detail || "节点不可达" : `${item.cpu_count || "—"} 核 · ${item.active}/${item.slots} 任务`)}</small></strong>
        ${offline ? '<span class="bad">离线</span><span>—</span><span>—</span>' : `
        ${loadMetric("CPU 峰值", load.cpu_percent, load.load_average_1m === null || load.load_average_1m === undefined ? "1 分钟负载不可用" : `1 分钟负载峰值 ${load.load_average_1m}`)}
        ${loadMetric("内存峰值", load.memory_used_percent, `当前可用 ${formatBytes(load.memory_available_bytes)} / ${formatBytes(load.memory_total_bytes)}`)}
        ${loadMetric("磁盘峰值", load.disk_used_percent, `当前可用 ${formatBytes(load.disk_free_bytes)} / ${formatBytes(load.disk_total_bytes)}`)}`}
      </div>`;
    }).join("")}`;
  } catch (error) {
    byId("load-summary").textContent = error.message;
  }
}

async function loadAcceptancePrerequisites() {
  byId("acceptance-prerequisites-summary").textContent = "正在检测测试数据库与浏览器授权";
  try {
    const data = await request("/api/system/config");
    const rows = (data.nodes || []).map((node) => {
      const database = node.test_database || {};
      const browser = node.browser_prerequisites || {};
      const databaseStatus = database.available ? "pass" : database.configured ? "fail" : "warn";
      const browserRelevant = (node.workloads || []).includes("browser_acceptance");
      const browserStatus = !browserRelevant ? "pass" : browser.authenticated ? "pass" : "fail";
      return `
        <div class="resource-row check-row">
          <strong>${escapeHtml(node.node_id)}<small>测试数据库</small></strong>
          <span class="${checkStatusClass(databaseStatus)}">${checkStatusLabel(databaseStatus)}</span>
          <span>${escapeHtml(database.detail || "未配置")}</span>
          <span>${escapeHtml((database.environment_names || []).join(" · ") || "TASKHUB_TEST_DATABASE_ADMIN_DSN")}</span>
        </div>
        <div class="resource-row check-row">
          <strong>${escapeHtml(node.node_id)}<small>浏览器 Profile / 授权</small></strong>
          <span class="${checkStatusClass(browserStatus)}">${checkStatusLabel(browserStatus)}</span>
          <span>${escapeHtml(browserRelevant ? browser.detail || "未上报" : "该节点不承担浏览器验收")}</span>
          <div>${prerequisiteAction(browser.action)}</div>
        </div>`;
    }).join("");
    const databaseReady = (data.nodes || []).filter((node) => node.test_database?.available).length;
    const browserNodes = (data.nodes || []).filter((node) => (node.workloads || []).includes("browser_acceptance"));
    const browserReady = browserNodes.filter((node) => node.browser_prerequisites?.authenticated).length;
    byId("acceptance-prerequisites-summary").textContent =
      `测试数据库 ${databaseReady} 个就绪 · 浏览器授权 ${browserReady}/${browserNodes.length} 就绪`;
    byId("acceptance-prerequisites").innerHTML = `<div class="resource-row resource-header">
      <span>节点 / 配置</span><span>结果</span><span>状态</span><span>目标 / 环境变量</span>
    </div>${rows}`;
  } catch (error) {
    byId("acceptance-prerequisites-summary").textContent = error.message;
  }
}

async function loadResources() {
  const sections = [
    ["system-disclosure", loadSystemConfig],
    ["providers-disclosure", loadProviders],
    ["nodes-disclosure", loadNodes],
    ["load-disclosure", loadNodeLoad],
    ["acceptance-prerequisites-disclosure", loadAcceptancePrerequisites],
  ];
  await Promise.all(sections.filter(([id]) => byId(id).open).map(([, load]) => load()));
}

function refreshWhenExpanded(id, load) {
  byId(id).addEventListener("toggle", (event) => {
    if (event.currentTarget.open) load();
  });
}

byId("refresh-system").addEventListener("click", loadSystemConfig);
byId("refresh-providers").addEventListener("click", loadProviders);
byId("refresh-nodes").addEventListener("click", loadNodes);
byId("refresh-load").addEventListener("click", loadNodeLoad);
refreshWhenExpanded("system-disclosure", loadSystemConfig);
refreshWhenExpanded("providers-disclosure", loadProviders);
refreshWhenExpanded("nodes-disclosure", loadNodes);
refreshWhenExpanded("load-disclosure", loadNodeLoad);
byId("refresh-acceptance-prerequisites").addEventListener("click", loadAcceptancePrerequisites);
refreshWhenExpanded("acceptance-prerequisites-disclosure", loadAcceptancePrerequisites);
byId("acceptance-prerequisites").addEventListener("click", async (event) => {
  const button = event.target.closest(".copy-prerequisite");
  if (!button) return;
  const command = button.dataset.command || "";
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(command);
  } else {
    const input = document.createElement("textarea");
    input.value = command;
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    input.remove();
  }
  button.textContent = "✓";
  setTimeout(() => { button.textContent = "⧉"; }, 1200);
});
window.loadResources = loadResources;
