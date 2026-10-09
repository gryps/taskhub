const containerRoleNames = {
  execution: "执行节点", test: "测试节点", preproduction: "预生产节点",
};

function containerActions(item) {
  const nodeId = escapeHtml(item.node_id);
  const primary = item.state === "running"
    ? `<button type="button" class="secondary container-action" data-node-id="${nodeId}" data-action="stop">停止</button>`
    : `<button type="button" class="secondary container-action" data-node-id="${nodeId}" data-action="start">启动</button>`;
  const upgrade = item.upgrade_available
    ? `<button type="button" class="secondary container-action" data-node-id="${nodeId}"
        data-action="upgrade">升级镜像</button>`
    : "";
  return `<div class="container-actions">${primary}${upgrade}
    <button type="button" class="secondary container-action remove-container"
      data-node-id="${nodeId}" data-action="remove">移除</button></div>`;
}

function containerCard(item) {
  const needsUpgrade = Boolean(item.upgrade_available);
  const stateClass = needsUpgrade
    ? "warn" : item.state === "running" ? "ok" : item.state === "error" ? "bad" : "warn";
  const stateLabel = needsUpgrade ? "镜像待升级" : item.state;
  const desiredImage = needsUpgrade
    ? `<div class="wide"><dt>目标镜像</dt><dd class="mono-value">${escapeHtml(item.desired_image)}</dd></div>`
    : "";
  return `<article class="management-card ${item.state === "running" ? "ready" : "blocked"}">
    <header><div><h3>${escapeHtml(item.node_id)}</h3><p>${escapeHtml(item.name)} · Seed 本机</p></div>
      <span class="card-state ${stateClass}">${escapeHtml(stateLabel)}</span></header>
    <dl class="management-card-facts">
      <div><dt>节点角色</dt><dd>${escapeHtml(containerRoleNames[item.role] || item.role)}</dd></div>
      <div><dt>部署位置</dt><dd>Seed 本机</dd></div>
      <div class="wide"><dt>当前镜像</dt><dd class="mono-value">${escapeHtml(item.image || "未记录")}</dd></div>
      ${desiredImage}
      <div class="wide"><dt>运行状态</dt><dd><small>${escapeHtml(item.status || item.state)}</small></dd></div>
    </dl><footer>${containerActions(item)}</footer></article>`;
}

async function loadContainers() {
  byId("container-summary").textContent = "正在读取 Docker Engine";
  try {
    const status = await request("/api/containers/status");
    const localData = status.available ? await request("/api/containers") : {containers: []};
    const containers = localData.containers;
    const diagnosticSelect = byId("diagnostic-node");
    const selectedDiagnostic = diagnosticSelect.value;
    diagnosticSelect.innerHTML = '<option value="">请选择节点</option>' + containers.map((item) =>
      `<option value="${escapeHtml(item.node_id)}">${escapeHtml(item.node_id)} · ${escapeHtml(containerRoleNames[item.role] || item.role)}</option>`).join("");
    if ([...diagnosticSelect.options].some((item) => item.value === selectedDiagnostic)) {
      diagnosticSelect.value = selectedDiagnostic;
    }
    byId("container-form").classList.toggle("hidden", !status.available);
    const running = containers.filter((item) => item.state === "running").length;
    const upgrades = containers.filter((item) => item.upgrade_available).length;
    byId("container-summary").textContent =
      `${running}/${containers.length} 运行${upgrades ? ` · ${upgrades} 个待升级` : ""} · Seed Docker ${status.available ? "可用" : "不可用"}`;
    byId("managed-containers").innerHTML = containers.map(containerCard).join("") ||
      '<p class="management-empty">尚未创建节点容器，请展开“创建节点”进行配置。</p>';
  } catch (error) {
    byId("container-summary").textContent = error.message;
  }
}

function diagnosticEventLines(events) {
  return (events || []).map((item) =>
    `${item.created_at || "—"}  ${item.event || item.operation || "事件"}  ${item.result || ""}`
  ).join("\n") || "尚无记录";
}

function diagnosticPercent(value) {
  return value === null || value === undefined ? "—" : `${value}%`;
}

async function loadNodeDiagnostics() {
  const nodeId = byId("diagnostic-node").value;
  if (!nodeId) {
    byId("diagnostic-message").textContent = "请先选择工作节点";
    return;
  }
  const button = byId("load-node-diagnostics");
  button.disabled = true;
  byId("diagnostic-message").textContent = `正在读取 ${nodeId}`;
  try {
    const data = await request(`/api/diagnostics/nodes/${encodeURIComponent(nodeId)}`);
    const resources = data.resources || {};
    byId("node-diagnostics").innerHTML = `
      <section class="diagnostic-card"><h4>资源与槽位</h4><div class="diagnostic-metrics">
        <span>CPU<strong>${diagnosticPercent(resources.cpu_percent)}</strong></span>
        <span>内存<strong>${diagnosticPercent(resources.memory_used_percent)}</strong></span>
        <span>磁盘<strong>${diagnosticPercent(resources.disk_used_percent)}</strong></span>
        <span>槽位<strong>${Number(data.active_jobs || 0)}/${Number(data.slots || 1)}</strong></span>
      </div><p class="${data.last_error ? "bad" : "ok"}">${escapeHtml(data.last_error || "节点未报告错误")}</p></section>
      <section class="diagnostic-card"><h4>Agent 日志</h4><pre>${escapeHtml(diagnosticEventLines(data.agent_logs))}</pre></section>
      <section class="diagnostic-card"><h4>容器最近日志</h4><pre>${escapeHtml(data.container_logs || "尚无容器日志")}</pre></section>
      <section class="diagnostic-card"><h4>Docker 操作</h4><pre>${escapeHtml(diagnosticEventLines(data.operations))}</pre></section>`;
    byId("diagnostic-message").textContent = `${nodeId} 诊断已更新`;
  } catch (error) {
    byId("diagnostic-message").textContent = error.message;
  } finally { button.disabled = false; }
}

async function createContainer(event) {
  event.preventDefault();
  const button = byId("create-container");
  button.disabled = true;
  byId("container-message").textContent = "正在创建并启动容器";
  try {
    const payload = {
      node_id: byId("container-node-id").value,
      role: byId("container-role").value,
      slots: Number(byId("container-slots").value),
    };
    const result = await request("/api/containers", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    byId("container-message").textContent = `${result.node_id} 已在 Seed 本机创建并加入调度`;
    byId("container-node-id").value = "";
    await Promise.all([loadContainers(), loadNodes()]);
  } catch (error) {
    byId("container-message").textContent = error.message;
  } finally { button.disabled = false; }
}

async function runContainerAction(nodeId, action, button) {
  if (action === "remove" &&
      !window.confirm(`确认移除节点容器 ${nodeId}？节点数据卷将保留。`)) return;
  if (action === "upgrade" &&
      !window.confirm(`确认升级节点 ${nodeId}？容器会短暂重启，数据卷和节点身份将保留。`)) return;
  const verbs = {start: "启动", stop: "停止", remove: "移除", upgrade: "升级"};
  button.disabled = true;
  byId("container-message").textContent = `正在${verbs[action]} ${nodeId}`;
  try {
    const result = await request(
      `/api/containers/${encodeURIComponent(nodeId)}/${action}`,
      {method: "POST"},
    );
    byId("container-message").textContent = result.detail || `${nodeId} 操作完成`;
    await Promise.all([loadContainers(), loadNodes()]);
  } catch (error) {
    byId("container-message").textContent = error.message;
    button.disabled = false;
  }
}

byId("load-node-diagnostics").addEventListener("click", loadNodeDiagnostics);
byId("container-form").addEventListener("submit", createContainer);
byId("managed-containers").addEventListener("click", (event) => {
  const button = event.target.closest(".container-action");
  if (button) runContainerAction(button.dataset.nodeId, button.dataset.action, button);
});
