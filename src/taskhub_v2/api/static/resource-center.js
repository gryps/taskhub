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
  byId("system-summary").textContent = "正在读取 Seed 控制面运行条件";
  try {
    const data = await request("/api/system/config");
    const controllerStatus = checkStatusLabel(data.status);
    const failed = data.controller.checks
      .filter((item) => item.status === "fail").length;
    const online = (data.nodes || []).filter((node) => node.status === "ok").length;
    byId("system-summary").textContent =
      `控制面 ${controllerStatus} · 工作节点 ${online}/${(data.nodes || []).length} 在线 · ${failed} 个失败项`;
    byId("system-checks").innerHTML = `<div class="resource-row resource-header">
      <span>检测项</span><span>结果</span><span>当前状态</span><span>建议 / 路径</span>
    </div>${renderCheckRows(`${data.controller.host} · ${data.controller.role}`, data.controller.checks)}`;
  } catch (error) {
    byId("system-summary").textContent = error.message;
  }
}

function configurationState(data) {
  if (data.restart_required) return `v${data.version} 已保存 · 重启后生效`;
  if (data.version) return `v${data.version} 已生效`;
  return "使用部署默认值";
}

function fillValue(id, value) {
  byId(id).value = value === null || value === undefined ? "" : value;
}

function credentialState(metadata) {
  if (!metadata?.configured) return "未配置";
  const source = metadata.source === "managed" ? "Web 加密配置" : "部署环境";
  return `已配置 · ${source} · ${metadata.mask}`;
}

function renderConfigurationAudit(target, events) {
  const actionNames = {update: "保存配置", connection_test: "连接测试", apply: "启动生效"};
  const resultNames = {pending_restart: "等待重启", passed: "通过", failed: "失败", applied: "已生效"};
  const rows = events.map((item) => {
    const summary = item.parameter_summary || {};
    const fields = (summary.changed_fields || []).join(" · ") ||
      (summary.provider_id ? `服务：${summary.provider_id}` : `版本：${summary.version || "—"}`);
    const secrets = (summary.replaced_secrets || []).length
      ? `<small>已替换密钥：${summary.replaced_secrets.map(escapeHtml).join(" · ")}</small>` : "";
    const resultClass = item.result === "failed" ? "bad" :
      item.result === "pending_restart" ? "warn" : "ok";
    return `<div class="resource-row audit-row">
      <strong>${escapeHtml(actionNames[item.action] || item.action)}<small>${new Date(item.created_at).toLocaleString()}</small></strong>
      <span>${escapeHtml(item.operator)}</span><span>${escapeHtml(fields)}${secrets}</span>
      <span class="${resultClass}">${escapeHtml(resultNames[item.result] || item.result)}</span>
    </div>`;
  }).join("");
  byId(target).innerHTML = `<div class="resource-row audit-row resource-header">
    <span>操作 / 时间</span><span>操作者</span><span>参数摘要</span><span>结果</span>
  </div>${rows || '<div class="resource-row audit-row"><span>尚无配置操作</span><span>—</span><span>—</span><span>—</span></div>'}`;
}

function fillModelConfiguration(data) {
  const values = data.desired;
  fillValue("model-provider-mode", values.provider);
  fillValue("model-openai-proxy", values.openai_proxy_url);
  fillValue("model-openai-base", values.openai_base_url);
  fillValue("model-openai-name", values.openai_model);
  fillValue("model-gpt-base", values.gpt_base_url);
  fillValue("model-gpt-name", values.gpt_model);
  fillValue("model-gpt-planner", values.gpt_planner_model);
  fillValue("model-gpt-coder", values.gpt_coder_model);
  fillValue("model-gpt-supervisor", values.gpt_supervisor_model);
  fillValue("model-deepseek-base", values.deepseek_base_url);
  fillValue("model-deepseek-name", values.deepseek_model);
  fillValue("model-minimax-base", values.minimax_base_url);
  fillValue("model-minimax-name", values.minimax_model);
  ["openai", "gpt", "deepseek", "minimax"].forEach((name) => {
    fillValue(`model-${name}-key`, "");
    byId(`model-${name}-key-state`).textContent =
      credentialState(data.secrets[`${name}_api_key`]);
  });
  byId("model-config-state").textContent = configurationState(data);
  if (!data.encryption_configured) {
    byId("model-config-state").textContent += " · 加密主密钥未配置";
  }
}

async function loadProviders() {
  byId("provider-summary").textContent = "正在读取模型状态与计费信息";
  try {
    const [data, configuration, audit] = await Promise.all([
      request("/api/providers"),
      request("/api/settings/model-services"),
      request("/api/settings/audit?scope=model_services&limit=8"),
    ]);
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
    fillModelConfiguration(configuration);
    renderConfigurationAudit("model-config-audit", audit.events);
  } catch (error) {
    byId("provider-summary").textContent = error.message;
  }
}

async function saveModelServices(event) {
  event.preventDefault();
  const button = byId("save-model-services");
  const payload = {
    provider: byId("model-provider-mode").value,
    openai_proxy_url: byId("model-openai-proxy").value,
    openai_base_url: byId("model-openai-base").value,
    openai_model: byId("model-openai-name").value,
    gpt_base_url: byId("model-gpt-base").value,
    gpt_model: byId("model-gpt-name").value,
    gpt_planner_model: byId("model-gpt-planner").value,
    gpt_coder_model: byId("model-gpt-coder").value,
    gpt_supervisor_model: byId("model-gpt-supervisor").value,
    deepseek_base_url: byId("model-deepseek-base").value,
    deepseek_model: byId("model-deepseek-name").value,
    minimax_base_url: byId("model-minimax-base").value,
    minimax_model: byId("model-minimax-name").value,
  };
  ["openai", "gpt", "deepseek", "minimax"].forEach((name) => {
    const value = byId(`model-${name}-key`).value;
    if (value) payload[`${name}_api_key`] = value;
  });
  button.disabled = true;
  byId("model-config-message").textContent = "正在安全保存";
  try {
    const result = await request("/api/settings/model-services", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    fillModelConfiguration(result);
    const audit = await request("/api/settings/audit?scope=model_services&limit=8");
    renderConfigurationAudit("model-config-audit", audit.events);
    byId("model-config-message").textContent = result.restart_required
      ? "配置已保存；重启 Seed 控制器后生效" : "配置已保存并生效";
  } catch (error) {
    byId("model-config-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function testModelService() {
  const button = byId("test-model-service");
  button.disabled = true;
  byId("model-config-message").textContent = "正在测试已保存的服务配置";
  try {
    const result = await request("/api/settings/model-services/test", {
      method: "POST",
      body: JSON.stringify({provider_id: byId("model-test-provider").value}),
    });
    byId("model-config-message").textContent = result.available
      ? `连接可用：${result.detail}` : `连接不可用：${result.detail}`;
    const audit = await request("/api/settings/audit?scope=model_services&limit=8");
    renderConfigurationAudit("model-config-audit", audit.events);
  } catch (error) {
    byId("model-config-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function loadNodes() {
  try {
    const data = await request("/api/nodes");
    const ready = data.nodes.filter((item) => item.status === "ok").length;
    byId("node-summary").textContent = `${ready}/${data.nodes.length} 在线`;
    byId("nodes").innerHTML = `<div class="resource-row node-row resource-header">
      <span>工作节点</span><span>状态</span><span>负载</span><span>能力</span>
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

async function loadPhysicalHosts() {
  byId("host-summary").textContent = "正在检查 Seed 本机 Docker";
  try {
    const status = await request("/api/containers/status");
    const stateClass = status.available ? "ok" : status.enabled ? "bad" : "warn";
    const stateLabel = status.available ? "可用" : status.enabled ? "异常" : "未启用";
    byId("host-summary").textContent =
      `Seed 本机 ${stateLabel} · 远程 SSH 主机待实现`;
    byId("physical-hosts").innerHTML = `<div class="resource-row resource-header">
      <span>物理主机</span><span>状态</span><span>Docker</span><span>说明</span>
    </div><div class="resource-row">
      <strong>Seed 本机<small>local-docker</small></strong>
      <span class="${stateClass}">${stateLabel}</span>
      <span>${escapeHtml(status.detail || "未检测")}</span>
      <span>当前仅支持在 Seed 所在主机创建节点容器。远程主机登记、SSH 指纹确认和准入检测将在下一阶段提供。</span>
    </div>`;
  } catch (error) {
    byId("host-summary").textContent = error.message;
  }
}

function fillPlatformConfiguration(data) {
  const values = data.desired;
  fillValue("platform-seed-url", values.seed_public_url);
  fillValue("platform-callback-url", values.node_callback_url);
  fillValue("platform-node-image", values.node_container_image);
  fillValue("platform-registry", values.node_image_registry);
  fillValue("platform-image-proxy", values.node_image_proxy);
  fillValue("platform-default-slots", values.default_node_slots);
  fillValue("platform-cpu-limit", values.default_node_cpu_limit);
  fillValue("platform-memory-limit", values.default_node_memory_limit);
  fillValue("platform-heartbeat", values.node_heartbeat_seconds);
  fillValue("platform-offline", values.node_offline_seconds);
  fillValue("platform-log-retention", values.log_retention_days);
  fillValue("platform-artifact-retention", values.artifact_retention_days);
  byId("platform-config-state").textContent = configurationState(data);
}

async function loadPlatformSettings() {
  byId("platform-summary").textContent = "正在读取运行配置";
  try {
    const [status, configuration, audit] = await Promise.all([
      request("/api/containers/status"),
      request("/api/settings/platform"),
      request("/api/settings/audit?scope=platform&limit=8"),
    ]);
    const desired = configuration.desired;
    byId("platform-summary").textContent = configuration.restart_required
      ? `平台配置 v${configuration.version} 等待重启`
      : `工作镜像 ${desired.node_container_image || "未指定"}`;
    byId("platform-settings").innerHTML = `<div class="resource-row resource-header">
      <span>设置</span><span>当前状态</span><span>当前值</span><span>生效方式</span>
    </div>
    <div class="resource-row"><strong>工作节点镜像</strong>
      <span class="${configuration.restart_required ? "warn" : "ok"}">${configuration.restart_required ? "等待生效" : "已生效"}</span>
      <code>${escapeHtml(desired.node_container_image || "—")}</code><span>保存后重启 Seed 控制器，使后续新节点使用此镜像。</span></div>
    <div class="resource-row"><strong>节点内部网络</strong>
      <span class="${status.network ? "ok" : "warn"}">${status.network ? "已配置" : "未提供"}</span>
      <code>${escapeHtml(status.network || "—")}</code><span>启动根配置；工作节点默认不发布宿主机端口。</span></div>
    <div class="resource-row"><strong>敏感启动配置</strong><span class="ok">受保护</span>
      <span>数据库、会话密钥、加密主密钥、节点令牌</span><span>继续由 Docker Secret 或环境变量提供，Web 不回读原文。</span></div>
    <div class="resource-row"><strong>Web 管理配置</strong><span class="ok">已开放</span>
      <span>地址、镜像策略、默认限额、心跳和保留策略</span><span>所有保存与启动生效动作均写入审计记录。</span></div>`;
    fillPlatformConfiguration(configuration);
    renderConfigurationAudit("platform-config-audit", audit.events);
  } catch (error) {
    byId("platform-summary").textContent = error.message;
  }
}

async function savePlatformSettings(event) {
  event.preventDefault();
  const button = byId("save-platform-settings");
  const payload = {
    seed_public_url: byId("platform-seed-url").value,
    node_callback_url: byId("platform-callback-url").value,
    node_container_image: byId("platform-node-image").value,
    node_image_registry: byId("platform-registry").value,
    node_image_proxy: byId("platform-image-proxy").value,
    default_node_slots: Number(byId("platform-default-slots").value),
    default_node_cpu_limit: byId("platform-cpu-limit").value,
    default_node_memory_limit: byId("platform-memory-limit").value,
    node_heartbeat_seconds: Number(byId("platform-heartbeat").value),
    node_offline_seconds: Number(byId("platform-offline").value),
    log_retention_days: Number(byId("platform-log-retention").value),
    artifact_retention_days: Number(byId("platform-artifact-retention").value),
  };
  button.disabled = true;
  byId("platform-config-message").textContent = "正在保存平台设置";
  try {
    const result = await request("/api/settings/platform", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    fillPlatformConfiguration(result);
    const audit = await request("/api/settings/audit?scope=platform&limit=8");
    renderConfigurationAudit("platform-config-audit", audit.events);
    byId("platform-config-message").textContent = result.restart_required
      ? "配置已保存；重启 Seed 控制器后生效" : "配置已保存并生效";
  } catch (error) {
    byId("platform-config-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

const containerRoleNames = {
  execution: "执行节点", test: "测试节点", preproduction: "预生产节点",
};

function containerActions(item) {
  const nodeId = escapeHtml(item.node_id);
  const primary = item.state === "running"
    ? `<button type="button" class="secondary container-action" data-node-id="${nodeId}" data-action="stop">停止</button>`
    : `<button type="button" class="secondary container-action" data-node-id="${nodeId}" data-action="start">启动</button>`;
  return `<div class="container-actions">${primary}
    <button type="button" class="secondary container-action remove-container"
      data-node-id="${nodeId}" data-action="remove">移除</button></div>`;
}

async function loadContainers() {
  byId("container-summary").textContent = "正在读取 Docker Engine";
  try {
    const status = await request("/api/containers/status");
    byId("container-form").classList.toggle("hidden", !status.available);
    if (!status.available) {
      byId("container-summary").textContent = status.detail || "不可用";
      byId("managed-containers").innerHTML = "";
      return;
    }
    const data = await request("/api/containers");
    const running = data.containers.filter((item) => item.state === "running").length;
    byId("container-summary").textContent =
      `${running}/${data.containers.length} 运行 · ${status.detail}`;
    const empty = `<div class="resource-row container-row"><span>尚未创建节点容器</span><span>—</span><span>—</span><span>使用上方表单创建</span></div>`;
    const rows = data.containers.map((item) => `
      <div class="resource-row container-row">
        <strong>${escapeHtml(item.node_id)}<small>${escapeHtml(item.name)}</small></strong>
        <span>${escapeHtml(containerRoleNames[item.role] || item.role)}</span>
        <span class="${item.state === "running" ? "ok" : "warn"}">${escapeHtml(item.state)}<small>${escapeHtml(item.status)}</small></span>
        ${containerActions(item)}
      </div>`).join("");
    byId("managed-containers").innerHTML = `<div class="resource-row container-row resource-header">
      <span>节点容器</span><span>角色</span><span>状态</span><span>操作</span>
    </div>${rows || empty}`;
  } catch (error) {
    byId("container-summary").textContent = error.message;
  }
}

async function createContainer(event) {
  event.preventDefault();
  const button = byId("create-container");
  button.disabled = true;
  byId("container-message").textContent = "正在创建并启动容器";
  try {
    const result = await request("/api/containers", {
      method: "POST",
      body: JSON.stringify({
        node_id: byId("container-node-id").value,
        role: byId("container-role").value,
        slots: Number(byId("container-slots").value),
      }),
    });
    byId("container-message").textContent = `${result.node_id} 已创建并加入调度`;
    byId("container-node-id").value = "";
    await Promise.all([loadContainers(), loadNodes()]);
  } catch (error) {
    byId("container-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function runContainerAction(nodeId, action) {
  if (action === "remove" &&
      !window.confirm(`确认移除节点容器 ${nodeId}？节点数据卷将保留。`)) return;
  const verbs = {start: "启动", stop: "停止", remove: "移除"};
  byId("container-message").textContent = `正在${verbs[action]} ${nodeId}`;
  try {
    await request(`/api/containers/${encodeURIComponent(nodeId)}/${action}`, {method: "POST"});
    byId("container-message").textContent = `${nodeId} 操作完成`;
    await Promise.all([loadContainers(), loadNodes()]);
  } catch (error) {
    byId("container-message").textContent = error.message;
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

async function loadWorkNodes() {
  await Promise.all([
    loadNodes(), loadContainers(), loadNodeLoad(), loadAcceptancePrerequisites(),
  ]);
}

function selectedTestEnvironmentProject() {
  return registeredProjects.find((item) => item.id === byId("test-environment-project").value);
}

function setTestEnvironmentEditMode(editing, lockProject = editing) {
  ["test-environment-url", "test-environment-edge", "test-environment-origin",
    "test-environment-name"].forEach((id) => { byId(id).disabled = !editing; });
  byId("test-environment-project").disabled = lockProject;
  byId("check-test-environment").classList.toggle("hidden", editing);
  byId("edit-test-environment").classList.toggle("hidden", editing);
  byId("save-test-environment").classList.toggle("hidden", !editing);
  byId("cancel-test-environment").classList.toggle("hidden", !editing);
  byId("delete-test-environment").classList.toggle("hidden", !editing);
}

function fillTestEnvironmentForm(forceEditing = false) {
  const project = selectedTestEnvironmentProject();
  const environment = project?.test_environment;
  byId("test-environment-url").value = environment?.target_url || "";
  byId("test-environment-edge").value = environment?.edge_host || "";
  byId("test-environment-origin").value = environment?.origin_host || "";
  byId("test-environment-name").value = environment?.expected_environment || "production";
  byId("delete-test-environment").disabled = !environment;
  byId("test-environment-summary").textContent = !project ? "没有已接入项目"
    : environment ? `${project.name} · 基础资源已配置` : `${project.name} · 未配置`;
  byId("test-environment-message").textContent = "";
  const editing = forceEditing || Boolean(project && !environment);
  setTestEnvironmentEditMode(editing, Boolean(environment));
  byId("check-test-environment").disabled = !environment;
  byId("edit-test-environment").disabled = !project;
}

function loadTestEnvironmentConfig() {
  const select = byId("test-environment-project");
  const previous = select.value || currentProjectId;
  select.innerHTML = registeredProjects.map((project) =>
    `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)}</option>`).join("");
  select.value = registeredProjects.some((item) => item.id === previous)
    ? previous : registeredProjects[0]?.id || "";
  byId("save-test-environment").disabled = registeredProjects.length === 0;
  fillTestEnvironmentForm();
}

async function saveTestEnvironment(event) {
  event.preventDefault();
  const projectId = byId("test-environment-project").value;
  const button = byId("save-test-environment");
  button.disabled = true;
  byId("test-environment-message").textContent = "正在保存";
  try {
    await request(`/api/projects/${encodeURIComponent(projectId)}/test-environment`, {
      method: "PUT",
      body: JSON.stringify({
        target_url: byId("test-environment-url").value,
        edge_host: byId("test-environment-edge").value,
        origin_host: byId("test-environment-origin").value,
        expected_environment: byId("test-environment-name").value,
      }),
    });
    await loadProjects(projectId);
    loadTestEnvironmentConfig();
    byId("test-environment-message").textContent = "配置已保存，后续验收将自动使用";
  } catch (error) {
    byId("test-environment-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function deleteTestEnvironment() {
  const projectId = byId("test-environment-project").value;
  byId("test-environment-message").textContent = "正在停用";
  try {
    await request(`/api/projects/${encodeURIComponent(projectId)}/test-environment`, {
      method: "DELETE",
    });
    await loadProjects(projectId);
    loadTestEnvironmentConfig();
    byId("test-environment-message").textContent = "预生产配置已停用";
  } catch (error) {
    byId("test-environment-message").textContent = error.message;
  }
}

async function checkTestEnvironment() {
  const projectId = byId("test-environment-project").value;
  const button = byId("check-test-environment");
  button.disabled = true;
  byId("test-environment-message").textContent = "正在检测";
  try {
    const result = await request(
      `/api/projects/${encodeURIComponent(projectId)}/test-environment/check`,
      {method: "POST"},
    );
    byId("test-environment-message").textContent = result.available
      ? `可用：${result.detail}` : `不可用：${result.detail}`;
  } catch (error) {
    byId("test-environment-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function loadResources() {
  const sections = [
    ["system-disclosure", loadSystemConfig],
    ["providers-disclosure", loadProviders],
    ["hosts-disclosure", loadPhysicalHosts],
    ["nodes-disclosure", loadWorkNodes],
    ["platform-disclosure", loadPlatformSettings],
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
byId("refresh-hosts").addEventListener("click", loadPhysicalHosts);
byId("refresh-nodes").addEventListener("click", loadWorkNodes);
byId("refresh-platform").addEventListener("click", loadPlatformSettings);
refreshWhenExpanded("system-disclosure", loadSystemConfig);
refreshWhenExpanded("providers-disclosure", loadProviders);
refreshWhenExpanded("hosts-disclosure", loadPhysicalHosts);
refreshWhenExpanded("nodes-disclosure", loadWorkNodes);
refreshWhenExpanded("platform-disclosure", loadPlatformSettings);
refreshWhenExpanded("test-environment-disclosure", loadTestEnvironmentConfig);
byId("test-environment-project").addEventListener("change", fillTestEnvironmentForm);
byId("test-environment-form").addEventListener("submit", saveTestEnvironment);
byId("delete-test-environment").addEventListener("click", deleteTestEnvironment);
byId("check-test-environment").addEventListener("click", checkTestEnvironment);
byId("edit-test-environment").addEventListener("click", () => setTestEnvironmentEditMode(true));
byId("cancel-test-environment").addEventListener("click", () => fillTestEnvironmentForm());
byId("model-services-form").addEventListener("submit", saveModelServices);
byId("test-model-service").addEventListener("click", testModelService);
byId("platform-settings-form").addEventListener("submit", savePlatformSettings);
byId("container-form").addEventListener("submit", createContainer);
byId("managed-containers").addEventListener("click", (event) => {
  const button = event.target.closest(".container-action");
  if (button) runContainerAction(button.dataset.nodeId, button.dataset.action);
});
window.addEventListener("taskhub:projects", () => {
  if (byId("test-environment-disclosure").open) loadTestEnvironmentConfig();
});
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
