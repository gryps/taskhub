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

const runtimeRoleRequirements = {
  execution: {
    title: "执行节点",
    description: "代码实施与构建",
    capabilities: [
      ["git", "Git"], ["python3", "Python 3"], ["coding", "编码执行器"],
      ["workspace_write_sandbox", "工作区写入沙箱"],
    ],
  },
  test: {
    title: "测试节点",
    description: "自动化测试与验收命令",
    capabilities: [["git", "Git"], ["python3", "Python 3"], ["pytest", "pytest"]],
  },
  preproduction: {
    title: "预生产节点",
    description: "构建、测试与预发布验证",
    capabilities: [["git", "Git"], ["python3", "Python 3"], ["pytest", "pytest"]],
  },
};

function nodeRole(node) {
  if (runtimeRoleRequirements[node.role]) return node.role;
  const workloads = new Set(node.workloads || []);
  if (workloads.has("coding")) return "execution";
  if (workloads.has("build") && workloads.has("test")) return "preproduction";
  return "test";
}

function controllerEnvironmentReady(onboarding) {
  const engine = onboarding.seed?.docker?.engine || {};
  const storage = onboarding.seed?.storage || {};
  return Boolean(onboarding.seed?.docker?.available && engine.cpu_count &&
    engine.memory_total_bytes && storage.free_bytes);
}

function eligibleRoleNodes(role, nodes) {
  const definition = runtimeRoleRequirements[role];
  return (nodes || []).filter((node) => nodeRole(node) === role && node.status === "ok" &&
    definition.capabilities.every(([capability]) => node.capabilities?.[capability]));
}

function environmentItem(label, passed, detail, unavailable = false) {
  const state = unavailable ? "待部署" : passed ? "通过" : "缺失";
  const stateClass = unavailable ? "muted" : passed ? "ok" : "bad";
  return `<li><span>${escapeHtml(label)}</span><strong class="${stateClass}">${state}</strong>
    <small>${escapeHtml(detail)}</small></li>`;
}

function renderRuntimeRoles(data, onboarding) {
  const engine = onboarding.seed?.docker?.engine || {};
  const storage = onboarding.seed?.storage || {};
  const dockerAvailable = Boolean(onboarding.seed?.docker?.available);
  const controllerItems = [
    environmentItem("Docker Engine", dockerAvailable, engine.version || "无法连接 Docker Engine"),
    environmentItem("CPU", Boolean(engine.cpu_count),
      engine.cpu_count ? `${engine.cpu_count} 核可用` : "未读取到 CPU 配额"),
    environmentItem("内存", Boolean(engine.memory_total_bytes),
      engine.memory_total_bytes ? formatBytes(engine.memory_total_bytes) : "未读取到内存配额"),
    environmentItem("持久化磁盘", Boolean(storage.free_bytes),
      storage.free_bytes ? `${formatBytes(storage.free_bytes)} 可用` : "未读取到磁盘空间"),
  ].join("");
  const controllerReady = controllerEnvironmentReady(onboarding);
  const cards = [`<article class="runtime-role-card ${controllerReady ? "ready" : "blocked"}">
    <header><div><h3>Seed 控制节点</h3><p>控制面与容器编排</p></div>
      <span class="role-state ${controllerReady ? "ok" : "bad"}">${controllerReady ? "环境就绪" : "环境异常"}</span></header>
    <ul>${controllerItems}</ul></article>`];

  Object.entries(runtimeRoleRequirements).forEach(([role, definition]) => {
    const roleNodes = (data.nodes || []).filter((node) => nodeRole(node) === role);
    const onlineNodes = roleNodes.filter((node) => node.status === "ok");
    const eligible = eligibleRoleNodes(role, data.nodes);
    const unavailable = roleNodes.length === 0;
    const items = definition.capabilities.map(([capability, label]) => {
      const passed = onlineNodes.some((node) => node.capabilities?.[capability]);
      const supporting = onlineNodes.filter((node) => node.capabilities?.[capability]).length;
      const detail = unavailable ? "尚未创建该角色节点" :
        `${supporting}/${roleNodes.length} 个节点提供此能力`;
      return environmentItem(label, passed, detail, unavailable);
    }).join("");
    const state = unavailable ? "尚未部署" : eligible.length ? "环境就绪" :
      onlineNodes.length ? "能力缺失" : "节点离线";
    const stateClass = unavailable ? "muted" : eligible.length ? "ok" : "bad";
    cards.push(`<article class="runtime-role-card ${eligible.length ? "ready" : "blocked"}">
      <header><div><h3>${definition.title}</h3><p>${definition.description}</p></div>
        <span class="role-state ${stateClass}">${state}</span></header>
      <div class="role-node-count">${onlineNodes.length}/${roleNodes.length} 在线 · ${eligible.length} 个满足全部必备项</div>
      <ul>${items}</ul></article>`);
  });
  return `<div class="runtime-role-grid">${cards.join("")}</div>
    <p class="runtime-role-note">这里只检测各角色的通用必备环境；Node.js、浏览器、数据库等项目特有能力按任务契约在“工作节点”中继续核验。</p>`;
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
    const [data, onboarding] = await Promise.all([
      request("/api/system/config"), request("/api/onboarding/status"),
    ]);
    const online = (data.nodes || []).filter((node) => node.status === "ok").length;
    const readyRoles = Number(controllerEnvironmentReady(onboarding)) +
      Object.keys(runtimeRoleRequirements).filter(
        (role) => eligibleRoleNodes(role, data.nodes).length,
      ).length;
    byId("system-summary").textContent =
      `角色环境 ${readyRoles}/4 就绪 · 工作节点 ${online}/${(data.nodes || []).length} 在线`;
    const banner = byId("system-readiness-banner");
    banner.className = `system-readiness-banner ${onboarding.ready ? "ready" : "pending"}`;
    banner.innerHTML = `<strong>${escapeHtml(onboarding.message)}</strong><span>${onboarding.completed}/${onboarding.total} 项初始化条件已完成</span>`;
    byId("system-checks").innerHTML = renderRuntimeRoles(data, onboarding);
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
  const actionNames = {update: "保存配置", connection_test: "连接测试", apply: "启动生效",
    admit: "登记主机", check: "重新检测", create: "创建节点", start: "启动节点",
    stop: "停止节点", restart: "重启节点", remove: "移除节点",
    rotate_credential: "轮换节点凭据", revoke_credential: "吊销节点凭据"};
  const resultNames = {pending_restart: "等待重启", passed: "通过", failed: "失败", applied: "已生效"};
  const rows = events.map((item) => {
    const summary = item.parameter_summary || {};
    const fields = (summary.changed_fields || []).join(" · ") ||
      (summary.provider_id ? `服务：${summary.provider_id}` :
        summary.node_id ? `节点：${summary.node_id} · 主机：${summary.host_id}` :
          summary.host_id ? `主机：${summary.host_id}` : `版本：${summary.version || "—"}`);
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

const modelRoleNames = {planner: "规划", coder: "编码", supervisor: "监督", reviewer: "评审", risk: "风险分析"};
const modelRoles = Object.keys(modelRoleNames);
let modelCardCredentials = {};

function newModelCard() {
  return {model_id: `model-${Date.now().toString(36)}`, display_name: "", service_type: "openai",
    auth_mode: "api", base_url: "https://api.openai.com/v1", model: "", proxy_url: "",
    enabled: true, assignments: []};
}

function modelAssignment(card, role) {
  return (card.assignments || []).find((item) => item.role === role);
}

function modelCardHtml(card) {
  const credential = modelCardCredentials[card.model_id] || {};
  const accountMode = card.auth_mode === "account";
  const roleRows = modelRoles.map((role) => {
    const assignment = modelAssignment(card, role);
    return `<label class="model-role-option"><input type="checkbox" data-model-role="${role}" ${assignment ? "checked" : ""}>
      <span>${modelRoleNames[role]}</span><select data-model-priority="${role}" ${assignment ? "" : "disabled"}>
        ${[0, 1, 2, 3].map((priority) => `<option value="${priority}" ${assignment?.priority === priority ? "selected" : ""}>${priority === 0 ? "主模型" : `备用 ${priority}`}</option>`).join("")}
      </select></label>`;
  }).join("");
  return `<article class="model-config-card ${accountMode ? "account-mode" : ""}" data-model-id="${escapeHtml(card.model_id)}">
    <header><div><h3>${escapeHtml(card.display_name || "新模型")}</h3><small>${accountMode ? "ChatGPT 账号认证" : "API 认证"}</small></div>
      <label class="model-enabled"><input type="checkbox" data-field="enabled" ${card.enabled ? "checked" : ""}>启用</label></header>
    <div class="model-card-fields">
      <label>模型 ID<input data-field="model_id" maxlength="48" value="${escapeHtml(card.model_id)}"></label>
      <label>显示名称<input data-field="display_name" maxlength="100" value="${escapeHtml(card.display_name)}" placeholder="例如 GPT 主服务"></label>
      <label>服务商<select data-field="service_type">
        ${[["openai", "OpenAI"], ["deepseek", "DeepSeek"], ["minimax", "MiniMax"], ["custom", "兼容服务"]].map(([value, label]) => `<option value="${value}" ${card.service_type === value ? "selected" : ""}>${label}</option>`).join("")}
      </select></label>
      <label>认证模式<select data-field="auth_mode"><option value="api" ${accountMode ? "" : "selected"}>API 模式</option>
        <option value="account" ${accountMode ? "selected" : ""} ${card.service_type === "openai" ? "" : "disabled"}>ChatGPT 账号</option></select></label>
      <label class="api-model-field">API 地址<input data-field="base_url" type="url" maxlength="500" value="${escapeHtml(card.base_url || "")}"></label>
      <label>模型<input data-field="model" maxlength="200" value="${escapeHtml(card.model || "")}" placeholder="账号模式留空使用默认模型"></label>
      <label class="api-model-field">替换 API Key<input data-field="api_key" type="password" maxlength="4096" autocomplete="new-password" placeholder="留空保留现有密钥"><small>${credential.configured ? "已安全配置" : "尚未配置"}</small></label>
      <label>网络代理（可选）<input data-field="proxy_url" type="url" maxlength="500" value="${escapeHtml(card.proxy_url || "")}" placeholder="http://proxy.example:7893"></label>
    </div>
    <fieldset class="model-role-selector"><legend>角色与主备顺序</legend>${roleRows}</fieldset>
    <div class="model-card-actions">
      ${accountMode ? `<button type="button" class="secondary model-device-auth">开始账号授权</button>` : ""}
      <button type="button" class="secondary model-test">测试连接</button>
      <button type="button" class="secondary model-remove">删除</button>
      <span class="model-card-status" role="status">${accountMode && credential.configured ? "账号已认证" : ""}</span>
    </div>
  </article>`;
}

function fillModelConfiguration(data) {
  modelCardCredentials = data.card_credentials || {};
  const cards = data.desired.model_cards || [];
  byId("model-card-editor").innerHTML = cards.length
    ? cards.map(modelCardHtml).join("")
    : '<div class="model-empty-state"><strong>尚未添加模型</strong><span>添加卡片并为五个角色配置主模型与备用顺序。</span></div>';
  byId("model-config-state").textContent = configurationState(data);
  if (!data.encryption_configured) {
    byId("model-config-state").textContent += " · 加密主密钥未配置";
  }
}

async function loadProviders() {
  byId("provider-summary").textContent = "正在读取模型状态与计费信息";
  try {
    const [configuration, audit] = await Promise.all([
      request("/api/settings/model-services"),
      request("/api/settings/audit?scope=model_services&limit=8"),
    ]);
    const cards = configuration.desired.model_cards || [];
    const ready = cards.filter((item) => item.enabled && configuration.card_credentials?.[item.model_id]?.configured).length;
    byId("provider-summary").textContent = `${ready}/${cards.length} 已认证 · 按角色主备路由`;
    fillModelConfiguration(configuration);
    renderConfigurationAudit("model-config-audit", audit.events);
  } catch (error) {
    byId("provider-summary").textContent = error.message;
  }
}

async function saveModelServices(event) {
  event.preventDefault();
  const button = byId("save-model-services");
  const payload = {model_cards: [...document.querySelectorAll(".model-config-card")].map(readModelCard)};
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

function readModelCard(card) {
  const value = (name) => card.querySelector(`[data-field="${name}"]`);
  const result = {model_id: value("model_id").value.trim(), display_name: value("display_name").value.trim(),
    service_type: value("service_type").value, auth_mode: value("auth_mode").value,
    base_url: value("base_url").value.trim(), model: value("model").value.trim(),
    proxy_url: value("proxy_url").value.trim(), enabled: value("enabled").checked,
    assignments: modelRoles.filter((role) => card.querySelector(`[data-model-role="${role}"]`).checked)
      .map((role) => ({role, priority: Number(card.querySelector(`[data-model-priority="${role}"]`).value)}))};
  const key = value("api_key").value;
  if (key) result.api_key = key;
  return result;
}

async function testModelService(card) {
  const button = card.querySelector(".model-test");
  button.disabled = true;
  const status = card.querySelector(".model-card-status");
  status.textContent = "正在测试已保存配置";
  try {
    const result = await request("/api/settings/model-services/test", {
      method: "POST",
      body: JSON.stringify({provider_id: card.dataset.modelId}),
    });
    status.textContent = result.available
      ? `连接可用：${result.detail}` : `连接不可用：${result.detail}`;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function startDeviceAuth(card) {
  const status = card.querySelector(".model-card-status");
  status.textContent = "正在启动 Codex 设备授权";
  try {
    let result = await request("/api/settings/model-services/device-auth", {method: "POST",
      body: JSON.stringify({model_id: card.dataset.modelId})});
    const poll = async () => {
      if (result.device_code) {
        status.innerHTML = `<a href="${escapeHtml(result.login_url)}" target="_blank" rel="noopener">打开登录页</a> · 验证码 <code>${escapeHtml(result.device_code)}</code> · ${escapeHtml(result.detail)}`;
      } else status.textContent = result.detail;
      if (["authenticated", "failed"].includes(result.status)) {
        if (result.status === "authenticated") await loadProviders();
        return;
      }
      setTimeout(async () => { result = await request(`/api/settings/model-services/device-auth/${result.session_id}`); await poll(); }, 2000);
    };
    await poll();
  } catch (error) { status.textContent = error.message; }
}

async function loadNodes() {
  try {
    const data = await request("/api/nodes");
    const ready = data.nodes.filter((item) => item.status === "ok").length;
    byId("node-summary").textContent = `${ready}/${data.nodes.length} 在线`;
    const cards = data.nodes.map((item) => {
      const healthy = item.status === "ok";
      const capabilities = healthy
        ? Object.entries(item.capabilities || {}).filter(([, value]) => value).map(([name]) => escapeHtml(name)).join(" · ") || "未上报"
        : escapeHtml(item.detail || "节点不可达");
      return `<article class="management-card ${healthy ? "ready" : "blocked"}">
        <header><div><h3>${escapeHtml(item.node_id)}</h3><p>${escapeHtml(item.kind)} · ${(item.workloads || []).map(escapeHtml).join(" · ") || "未声明工作负载"}</p></div>
          <span class="card-state ${healthy ? "ok" : "bad"}">${healthy ? "可调度" : "不可用"}</span></header>
        <dl class="management-card-facts">
          <div><dt>Agent 状态</dt><dd>${escapeHtml(item.status)}</dd></div>
          <div><dt>槽位占用</dt><dd>${Number(item.active || 0)} / ${Number(item.slots || 0)}</dd></div>
          <div class="wide"><dt>已具备能力</dt><dd>${capabilities}</dd></div>
        </dl></article>`;
    }).join("");
    byId("nodes").innerHTML = cards || '<p class="management-empty">尚无已注册且可调度的 Agent。</p>';
  } catch (error) {
    byId("node-summary").textContent = error.message;
  }
}

let pendingHostFingerprint = "";
let physicalHostInventory = [];

function selectedHostRoles() {
  return [...document.querySelectorAll('input[name="host-role"]:checked')]
    .map((item) => item.value);
}

function hostConnectionPayload() {
  return {
    address: byId("host-address").value.trim(),
    port: Number(byId("host-port").value),
    username: byId("host-user").value.trim(),
    private_key: byId("host-private-key").value,
    docker_access: byId("host-docker-access").value,
  };
}

function resetHostFingerprint() {
  pendingHostFingerprint = "";
  byId("host-fingerprint-panel").classList.add("hidden");
  byId("host-fingerprint-confirmed").checked = false;
  byId("save-host").disabled = true;
}

function hostFacts(facts) {
  if (!facts || !Object.keys(facts).length) return "尚未检测";
  return `${facts.cpu_count || "—"} CPU · ${formatBytes(facts.memory_bytes)} 内存 · ` +
    `${formatBytes(facts.disk_available_bytes)} 可用磁盘`;
}

function hostStatusClass(status) {
  return status === "available" ? "ok" : ["degraded", "blocked"].includes(status) ? "bad" : "warn";
}

function hostMaintenanceActions(item) {
  const id = escapeHtml(item.host_id);
  const state = item.operational_state || "active";
  if (state !== "active") return `<div class="host-maintenance-actions">
    <button type="button" class="secondary host-state-action" data-host-id="${id}" data-state="active">重新启用</button>
    <button type="button" class="secondary host-action" data-host-id="${id}">重新检测</button></div>`;
  return `<div class="host-maintenance-actions">
    <button type="button" class="secondary host-action" data-host-id="${id}">重新检测</button>
    <button type="button" class="secondary host-state-action" data-host-id="${id}" data-state="draining">排空</button>
    <button type="button" class="secondary host-state-action" data-host-id="${id}" data-state="maintenance">维护</button>
    <button type="button" class="secondary danger-action host-state-action" data-host-id="${id}" data-state="disabled">停用</button>
  </div>`;
}

function refreshHostMaintenanceSelectors() {
  for (const id of ["host-rebuild-source", "host-rebuild-target"]) {
    const select = byId(id);
    const previous = select.value;
    select.innerHTML = physicalHostInventory.map((item) =>
      `<option value="${escapeHtml(item.host_id)}">${escapeHtml(item.display_name)} · ${escapeHtml(item.status_label)}</option>`).join("");
    if ([...select.options].some((item) => item.value === previous)) select.value = previous;
  }
}

async function loadPhysicalHosts() {
  byId("host-summary").textContent = "正在读取物理主机库存";
  try {
    const [status, inventory, audit] = await Promise.all([
      request("/api/containers/status"), request("/api/hosts"),
      request("/api/settings/audit?scope=physical_hosts&limit=8"),
    ]);
    physicalHostInventory = inventory.hosts;
    refreshContainerTargets();
    refreshHostMaintenanceSelectors();
    const stateClass = status.available ? "ok" : status.enabled ? "bad" : "warn";
    const stateLabel = status.available ? "可用" : status.enabled ? "异常" : "未启用";
    const available = inventory.hosts.filter((item) => item.status === "available").length;
    byId("host-summary").textContent =
      `Seed 本机 ${stateLabel} · 远程主机 ${available}/${inventory.hosts.length} 可用`;
    const seedCard = `<article class="management-card ${status.available ? "ready" : "blocked"}">
      <header><div><h3>Seed 本机</h3><p>local-docker · 控制节点内置主机</p></div>
        <span class="card-state ${stateClass}">${stateLabel}</span></header>
      <dl class="management-card-facts">
        <div><dt>连接方式</dt><dd>本机 Docker</dd></div>
        <div><dt>SSH 准入</dt><dd>无需配置</dd></div>
        <div class="wide"><dt>检测结果</dt><dd>${escapeHtml(status.detail || "未检测")}</dd></div>
      </dl></article>`;
    const remoteCards = inventory.hosts.map((item) => `<article class="management-card ${item.status === "available" ? "ready" : "blocked"}">
      <header><div><h3>${escapeHtml(item.display_name)}</h3><p>${escapeHtml(item.host_id)} · ${escapeHtml(item.address)}:${item.port}</p></div>
        <span class="card-state ${hostStatusClass(item.status)}">${escapeHtml(item.status_label)}</span></header>
      <dl class="management-card-facts">
        <div><dt>Docker</dt><dd>${escapeHtml(item.facts?.docker_version || "—")}</dd></div>
        <div><dt>操作系统</dt><dd>${escapeHtml(item.facts?.os || "尚未检测")}</dd></div>
        <div class="wide"><dt>主机资源</dt><dd>${escapeHtml(hostFacts(item.facts))}</dd></div>
        <div class="wide"><dt>允许角色</dt><dd>${(item.allowed_roles || []).map((role) => escapeHtml(containerRoleNames[role] || role)).join(" · ") || "未指定"}</dd></div>
        <div class="wide"><dt>运行判断</dt><dd>${escapeHtml(item.status_reason)} · ${(item.alerts || []).map(escapeHtml).join("；") || "资源阈值正常"}</dd></div>
        <div class="wide"><dt>SSH 指纹</dt><dd class="mono-value">${escapeHtml(item.fingerprint)}</dd></div>
      </dl><footer>${hostMaintenanceActions(item)}</footer></article>`).join("");
    byId("physical-hosts").innerHTML = seedCard + remoteCards;
    renderConfigurationAudit("host-config-audit", audit.events);
  } catch (error) {
    byId("host-summary").textContent = error.message;
  }
}

async function probePhysicalHost() {
  if (!byId("host-form").reportValidity()) return;
  const button = byId("probe-host");
  button.disabled = true;
  resetHostFingerprint();
  byId("host-message").textContent = "正在读取 SSH 主机指纹";
  try {
    const result = await request("/api/hosts/probe", {
      method: "POST", body: JSON.stringify(hostConnectionPayload()),
    });
    pendingHostFingerprint = result.fingerprint;
    byId("host-fingerprint").textContent = result.fingerprint;
    byId("host-fingerprint-panel").classList.remove("hidden");
    byId("host-message").textContent = result.detail;
  } catch (error) {
    byId("host-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function savePhysicalHost(event) {
  event.preventDefault();
  if (!pendingHostFingerprint || !byId("host-fingerprint-confirmed").checked) return;
  const button = byId("save-host");
  button.disabled = true;
  byId("host-message").textContent = "正在执行 SSH、Docker、硬件和回连准入检测";
  try {
    const result = await request("/api/hosts", {
      method: "POST",
      body: JSON.stringify({
        ...hostConnectionPayload(), expected_fingerprint: pendingHostFingerprint,
        host_id: byId("host-id").value.trim(),
        display_name: byId("host-name").value.trim(),
        allowed_roles: selectedHostRoles(),
        labels: byId("host-labels").value.split(",").map((item) => item.trim()).filter(Boolean),
        notes: byId("host-notes").value.trim(),
      }),
    });
    byId("host-message").textContent = `${result.display_name} 已通过准入并登记`;
    byId("host-private-key").value = "";
    resetHostFingerprint();
    await loadPhysicalHosts();
  } catch (error) {
    byId("host-message").textContent = error.message;
    button.disabled = false;
  }
}

async function checkPhysicalHost(hostId) {
  byId("host-message").textContent = `正在重新检测 ${hostId}`;
  try {
    const result = await request(`/api/hosts/${encodeURIComponent(hostId)}/check`, {method: "POST"});
    byId("host-message").textContent = `${result.display_name}：${result.status_reason}`;
  } catch (error) {
    byId("host-message").textContent = error.message;
  }
  await loadPhysicalHosts();
}

async function changeHostState(hostId, state) {
  const labels = {active: "重新启用", draining: "排空", maintenance: "进入维护", disabled: "停用"};
  if (state !== "active" && !window.confirm(`${labels[state]}主机 ${hostId}？该主机将立即停止接收新任务。`)) return;
  byId("host-message").textContent = `正在${labels[state]} ${hostId}`;
  try {
    const result = await request(`/api/hosts/${encodeURIComponent(hostId)}/state`, {
      method: "POST", body: JSON.stringify({state}),
    });
    byId("host-message").textContent = `${result.display_name}：${result.status_reason} · 影响 ${result.affected_nodes} 个节点`;
  } catch (error) { byId("host-message").textContent = error.message; }
  await Promise.all([loadPhysicalHosts(), loadContainers(), loadNodes()]);
}

async function rebuildHostNodes(event) {
  event.preventDefault();
  const source = byId("host-rebuild-source").value;
  const target = byId("host-rebuild-target").value;
  const nodeIds = byId("host-rebuild-nodes").value.split(",").map((item) => item.trim()).filter(Boolean);
  if (!source || !target || !window.confirm(`确认将 ${source} 的${nodeIds.length || "全部"}节点在 ${target} 重建？源数据卷会保留。`)) return;
  byId("host-rebuild-message").textContent = "正在逐节点停止、迁移期望状态并重建";
  try {
    const result = await request(`/api/hosts/${encodeURIComponent(source)}/rebuild-nodes`, {
      method: "POST", body: JSON.stringify({target_host_id: target, node_ids: nodeIds}),
    });
    const passed = result.results.filter((item) => item.ok).length;
    byId("host-rebuild-message").textContent = `完成 ${passed}/${result.results.length} 个节点`;
    await Promise.all([loadPhysicalHosts(), loadContainers(), loadNodes()]);
  } catch (error) { byId("host-rebuild-message").textContent = error.message; }
}

function fillPlatformConfiguration(data) {
  const values = data.desired;
  fillValue("platform-seed-url", values.seed_public_url);
  fillValue("platform-callback-url", values.node_callback_url);
  fillValue("platform-node-image", values.node_container_image);
  fillValue("platform-registry", values.node_image_registry);
  fillValue("platform-image-proxy", values.node_image_proxy);
  fillValue("platform-registry-username", values.node_registry_username);
  fillValue("platform-registry-password", "");
  byId("platform-registry-password-state").textContent =
    credentialState(data.secrets?.node_registry_password);
  fillValue("platform-default-slots", values.default_node_slots);
  fillValue("platform-cpu-limit", values.default_node_cpu_limit);
  fillValue("platform-memory-limit", values.default_node_memory_limit);
  fillValue("platform-heartbeat", values.node_heartbeat_seconds);
  fillValue("platform-offline", values.node_offline_seconds);
  fillValue("platform-log-retention", values.log_retention_days);
  fillValue("platform-artifact-retention", values.artifact_retention_days);
  fillValue("platform-failure-threshold", values.provider_failure_threshold);
  fillValue("platform-recovery-threshold", values.provider_recovery_threshold);
  fillValue("platform-probe-interval", values.provider_probe_interval_seconds);
  fillValue("platform-switch-lock", values.provider_switch_lock_seconds);
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
    byId("platform-settings").innerHTML = `
      <article class="management-card ${configuration.restart_required ? "blocked" : "ready"}">
        <header><div><h3>工作节点镜像</h3><p>新建节点使用的统一基础镜像</p></div>
          <span class="card-state ${configuration.restart_required ? "warn" : "ok"}">${configuration.restart_required ? "等待生效" : "已生效"}</span></header>
        <dl class="management-card-facts"><div class="wide"><dt>镜像地址</dt><dd class="mono-value">${escapeHtml(desired.node_container_image || "—")}</dd></div>
          <div class="wide"><dt>生效方式</dt><dd>保存后重启 Seed，后续新节点使用新镜像。</dd></div></dl></article>
      <article class="management-card ${status.network ? "ready" : "blocked"}">
        <header><div><h3>节点内部网络</h3><p>Seed 与本机工作节点通信边界</p></div>
          <span class="card-state ${status.network ? "ok" : "warn"}">${status.network ? "已配置" : "未提供"}</span></header>
        <dl class="management-card-facts"><div class="wide"><dt>网络名称</dt><dd class="mono-value">${escapeHtml(status.network || "—")}</dd></div>
          <div class="wide"><dt>端口策略</dt><dd>工作节点默认不发布宿主机端口。</dd></div></dl></article>
      <article class="management-card ready"><header><div><h3>敏感启动配置</h3><p>仅由部署环境提供的根配置</p></div><span class="card-state ok">受保护</span></header>
        <dl class="management-card-facts"><div class="wide"><dt>保护内容</dt><dd>数据库、会话密钥、加密主密钥、节点令牌</dd></div>
          <div class="wide"><dt>读取边界</dt><dd>通过 Docker Secret 或环境变量提供，Web 不回读原文。</dd></div></dl></article>
      <article class="management-card ready"><header><div><h3>Web 管理配置</h3><p>可审计、可版本化的平台参数</p></div><span class="card-state ok">已开放</span></header>
        <dl class="management-card-facts"><div class="wide"><dt>配置范围</dt><dd>地址、镜像策略、默认限额、心跳和保留策略</dd></div>
          <div class="wide"><dt>变更证据</dt><dd>保存与启动生效动作均写入审计记录。</dd></div></dl></article>`;
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
    node_registry_username: byId("platform-registry-username").value,
    node_registry_password: byId("platform-registry-password").value,
    default_node_slots: Number(byId("platform-default-slots").value),
    default_node_cpu_limit: byId("platform-cpu-limit").value,
    default_node_memory_limit: byId("platform-memory-limit").value,
    node_heartbeat_seconds: Number(byId("platform-heartbeat").value),
    node_offline_seconds: Number(byId("platform-offline").value),
    log_retention_days: Number(byId("platform-log-retention").value),
    artifact_retention_days: Number(byId("platform-artifact-retention").value),
    provider_failure_threshold: Number(byId("platform-failure-threshold").value),
    provider_recovery_threshold: Number(byId("platform-recovery-threshold").value),
    provider_probe_interval_seconds: Number(byId("platform-probe-interval").value),
    provider_switch_lock_seconds: Number(byId("platform-switch-lock").value),
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

const securityRoleNames = {
  administrator: "管理员", project_owner: "项目负责人",
  developer: "开发人员", auditor: "只读审计人员",
};

async function loadUsers() {
  const inventory = byId("user-inventory");
  try {
    const data = await request("/api/auth/users");
    inventory.innerHTML = `<div class="resource-row resource-header"><span>用户</span><span>角色</span><span>状态</span><span>操作</span></div>` +
      data.users.map((item) => `<div class="resource-row">
        <strong>${escapeHtml(item.username)}</strong><span>${escapeHtml(securityRoleNames[item.role] || item.role)}</span>
        <span class="${item.enabled ? "ok" : "warn"}">${item.enabled ? "可登录" : "已停用"}</span>
        <span>${item.built_in ? "内置账号" : `<button type="button" class="secondary edit-user" data-username="${escapeHtml(item.username)}" data-role="${escapeHtml(item.role)}" data-enabled="${item.enabled}">编辑</button> <button type="button" class="secondary delete-user" data-username="${escapeHtml(item.username)}">删除</button>`}</span>
      </div>`).join("");
  } catch (error) {
    inventory.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

async function saveUser(event) {
  event.preventDefault();
  const username = byId("security-username").value.trim().toLowerCase();
  byId("security-message").textContent = "正在保存用户";
  try {
    await request(`/api/auth/users/${encodeURIComponent(username)}`, {method: "PUT", body: JSON.stringify({
      username, role: byId("security-role").value,
      password: byId("security-password").value,
      enabled: byId("security-enabled").checked,
    })});
    byId("security-password").value = "";
    byId("security-message").textContent = "用户已保存";
    await loadUsers();
  } catch (error) { byId("security-message").textContent = error.message; }
}

async function deleteUser(username) {
  if (!window.confirm(`确认删除用户 ${username}？该用户将不能再次登录。`)) return;
  try {
    await request(`/api/auth/users/${encodeURIComponent(username)}`, {method: "DELETE"});
    byId("security-message").textContent = `用户 ${username} 已删除`;
    await loadUsers();
  } catch (error) { byId("security-message").textContent = error.message; }
}

async function rotateSessionKey() {
  if (!window.confirm("轮换会话签名密钥会注销包括当前用户在内的全部会话，确认继续？")) return;
  try {
    await request("/api/auth/signing-key/rotate", {method: "POST"});
    window.location.reload();
  } catch (error) { byId("security-message").textContent = error.message; }
}

const containerRoleNames = {
  execution: "执行节点", test: "测试节点", preproduction: "预生产节点",
};

function containerActions(item) {
  const nodeId = escapeHtml(item.node_id);
  const location = escapeHtml(item.location || "local");
  if (location === "remote" && !item.container_id) {
    return `<div class="container-actions"><button type="button" class="secondary container-action remove-container"
      data-node-id="${nodeId}" data-location="remote" data-action="remove">移除记录</button></div>`;
  }
  const primary = item.state === "running"
    ? `<button type="button" class="secondary container-action" data-node-id="${nodeId}" data-location="${location}" data-action="stop">停止</button>`
    : `<button type="button" class="secondary container-action" data-node-id="${nodeId}" data-location="${location}" data-action="start">启动</button>`;
  const restart = location === "remote" && item.state === "running"
    ? `<button type="button" class="secondary container-action" data-node-id="${nodeId}" data-location="remote" data-action="restart">重启</button>` : "";
  const rotate = item.credential?.status === "active"
    ? `<button type="button" class="secondary container-action" data-node-id="${nodeId}" data-location="${location}" data-action="rotate-credential">轮换凭据</button>` : "";
  const revoke = location === "remote" && item.credential?.status === "active"
    ? `<button type="button" class="secondary container-action" data-node-id="${nodeId}" data-location="remote" data-action="revoke-credential">吊销凭据</button>` : "";
  return `<div class="container-actions">${primary}
    ${restart}${rotate}${revoke}
    <button type="button" class="secondary container-action remove-container"
      data-node-id="${nodeId}" data-location="${location}" data-action="remove">移除</button></div>`;
}

function refreshContainerTargets() {
  const select = byId("container-target");
  const previous = select.value || "local";
  select.innerHTML = '<option value="local">Seed 本机</option>' + physicalHostInventory
    .filter((item) => item.status === "available")
    .map((item) => `<option value="${escapeHtml(item.host_id)}">${escapeHtml(item.display_name)} · 远程</option>`)
    .join("");
  select.value = [...select.options].some((item) => item.value === previous) ? previous : "local";
  updateRemoteNodeFields();
}

function updateRemoteNodeFields() {
  const hostId = byId("container-target").value;
  const remote = hostId !== "local";
  document.querySelectorAll(".remote-node-field").forEach((item) => item.classList.toggle("hidden", !remote));
  const host = physicalHostInventory.find((item) => item.host_id === hostId);
  const allowed = new Set(host?.allowed_roles || ["execution", "test", "preproduction"]);
  [...byId("container-role").options].forEach((item) => {
    item.disabled = remote && !allowed.has(item.value);
  });
  if (byId("container-role").selectedOptions[0]?.disabled) {
    const first = [...byId("container-role").options].find((item) => !item.disabled);
    if (first) byId("container-role").value = first.value;
  }
}

async function loadContainers() {
  byId("container-summary").textContent = "正在读取 Docker Engine";
  try {
    const [status, remoteData, hostData] = await Promise.all([
      request("/api/containers/status"), request("/api/remote-nodes"), request("/api/hosts"),
    ]);
    physicalHostInventory = hostData.hosts;
    refreshContainerTargets();
    const localData = status.available ? await request("/api/containers") : {containers: []};
    const local = localData.containers.map((item) => ({...item, location: "local", host_id: "Seed 本机"}));
    const remote = remoteData.nodes.map((item) => ({
      ...item, location: "remote", state: item.actual_state,
      status: item.status_reason, name: `taskhub-node-${item.node_id}`,
    }));
    const containers = [...local, ...remote];
    const diagnosticSelect = byId("diagnostic-node");
    const selectedDiagnostic = diagnosticSelect.value;
    diagnosticSelect.innerHTML = '<option value="">请选择节点</option>' + containers.map((item) =>
      `<option value="${escapeHtml(item.node_id)}">${escapeHtml(item.node_id)} · ${escapeHtml(containerRoleNames[item.role] || item.role)}</option>`).join("");
    if ([...diagnosticSelect.options].some((item) => item.value === selectedDiagnostic)) {
      diagnosticSelect.value = selectedDiagnostic;
    }
    byId("container-form").classList.toggle("hidden", !status.available && !physicalHostInventory.some((item) => item.status === "available"));
    const running = containers.filter((item) => item.state === "running").length;
    byId("container-summary").textContent =
      `${running}/${containers.length} 运行 · 本机 ${status.available ? "可用" : "不可用"} · 远程 ${remote.length}`;
    const cards = containers.map((item) => {
      const distribution = item.distribution || {};
      const progress = item.location === "remote" && distribution.phase && distribution.phase !== "complete"
        ? `<progress class="distribution-progress" max="100" value="${Number(distribution.percent || 0)}">${Number(distribution.percent || 0)}%</progress>
          <small>${Number(distribution.percent || 0)}% · ${escapeHtml(distribution.detail || item.status || "")}</small>`
        : `<small>${escapeHtml(item.status)}</small>`;
      const stateClass = item.state === "running" ? "ok" : item.state === "error" ? "bad" : "warn";
      const credential = item.credential
        ? ` · 凭据 v${Number(item.credential.version || 0)} ${item.credential.status === "active" ? "有效" : "已吊销"}` : "";
      const reconciled = item.reconciliation?.checked_at
        ? ` · 协调 ${new Date(item.reconciliation.checked_at).toLocaleTimeString()}` : "";
      return `<article class="management-card ${item.state === "running" ? "ready" : "blocked"}">
        <header><div><h3>${escapeHtml(item.node_id)}</h3><p>${escapeHtml(item.name)} · ${escapeHtml(item.host_id)}</p></div>
          <span class="card-state ${stateClass}">${escapeHtml(item.state)}</span></header>
        <dl class="management-card-facts">
          <div><dt>节点角色</dt><dd>${escapeHtml(containerRoleNames[item.role] || item.role)}</dd></div>
          <div><dt>部署位置</dt><dd>${item.location === "remote" ? "远程主机" : "Seed 本机"}</dd></div>
          <div class="wide"><dt>运行与分发</dt><dd>${progress}${escapeHtml(credential + reconciled)}</dd></div>
        </dl><footer>${containerActions(item)}</footer></article>`;
    }).join("");
    byId("managed-containers").innerHTML = cards || '<p class="management-empty">尚未创建节点容器，请展开“创建节点”进行配置。</p>';
    scheduleDistributionRefresh(remote.some((item) =>
      ["distributing", "starting"].includes(item.actual_state)));
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
      <section class="diagnostic-card"><h4>SSH / Docker 操作</h4><pre>${escapeHtml(diagnosticEventLines(data.operations))}</pre></section>`;
    byId("diagnostic-message").textContent = `${nodeId} 诊断已更新`;
  } catch (error) {
    byId("diagnostic-message").textContent = error.message;
  } finally { button.disabled = false; }
}

let distributionRefreshTimer = null;
function scheduleDistributionRefresh(active) {
  if (distributionRefreshTimer) {
    clearTimeout(distributionRefreshTimer);
    distributionRefreshTimer = null;
  }
  if (active && byId("nodes-disclosure").open) {
    distributionRefreshTimer = setTimeout(() => loadContainers(), 2000);
  }
}

async function createContainer(event) {
  event.preventDefault();
  const button = byId("create-container");
  button.disabled = true;
  byId("container-message").textContent = "正在创建并启动容器";
  try {
    const hostId = byId("container-target").value;
    const remote = hostId !== "local";
    const payload = {
      node_id: byId("container-node-id").value,
      role: byId("container-role").value,
      slots: Number(byId("container-slots").value),
    };
    if (remote) Object.assign(payload, {
      host_id: hostId,
      host_port: Number(byId("container-host-port").value),
      cpu_limit: byId("container-cpu-limit").value,
      memory_limit: byId("container-memory-limit").value,
    });
    const result = await request(remote ? "/api/remote-nodes" : "/api/containers", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    byId("container-message").textContent = remote
      ? `${result.node_id} 已进入镜像分发队列，可在下方查看进度`
      : `${result.node_id} 已创建并加入调度`;
    byId("container-node-id").value = "";
    await Promise.all([loadContainers(), loadNodes()]);
  } catch (error) {
    byId("container-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function runContainerAction(nodeId, action, location) {
  if (action === "remove" &&
      !window.confirm(`确认移除节点容器 ${nodeId}？节点数据卷将保留。`)) return;
  if (action === "rotate-credential" &&
      !window.confirm(`确认轮换 ${nodeId} 的独立凭据？节点将短暂重建并保留数据卷。`)) return;
  if (action === "revoke-credential" &&
      !window.confirm(`确认吊销 ${nodeId} 的独立凭据？节点将停止并退出调度。`)) return;
  const verbs = {start: "启动", stop: "停止", restart: "重启", remove: "移除",
    "rotate-credential": "轮换凭据", "revoke-credential": "吊销凭据"};
  byId("container-message").textContent = `正在${verbs[action]} ${nodeId}`;
  try {
    const prefix = location === "remote" ? "/api/remote-nodes" : "/api/containers";
    const credentialAction = {"rotate-credential": "rotate", "revoke-credential": "revoke"}[action];
    const endpoint = credentialAction && location === "remote"
      ? `${prefix}/${encodeURIComponent(nodeId)}/credential/${credentialAction}`
      : `${prefix}/${encodeURIComponent(nodeId)}/${action}`;
    await request(endpoint, {
      method: "POST", body: action === "remove" && location === "remote"
        ? JSON.stringify({remove_volume: false}) : undefined,
    });
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

const resourceSections = [
    ["system-disclosure", loadSystemConfig],
    ["providers-disclosure", loadProviders],
    ["hosts-disclosure", loadPhysicalHosts],
    ["nodes-disclosure", loadWorkNodes],
    ["platform-disclosure", loadPlatformSettings],
];

function storeOpenResource(id) {
  try {
    if (id) localStorage.setItem("taskhub_open_resource", id);
    else localStorage.removeItem("taskhub_open_resource");
  } catch (_error) {
    // The accordion still works when browser storage is unavailable.
  }
}

function restoreOpenResource() {
  let saved = "system-disclosure";
  try {
    saved = localStorage.getItem("taskhub_open_resource") || saved;
  } catch (_error) {
    // Keep the default running overview open.
  }
  if (!resourceSections.some(([id]) => id === saved)) saved = "system-disclosure";
  resourceSections.forEach(([id]) => { byId(id).open = id === saved; });
}

function openResourceDisclosure() {
  return resourceSections.map(([id]) => byId(id)).find((item) => item.open);
}

function updateResourceCollapseShortcut() {
  const button = byId("collapse-current-resource");
  const disclosure = openResourceDisclosure();
  const pageVisible = !byId("resource-page").classList.contains("hidden");
  const headingHasReachedHeader = disclosure && disclosure.getBoundingClientRect().top <= 69;
  button.classList.toggle("hidden", !(pageVisible && headingHasReachedHeader));
}

function scrollResourceHeadingIntoView(disclosure) {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  disclosure.scrollIntoView({behavior: reduceMotion ? "auto" : "smooth", block: "start"});
}

async function loadResources() {
  await Promise.all(resourceSections
    .filter(([id]) => byId(id).open)
    .map(([, load]) => load()));
  updateResourceCollapseShortcut();
}

function refreshWhenExpanded(id, load) {
  byId(id).addEventListener("toggle", (event) => {
    const disclosure = event.currentTarget;
    if (!disclosure.open) {
      if (!openResourceDisclosure()) storeOpenResource("");
      updateResourceCollapseShortcut();
      return;
    }
    resourceSections.forEach(([otherId]) => {
      if (otherId !== id) byId(otherId).open = false;
    });
    storeOpenResource(id);
    if (!byId("resource-page").classList.contains("hidden")) {
      load();
      requestAnimationFrame(() => {
        scrollResourceHeadingIntoView(disclosure);
        updateResourceCollapseShortcut();
      });
    }
  });
}

restoreOpenResource();

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
byId("collapse-current-resource").addEventListener("click", () => {
  const disclosure = openResourceDisclosure();
  if (!disclosure) return;
  disclosure.open = false;
  storeOpenResource("");
  scrollResourceHeadingIntoView(disclosure);
  updateResourceCollapseShortcut();
});
window.addEventListener("scroll", updateResourceCollapseShortcut, {passive: true});
window.addEventListener("resize", updateResourceCollapseShortcut);
byId("test-environment-project").addEventListener("change", fillTestEnvironmentForm);
byId("test-environment-form").addEventListener("submit", saveTestEnvironment);
byId("delete-test-environment").addEventListener("click", deleteTestEnvironment);
byId("check-test-environment").addEventListener("click", checkTestEnvironment);
byId("edit-test-environment").addEventListener("click", () => setTestEnvironmentEditMode(true));
byId("cancel-test-environment").addEventListener("click", () => fillTestEnvironmentForm());
byId("model-services-form").addEventListener("submit", saveModelServices);
byId("add-model-card").addEventListener("click", () => {
  const empty = byId("model-card-editor").querySelector(".model-empty-state");
  if (empty) empty.remove();
  byId("model-card-editor").insertAdjacentHTML("beforeend", modelCardHtml(newModelCard()));
});
byId("model-card-editor").addEventListener("click", (event) => {
  const card = event.target.closest(".model-config-card");
  if (!card) return;
  if (event.target.closest(".model-remove")) card.remove();
  if (event.target.closest(".model-test")) testModelService(card);
  if (event.target.closest(".model-device-auth")) startDeviceAuth(card);
});
byId("model-card-editor").addEventListener("change", (event) => {
  const card = event.target.closest(".model-config-card");
  if (!card) return;
  if (event.target.matches("[data-model-role]")) {
    card.querySelector(`[data-model-priority="${event.target.dataset.modelRole}"]`).disabled = !event.target.checked;
  }
  if (event.target.matches('[data-field="model_id"]')) card.dataset.modelId = event.target.value.trim();
  if (event.target.matches('[data-field="service_type"]')) {
    const account = card.querySelector('[data-field="auth_mode"] option[value="account"]');
    account.disabled = event.target.value !== "openai";
    if (account.disabled && account.selected) card.querySelector('[data-field="auth_mode"]').value = "api";
    const value = readModelCard(card);
    card.outerHTML = modelCardHtml(value);
  }
  if (event.target.matches('[data-field="auth_mode"]')) {
    const value = readModelCard(card);
    card.outerHTML = modelCardHtml(value);
  }
});
byId("platform-settings-form").addEventListener("submit", savePlatformSettings);
byId("access-security-disclosure").addEventListener("toggle", (event) => {
  if (event.currentTarget.open) loadUsers();
});
byId("user-form").addEventListener("submit", saveUser);
byId("rotate-session-key").addEventListener("click", rotateSessionKey);
byId("user-inventory").addEventListener("click", (event) => {
  const edit = event.target.closest(".edit-user");
  if (edit) {
    byId("security-username").value = edit.dataset.username;
    byId("security-role").value = edit.dataset.role;
    byId("security-enabled").checked = edit.dataset.enabled === "true";
    byId("security-password").value = "";
  }
  const remove = event.target.closest(".delete-user");
  if (remove) deleteUser(remove.dataset.username);
});
byId("host-form").addEventListener("submit", savePhysicalHost);
byId("host-rebuild-form").addEventListener("submit", rebuildHostNodes);
byId("load-node-diagnostics").addEventListener("click", loadNodeDiagnostics);
byId("probe-host").addEventListener("click", probePhysicalHost);
byId("host-fingerprint-confirmed").addEventListener("change", (event) => {
  byId("save-host").disabled = !event.currentTarget.checked || !pendingHostFingerprint;
});
for (const id of ["host-address", "host-port"]) {
  byId(id).addEventListener("input", resetHostFingerprint);
}
byId("physical-hosts").addEventListener("click", (event) => {
  const stateButton = event.target.closest(".host-state-action");
  if (stateButton) changeHostState(stateButton.dataset.hostId, stateButton.dataset.state);
  else {
    const button = event.target.closest(".host-action");
    if (button) checkPhysicalHost(button.dataset.hostId);
  }
});
byId("container-form").addEventListener("submit", createContainer);
byId("container-target").addEventListener("change", updateRemoteNodeFields);
byId("managed-containers").addEventListener("click", (event) => {
  const button = event.target.closest(".container-action");
  if (button) runContainerAction(button.dataset.nodeId, button.dataset.action, button.dataset.location);
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
