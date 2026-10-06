(() => {
  const request = window.taskhubApi.request;
  const disclosure = document.getElementById("access-security-disclosure");
  const userForm = document.getElementById("user-form");
  if (!disclosure || !userForm) return;

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
  const dateTime = (value) => value
    ? new Intl.DateTimeFormat("zh-CN", {dateStyle: "medium", timeStyle: "short"})
      .format(new Date(Number(value) * 1000))
    : "尚未使用";
  const roleNames = {
    project_owner: "项目负责人", developer: "开发人员", auditor: "只读审计人员",
  };

  userForm.insertAdjacentHTML("afterend", `
    <section id="agent-access" class="agent-access" aria-labelledby="agent-access-title">
      <header class="agent-access-head">
        <div><h3 id="agent-access-title">开发代理连接</h3>
          <p>代理发起配对后由管理员确认。凭据可撤销、有到期时间，不使用管理员密码或浏览器 Cookie。</p></div>
        <button id="refresh-agent-access" type="button" class="secondary">刷新连接</button>
      </header>
      <div id="agent-pairings" class="agent-access-list" aria-live="polite"></div>
      <div id="agent-credentials" class="agent-access-list"></div>
      <p id="agent-access-message" class="muted" role="status"></p>
    </section>`);

  const pairings = document.getElementById("agent-pairings");
  const credentials = document.getElementById("agent-credentials");
  const message = document.getElementById("agent-access-message");

  function pairingView(item) {
    return `<article class="agent-access-row" data-pairing-id="${escapeHtml(item.pairing_id)}">
      <div class="agent-access-primary"><strong>${escapeHtml(item.label)}</strong>
        <code>${escapeHtml(item.user_code)}</code>
        <small>将在 ${escapeHtml(dateTime(item.expires_at))} 失效</small></div>
      <label>授权角色<select class="agent-role">
        <option value="project_owner">项目负责人</option>
        <option value="developer">开发人员</option>
        <option value="auditor">只读审计人员</option>
      </select></label>
      <label>有效期<select class="agent-days">
        <option value="30">30 天</option><option value="7">7 天</option><option value="90">90 天</option>
      </select></label>
      <div class="agent-access-actions"><button type="button" class="approve-agent">批准</button>
        <button type="button" class="secondary reject-agent">拒绝</button></div>
    </article>`;
  }

  function credentialView(item) {
    const status = item.revoked_at ? "已撤销" : Number(item.expires_at) <= Date.now() / 1000
      ? "已过期" : "可用";
    return `<article class="agent-access-row agent-credential-row">
      <div class="agent-access-primary"><strong>${escapeHtml(item.label)}</strong>
        <span>${escapeHtml(roleNames[item.role] || item.role)} · ${escapeHtml(status)}</span>
        <small>最近使用：${escapeHtml(dateTime(item.last_used_at))} · 到期：${escapeHtml(dateTime(item.expires_at))}</small></div>
      <span class="agent-owner">授权人：${escapeHtml(item.owner)}</span>
      ${item.revoked_at ? "" : `<button type="button" class="secondary revoke-agent"
        data-credential-id="${escapeHtml(item.credential_id)}">撤销</button>`}
    </article>`;
  }

  async function loadAgentAccess() {
    message.textContent = "正在读取代理连接";
    try {
      const [pairingData, credentialData] = await Promise.all([
        request("/api/auth/agent-pairings"), request("/api/auth/agent-credentials"),
      ]);
      pairings.innerHTML = `<h4>等待审批</h4>${pairingData.pairings.length
        ? pairingData.pairings.map(pairingView).join("")
        : '<p class="agent-access-empty">没有等待审批的连接。请先在开发代理中发起配对。</p>'}`;
      credentials.innerHTML = `<h4>已授权连接</h4>${credentialData.credentials.length
        ? credentialData.credentials.map(credentialView).join("")
        : '<p class="agent-access-empty">尚未授权开发代理。</p>'}`;
      message.textContent = "";
    } catch (error) {
      pairings.innerHTML = "";
      credentials.innerHTML = "";
      message.textContent = error.message;
    }
  }

  async function decidePairing(row, action) {
    const buttons = row.querySelectorAll("button");
    buttons.forEach((button) => { button.disabled = true; });
    message.textContent = action === "approve" ? "正在批准连接" : "正在拒绝连接";
    const options = action === "approve" ? {
      method: "POST",
      body: JSON.stringify({
        role: row.querySelector(".agent-role").value,
        expires_days: Number(row.querySelector(".agent-days").value),
      }),
    } : {method: "POST"};
    try {
      await request(`/api/auth/agent-pairings/${encodeURIComponent(row.dataset.pairingId)}/${action}`, options);
      message.textContent = action === "approve" ? "连接已批准，等待代理领取凭据" : "连接已拒绝";
      await loadAgentAccess();
    } catch (error) {
      message.textContent = error.message;
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  pairings.addEventListener("click", (event) => {
    const row = event.target.closest("[data-pairing-id]");
    if (!row) return;
    if (event.target.closest(".approve-agent")) decidePairing(row, "approve");
    if (event.target.closest(".reject-agent")) decidePairing(row, "reject");
  });
  credentials.addEventListener("click", async (event) => {
    const button = event.target.closest(".revoke-agent");
    if (!button || !window.confirm("撤销后该开发代理会立即失去访问权限，确认继续？")) return;
    button.disabled = true;
    try {
      await request(`/api/auth/agent-credentials/${encodeURIComponent(button.dataset.credentialId)}`, {method: "DELETE"});
      message.textContent = "代理连接已撤销";
      await loadAgentAccess();
    } catch (error) {
      message.textContent = error.message;
      button.disabled = false;
    }
  });
  document.getElementById("refresh-agent-access").addEventListener("click", loadAgentAccess);
  disclosure.addEventListener("toggle", () => { if (disclosure.open) loadAgentAccess(); });
})();
