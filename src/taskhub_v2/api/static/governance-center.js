(() => {
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
  const can = (permission) => window.taskhubCan?.(permission) ?? false;
  const labels = {
    active: "已生效", draft: "草稿", superseded: "已替代", rejected: "已拒绝",
    proposed: "待审批", approved: "已批准", revoked: "已撤销",
  };
  const categoryLabels = {
    foundation: "工程基线", architecture: "架构", quality: "质量",
    frontend: "前端", security: "安全", delivery: "交付",
  };
  let policies = [];
  let selectedPolicyVersion = null;
  let activeProjectId = "";
  const request = window.taskhubApi.request;

  function tone(status) {
    if (["active", "approved", "passed"].includes(status)) return "ok";
    if (["rejected", "revoked", "failed", "invalid"].includes(status)) return "bad";
    return "warn";
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", {hour12: false});
  }

  function ruleMeta(rule) {
    const details = [];
    if (rule.required_paths?.length) details.push(`${rule.required_paths.length} 个必需路径`);
    if (rule.required_command_groups?.length) details.push(`命令：${rule.required_command_groups.join(" / ")}`);
    if (rule.max_file_lines) details.push(`文件 ≤ ${rule.max_file_lines} 行`);
    if (rule.max_function_lines) details.push(`函数 ≤ ${rule.max_function_lines} 行`);
    if (rule.max_complexity) details.push(`复杂度 ≤ ${rule.max_complexity}`);
    if (rule.manual_evidence) details.push("需要人工证据");
    return details.join(" · ") || "按规则指令执行";
  }

  function renderRuleList(rules, allowedIds = null) {
    const visible = (rules || []).filter((rule) => !allowedIds || allowedIds.has(rule.rule_id));
    if (!visible.length) return '<p class="empty-state">没有适用规则</p>';
    return `<div class="governance-rule-list">${visible.map((rule) => `
      <article class="governance-rule">
        <header><strong>${escapeHtml(rule.title)}</strong><span>${escapeHtml(categoryLabels[rule.category] || rule.category)}</span></header>
        <code>${escapeHtml(rule.rule_id)}</code>
        <p>${escapeHtml(rule.instruction)}</p>
        <small>${escapeHtml(ruleMeta(rule))}</small>
      </article>`).join("")}</div>`;
  }

  function policyDiff(policy) {
    const previous = policies.find((item) => item.version === policy.previous_version);
    if (!previous) return '<p class="empty-state">首个规则版本，没有可比较的上一版。</p>';
    const before = new Map(previous.rules.map((rule) => [rule.rule_id, JSON.stringify(rule)]));
    const after = new Map(policy.rules.map((rule) => [rule.rule_id, JSON.stringify(rule)]));
    const added = [...after.keys()].filter((id) => !before.has(id));
    const removed = [...before.keys()].filter((id) => !after.has(id));
    const changed = [...after.keys()].filter((id) => before.has(id) && before.get(id) !== after.get(id));
    if (!added.length && !removed.length && !changed.length) {
      return '<p class="empty-state">规则内容与上一版一致。</p>';
    }
    const group = (title, values) => values.length
      ? `<div><strong>${title}</strong><span>${values.map(escapeHtml).join("、")}</span></div>` : "";
    return `<div class="governance-diff">${group("新增", added)}${group("修改", changed)}${group("移除", removed)}</div>`;
  }

  function renderPolicy(policy) {
    const root = byId("engineering-policy-root");
    if (!root || !policy) return;
    const mayManage = can("infrastructure:manage");
    byId("engineering-policy-summary").textContent = `v${policy.version} · ${labels[policy.status] || policy.status}`;
    root.innerHTML = `
      <section class="governance-overview">
        <header><div><h3>${escapeHtml(policy.name)}</h3><p>版本化规则是项目契约和批次门禁的唯一事实来源。</p></div><span class="card-state ${tone(policy.status)}">${escapeHtml(labels[policy.status] || policy.status)}</span></header>
        <div class="governance-toolbar"><label>规则版本<select id="engineering-policy-version">${policies.map((item) => `<option value="${item.version}" ${item.version === policy.version ? "selected" : ""}>v${item.version} · ${escapeHtml(labels[item.status] || item.status)}</option>`).join("")}</select></label><span>${policy.rules.length} 条规则</span></div>
        <dl class="governance-facts">
          <div><dt>内容摘要</dt><dd><code>${escapeHtml(policy.content_digest?.slice(0, 16) || "—")}</code></dd></div>
          <div><dt>规则来源</dt><dd>${escapeHtml(policy.source_reference || "未记录")}</dd></div>
          <div><dt>激活人</dt><dd>${escapeHtml(policy.activated_by || "—")}</dd></div>
          <div><dt>激活时间</dt><dd>${escapeHtml(formatDate(policy.activated_at))}</dd></div>
        </dl>
        ${renderRuleList(policy.rules)}
        <details class="governance-inline-disclosure"><summary>与上一版本比较</summary>${policyDiff(policy)}</details>
        <div class="governance-actions ${mayManage ? "" : "hidden"}">
          <button id="open-policy-draft" type="button" class="secondary">创建修订草稿</button>
          ${policy.status === "draft" ? `<button id="activate-policy" type="button">激活 v${policy.version}</button>` : ""}
          <span id="engineering-policy-message" role="status"></span>
        </div>
        <form id="engineering-policy-draft-form" class="governance-editor hidden">
          <div class="form-grid"><label>版本名称<input id="policy-draft-name" maxlength="200" required></label><label>来源说明<input id="policy-draft-source" maxlength="500"></label><label class="wide">规则 JSON<textarea id="policy-draft-rules" rows="14" spellcheck="false" required></textarea><span class="field-help">保留规则 ID；提交后先形成草稿，不会立即影响项目。</span></label></div>
          <div class="form-actions"><button type="submit">保存规则草稿</button><button id="cancel-policy-draft" type="button" class="secondary">取消</button></div>
        </form>
      </section>`;
    byId("engineering-policy-version").addEventListener("change", (event) => {
      selectedPolicyVersion = Number(event.target.value);
      renderPolicy(policies.find((item) => item.version === selectedPolicyVersion));
    });
    byId("open-policy-draft")?.addEventListener("click", () => {
      const form = byId("engineering-policy-draft-form");
      form.classList.remove("hidden");
      byId("policy-draft-name").value = `${policy.name} v${Math.max(...policies.map((item) => item.version)) + 1}`;
      byId("policy-draft-source").value = "web://engineering-governance";
      byId("policy-draft-rules").value = JSON.stringify(policy.rules, null, 2);
      byId("policy-draft-name").focus();
    });
    byId("cancel-policy-draft")?.addEventListener("click", () => byId("engineering-policy-draft-form").classList.add("hidden"));
    byId("engineering-policy-draft-form")?.addEventListener("submit", savePolicyDraft);
    byId("activate-policy")?.addEventListener("click", () => activatePolicy(policy.version));
  }

  async function loadPolicies(preferredVersion = null) {
    const root = byId("engineering-policy-root");
    if (!root) return;
    try {
      const data = await request("/api/engineering-policies");
      policies = (data.policies || []).sort((a, b) => b.version - a.version);
      const active = policies.find((item) => item.status === "active") || policies[0];
      selectedPolicyVersion = preferredVersion || selectedPolicyVersion || active?.version;
      renderPolicy(policies.find((item) => item.version === selectedPolicyVersion) || active);
    } catch (error) {
      byId("engineering-policy-summary").textContent = "读取失败";
      root.innerHTML = `<p class="governance-error">${escapeHtml(error.message)}</p>`;
    }
  }

  async function savePolicyDraft(event) {
    event.preventDefault();
    const message = byId("engineering-policy-message");
    try {
      const rules = JSON.parse(byId("policy-draft-rules").value);
      if (!Array.isArray(rules) || !rules.length) throw new Error("规则 JSON 必须是非空数组");
      message.textContent = "正在保存草稿";
      const draft = await request("/api/engineering-policies/drafts", {method: "POST", body: JSON.stringify({name: byId("policy-draft-name").value.trim(), source_reference: byId("policy-draft-source").value.trim(), rules})});
      await loadPolicies(draft.version);
    } catch (error) {
      message.textContent = error.message;
    }
  }

  async function activatePolicy(version) {
    const message = byId("engineering-policy-message");
    message.textContent = `正在激活 v${version}`;
    try {
      await request(`/api/engineering-policies/${version}/activate`, {method: "POST"});
      await loadPolicies(version);
    } catch (error) {
      message.textContent = error.message;
    }
  }

  function exceptionState(item) {
    if (item.status === "approved" && new Date(item.expires_at) <= new Date()) return ["已过期", "bad"];
    return [labels[item.status] || item.status, tone(item.status)];
  }

  function renderExceptions(items) {
    if (!items.length) return '<p class="empty-state">当前项目没有规则例外。</p>';
    const mayManage = can("projects:manage");
    return `<div class="governance-exception-list">${items.map((item) => {
      const [state, stateTone] = exceptionState(item);
      return `<article class="governance-exception"><header><div><strong>${escapeHtml(item.rule_ids.join("、"))}</strong><code>${escapeHtml(item.exception_id)}</code></div><span class="card-state ${stateTone}">${state}</span></header><p>${escapeHtml(item.reason)}</p><dl><div><dt>风险</dt><dd>${escapeHtml(item.risk)}</dd></div><div><dt>临时控制</dt><dd>${escapeHtml(item.controls.join("；"))}</dd></div><div><dt>恢复条件</dt><dd>${escapeHtml(item.recovery_condition)}</dd></div><div><dt>到期</dt><dd>${escapeHtml(formatDate(item.expires_at))}</dd></div></dl>${item.status === "proposed" && mayManage ? `<footer><button type="button" data-exception-action="approve" data-exception-id="${escapeHtml(item.exception_id)}">批准</button><button type="button" class="secondary" data-exception-action="reject" data-exception-id="${escapeHtml(item.exception_id)}">拒绝</button></footer>` : ""}</article>`;
    }).join("")}</div>`;
  }

  function exceptionForm(rules) {
    if (!can("projects:manage")) return "";
    const expiry = new Date(Date.now() + 14 * 86400000);
    expiry.setMinutes(expiry.getMinutes() - expiry.getTimezoneOffset());
    return `<details class="governance-inline-disclosure"><summary>申请临时规则例外</summary><form id="policy-exception-form" class="governance-editor"><fieldset><legend>选择规则</legend><div class="governance-rule-options">${rules.map((rule) => `<label class="check"><input type="checkbox" name="rule_id" value="${escapeHtml(rule.rule_id)}"> ${escapeHtml(rule.title)}</label>`).join("")}</div></fieldset><div class="form-grid"><label class="wide">例外原因<textarea id="exception-reason" minlength="10" maxlength="4000" required></textarea></label><label class="wide">已知风险<textarea id="exception-risk" minlength="10" maxlength="4000" required></textarea></label><label class="wide">临时控制措施<textarea id="exception-controls" required placeholder="每行一项"></textarea></label><label class="wide">恢复条件<textarea id="exception-recovery" minlength="10" maxlength="2000" required></textarea></label><label>到期时间<input id="exception-expiry" type="datetime-local" value="${expiry.toISOString().slice(0, 16)}" required></label></div><div class="form-actions"><button type="submit">提交例外申请</button><span id="policy-exception-message" role="status"></span></div></form></details>`;
  }

  function renderProjectGovernance(view) {
    const root = byId("project-governance-root");
    if (!root) return;
    const binding = view?.binding;
    if (!binding?.valid) {
      root.innerHTML = `<section class="project-governance-status bad"><header><strong>全局规则未绑定</strong><span class="card-state bad">阻塞</span></header><p>${escapeHtml(binding?.detail || "项目契约尚未建立")}</p></section>`;
      return;
    }
    const policy = binding.policy;
    const bindingRuleIds = view.contract_binding?.rule_ids || [];
    const allowedIds = new Set(bindingRuleIds);
    const applicableRules = policy.rules.filter((rule) => allowedIds.has(rule.rule_id));
    const current = Boolean(binding.current);
    root.innerHTML = `<details class="governance-inline-disclosure" open><summary><span>工程规则绑定</span><span class="card-state ${current ? "ok" : "warn"}">${current ? "当前版本" : "需要迁移"}</span></summary><div class="project-governance-content"><p>${escapeHtml(binding.detail)}</p><dl class="governance-facts"><div><dt>政策版本</dt><dd>v${policy.version}</dd></div><div><dt>绑定摘要</dt><dd><code>${escapeHtml(policy.content_digest.slice(0, 16))}</code></dd></div><div><dt>适用规则</dt><dd>${bindingRuleIds.length} 条</dd></div><div><dt>执行指令</dt><dd>${view.contract_binding?.instructions?.length || 0} 条</dd></div></dl>${renderRuleList(policy.rules, allowedIds)}${!current && can("projects:manage") ? '<button id="governance-contract-revision" type="button" class="secondary">创建合同修订并迁移</button>' : ""}<section class="governance-exceptions"><div class="section-head"><h4>规则例外</h4><span>有期限、可审计</span></div>${renderExceptions(view.exceptions || [])}${exceptionForm(applicableRules)}</section></div></details>`;
    byId("governance-contract-revision")?.addEventListener("click", () => byId("revise-project-contract")?.click());
    byId("policy-exception-form")?.addEventListener("submit", submitException);
    root.querySelectorAll("[data-exception-action]").forEach((button) => button.addEventListener("click", decideException));
  }

  async function loadProjectGovernance(projectId) {
    activeProjectId = projectId || "";
    const root = byId("project-governance-root");
    if (!root) return;
    if (!activeProjectId) {
      root.innerHTML = '<p class="empty-state">选择项目后查看规则绑定。</p>';
      return;
    }
    root.innerHTML = '<p class="muted">正在核对项目规则绑定。</p>';
    try {
      renderProjectGovernance(await request(`/api/projects/${encodeURIComponent(activeProjectId)}/engineering-governance`));
    } catch (error) {
      root.innerHTML = `<p class="governance-error">${escapeHtml(error.message)}</p>`;
    }
  }

  async function submitException(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const message = byId("policy-exception-message");
    const ruleIds = [...form.querySelectorAll('input[name="rule_id"]:checked')].map((item) => item.value);
    if (!ruleIds.length) return void (message.textContent = "请至少选择一条规则");
    try {
      message.textContent = "正在提交";
      await request(`/api/projects/${encodeURIComponent(activeProjectId)}/policy-exceptions`, {method: "POST", body: JSON.stringify({rule_ids: ruleIds, reason: byId("exception-reason").value.trim(), risk: byId("exception-risk").value.trim(), controls: byId("exception-controls").value.split("\n").map((item) => item.trim()).filter(Boolean), recovery_condition: byId("exception-recovery").value.trim(), expires_at: new Date(byId("exception-expiry").value).toISOString()})});
      await loadProjectGovernance(activeProjectId);
    } catch (error) {
      message.textContent = error.message;
    }
  }

  async function decideException(event) {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await request(`/api/projects/${encodeURIComponent(activeProjectId)}/policy-exceptions/${encodeURIComponent(button.dataset.exceptionId)}/${button.dataset.exceptionAction}`, {method: "POST"});
      await loadProjectGovernance(activeProjectId);
    } catch (error) {
      button.disabled = false;
      button.closest("article").querySelector("p").textContent = error.message;
    }
  }

  function renderBatchGovernance(data) {
    for (const batch of data?.batches || []) {
      const host = document.querySelector(`[data-batch-governance="${CSS.escape(batch.batch_id)}"]`);
      if (!host) continue;
      const gate = batch.governance_gate || {};
      if (!gate.status) {
        host.innerHTML = '<span class="governance-gate pending">工程治理 · 门禁尚未执行</span>';
        continue;
      }
      const failed = gate.failed || [];
      const gateTone = gate.status === "passed" ? "ok" : gate.status === "warning" ? "warn" : "bad";
      host.innerHTML = `<div class="governance-gate ${gateTone}"><strong>工程治理 · ${gate.status === "passed" ? "通过" : gate.status === "warning" ? "提醒" : "失败"}</strong><span>${gate.passed || 0} 通过 · ${gate.warnings || 0} 提醒 · ${failed.length} 失败</span></div>${failed.length ? `<details><summary>查看失败明细</summary><ul>${failed.map((item) => `<li>${escapeHtml(item.summary || "治理门禁失败")}${item.path ? `<code>${escapeHtml(item.path)}</code>` : ""}</li>`).join("")}</ul></details>` : ""}`;
    }
  }

  window.addEventListener("taskhub:projects", () => {
    loadPolicies();
    loadProjectGovernance(byId("workflow-project")?.value || "");
  });
  window.addEventListener("taskhub:contract", (event) => loadProjectGovernance(event.detail?.projectId));
  window.addEventListener("taskhub:execution-plan", (event) => renderBatchGovernance(event.detail));
  window.loadEngineeringPolicies = loadPolicies;
})();
