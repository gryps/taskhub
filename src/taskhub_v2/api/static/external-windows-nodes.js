(() => {
  const {request} = window.taskhubApi;
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(
    /[&<>'"]/g,
    (character) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"})[character],
  );

  function mountSurface() {
    byId("external-windows-node-surface").innerHTML = `
      <details id="external-windows-disclosure" class="resource-subdisclosure">
        <summary class="resource-subsection-head"><strong>接入外部 Windows 实测机</strong><span id="external-windows-summary" class="muted">正在读取</span></summary>
        <div class="external-windows-intro"><p>SSH 只用于首次检测、安装或修复。接入后由原生 Agent 提供测试能力，不加入物理主机池，也不承担节点迁移。</p></div>
        <div id="external-windows-nodes" class="management-card-grid"></div>
        <form id="external-windows-form" class="project-form resource-form configuration-form">
          <div class="form-grid configuration-grid">
            <label>节点 ID<input id="external-windows-node-id" required minlength="2" maxlength="64" pattern="[a-z0-9][a-z0-9_-]{1,63}" placeholder="windows-test-01"></label>
            <label>显示名称<input id="external-windows-display-name" required maxlength="100" placeholder="Windows 实测机"></label>
            <label>主机地址<input id="external-windows-address" required maxlength="253" spellcheck="false" placeholder="192.168.31.34"></label>
            <label>SSH 用户名<input id="external-windows-username" required maxlength="64" spellcheck="false" placeholder="user"></label>
            <label>SSH 端口<input id="external-windows-ssh-port" type="number" min="1" max="65535" value="22" required></label>
            <label>Agent 端口<input id="external-windows-agent-port" type="number" min="1024" max="65535" value="8301" required></label>
            <label>并发任务数<input id="external-windows-slots" type="number" min="1" max="16" value="1" required></label>
            <label class="wide">SSH 私钥<textarea id="external-windows-private-key" required rows="4" maxlength="32768" autocomplete="off" spellcheck="false" placeholder="粘贴能够免密登录该 Windows 用户的私钥"></textarea><span class="field-help">仅用于本次检测和安装，不持久化、不回显；主机指纹必须人工确认。</span></label>
            <label class="wide">检测到的主机指纹<input id="external-windows-fingerprint" readonly aria-readonly="true" placeholder="先执行 SSH 检测"></label>
            <label class="check wide"><input id="external-windows-confirm-fingerprint" type="checkbox" disabled> 我已从可信渠道核对该 SSH 主机指纹</label>
            <label class="check wide"><input id="external-windows-browser-mode" type="checkbox"> 同时配置登录态浏览器验收能力</label>
            <label class="wide">浏览器登录目标（启用浏览器能力时必填）<input id="external-windows-browser-target" maxlength="500" spellcheck="false" placeholder="https://example.internal/login" disabled></label>
          </div>
          <div class="form-actions external-windows-actions"><button id="probe-external-windows" type="button" class="secondary">检测 SSH</button><button id="install-external-windows" type="submit" disabled>安装并接入 Agent</button><span id="external-windows-message" role="status"></span></div>
        </form>
      </details>`;
  }

  function connectionPayload(includeFingerprint = false) {
    const payload = {
      address: byId("external-windows-address").value.trim(),
      ssh_port: Number(byId("external-windows-ssh-port").value),
      username: byId("external-windows-username").value.trim(),
      private_key: byId("external-windows-private-key").value.trim(),
    };
    if (includeFingerprint) payload.expected_fingerprint = byId("external-windows-fingerprint").value;
    return payload;
  }

  function renderNodes(items) {
    byId("external-windows-summary").textContent = items.length
      ? `${items.filter((item) => item.health?.status === "ok").length}/${items.length} 在线`
      : "尚未接入";
    byId("external-windows-nodes").innerHTML = items.map((item) => {
      const online = item.health?.status === "ok";
      const capabilities = online
        ? Object.entries(item.health.capabilities || {}).filter(([, value]) => value).map(([name]) => name).join(" · ")
        : item.health?.detail || "Agent 不可达";
      return `<article class="management-card ${online ? "ready" : "blocked"}">
        <header><div><h3>${escapeHtml(item.display_name)}</h3><p>${escapeHtml(item.username)}@${escapeHtml(item.address)}:${Number(item.agent_port)}</p></div>
          <span class="card-state ${online ? "ok" : "bad"}">${online ? "可调度" : "不可用"}</span></header>
        <dl class="management-card-facts">
          <div><dt>节点 ID</dt><dd>${escapeHtml(item.node_id)}</dd></div>
          <div><dt>模式</dt><dd>${item.browser_mode ? "浏览器验收" : "Windows 测试"}</dd></div>
          <div class="wide"><dt>能力</dt><dd class="external-node-capabilities">${escapeHtml(capabilities || "未上报")}</dd></div>
        </dl>
        <footer><button type="button" class="danger secondary" data-remove-external-node="${escapeHtml(item.node_id)}">解除接入</button></footer>
      </article>`;
    }).join("") || '<p class="management-empty">尚未接入外部 Windows 实测机。填写下方连接信息即可检测并安装 Agent。</p>';
  }

  async function loadNodes() {
    try {
      const data = await request("/api/external-windows-nodes");
      renderNodes(data.nodes || []);
    } catch (error) {
      byId("external-windows-summary").textContent = "读取失败";
      byId("external-windows-message").textContent = error.message;
    }
  }

  async function probe() {
    const button = byId("probe-external-windows");
    button.disabled = true;
    byId("external-windows-message").textContent = "正在检测 SSH、Windows 与 Python";
    try {
      const result = await request("/api/external-windows-nodes/probe", {
        method: "POST", body: JSON.stringify(connectionPayload(false)),
      });
      byId("external-windows-fingerprint").value = result.fingerprint || "";
      byId("external-windows-confirm-fingerprint").disabled = false;
      byId("external-windows-confirm-fingerprint").checked = false;
      byId("install-external-windows").disabled = true;
      byId("external-windows-message").textContent = result.detail;
      byId("external-windows-confirm-fingerprint").focus();
    } catch (error) {
      byId("external-windows-message").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  async function install(event) {
    event.preventDefault();
    const button = byId("install-external-windows");
    button.disabled = true;
    byId("external-windows-message").textContent = "正在安装 Agent；首次安装可能需要下载缺失的 Python 依赖";
    try {
      const payload = {
        ...connectionPayload(true),
        node_id: byId("external-windows-node-id").value.trim(),
        display_name: byId("external-windows-display-name").value.trim(),
        agent_port: Number(byId("external-windows-agent-port").value),
        slots: Number(byId("external-windows-slots").value),
        browser_mode: byId("external-windows-browser-mode").checked,
        browser_auth_target: byId("external-windows-browser-target").value.trim(),
      };
      await request("/api/external-windows-nodes", {method: "POST", body: JSON.stringify(payload)});
      byId("external-windows-private-key").value = "";
      byId("external-windows-message").textContent = "Agent 已安装、健康确认并注册，可以在项目质量配置中绑定节点 ID";
      await Promise.all([loadNodes(), window.loadWorkNodes?.()]);
    } catch (error) {
      byId("external-windows-message").textContent = error.message;
      button.disabled = !byId("external-windows-confirm-fingerprint").checked;
    }
  }

  mountSurface();
  byId("external-windows-confirm-fingerprint").addEventListener("change", (event) => {
    byId("install-external-windows").disabled = !event.target.checked;
  });
  byId("external-windows-browser-mode").addEventListener("change", (event) => {
    const target = byId("external-windows-browser-target");
    target.disabled = !event.target.checked;
    target.required = event.target.checked;
  });
  byId("probe-external-windows").addEventListener("click", probe);
  byId("external-windows-form").addEventListener("submit", install);
  byId("external-windows-nodes").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-remove-external-node]");
    if (!button || !window.confirm("解除后将撤销节点凭据并停止调度；Windows 上的 Agent 文件会保留。继续吗？")) return;
    button.disabled = true;
    try {
      await request(`/api/external-windows-nodes/${encodeURIComponent(button.dataset.removeExternalNode)}`, {method: "DELETE"});
      await loadNodes();
    } catch (error) {
      byId("external-windows-message").textContent = error.message;
      button.disabled = false;
    }
  });
  byId("external-windows-disclosure").addEventListener("toggle", (event) => {
    if (event.target.open) loadNodes();
  });
  byId("refresh-nodes").addEventListener("click", loadNodes);
})();
