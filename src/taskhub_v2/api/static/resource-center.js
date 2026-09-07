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

async function loadResources() {
  await Promise.all([loadSystemConfig(), loadProviders(), loadNodes()]);
}

byId("refresh-system").addEventListener("click", loadSystemConfig);
byId("refresh-providers").addEventListener("click", loadProviders);
byId("refresh-nodes").addEventListener("click", loadNodes);
window.loadResources = loadResources;
