let capabilityProjectId = null;
let capabilitySpec = null;
let capabilityContract = null;
let capabilityInventory = [];

function capabilityById(id) { return document.getElementById(id); }
function capabilityEscape(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}
function capabilityCookie(name) {
  const value = document.cookie.split("; ").find((item) => item.startsWith(`${name}=`));
  return value ? decodeURIComponent(value.split("=").slice(1).join("=")) : "";
}
async function capabilityRequest(path, options = {}) {
  const method = options.method || "GET";
  const headers = {"Content-Type": "application/json", ...(options.headers || {})};
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers["X-CSRF-Token"] = capabilityCookie("taskhub_v2_csrf");
  }
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

function renderCapabilityInventory() {
  const stateLabels = {trusted: "受信可用", draft: "待审查", disabled: "已停用", rejected: "已拒绝"};
  const mayManage = typeof canPermission === "function" && canPermission("infrastructure:manage");
  capabilityById("capability-inventory-summary").textContent = `${capabilityInventory.length} 个版本`;
  capabilityById("capability-inventory").innerHTML = capabilityInventory.length
    ? capabilityInventory.map((pack) => {
      const actions = !mayManage ? "" : pack.status === "draft"
        ? `${pack.contains_executable ? `<label class="capability-executable-confirm"><input type="checkbox" data-executable-confirm="${capabilityEscape(pack.pack_id)}@${capabilityEscape(pack.version)}"> 已审查可执行内容与所需权限</label>` : ""}<button data-pack-action="trust" data-pack-id="${capabilityEscape(pack.pack_id)}" data-pack-version="${capabilityEscape(pack.version)}" data-pack-executable="${pack.contains_executable ? "true" : "false"}">审查并信任</button>`
        : ["trusted", "disabled"].includes(pack.status)
          ? `<button class="secondary" data-pack-action="${pack.status === "trusted" ? "disable" : "enable"}" data-pack-id="${capabilityEscape(pack.pack_id)}" data-pack-version="${capabilityEscape(pack.version)}">${pack.status === "trusted" ? "停用" : "重新启用"}</button>` : "";
      return `<article class="management-card capability-inventory-card"><header><div><h3>${capabilityEscape(pack.name || pack.pack_id)}</h3><p>${capabilityEscape(pack.pack_type)} · ${capabilityEscape(pack.pack_id)}@${capabilityEscape(pack.version)}</p></div><span class="card-state ${pack.status === "trusted" ? "ok" : pack.status === "disabled" ? "bad" : "warn"}">${stateLabels[pack.status] || pack.status}</span></header><dl><div><dt>来源</dt><dd>${capabilityEscape(pack.source)}</dd></div><div><dt>许可证</dt><dd>${capabilityEscape(pack.license || "未声明")}</dd></div><div><dt>摘要</dt><dd>${capabilityEscape(pack.manifest_digest?.slice(0, 12) || "待生成")}</dd></div></dl><p>${capabilityEscape(pack.summary)}</p>${actions ? `<footer class="management-card-actions">${actions}</footer>` : ""}</article>`;
    }).join("") : '<p class="empty-state">尚无能力包</p>';
}

function previewMarkup(preview) {
  const theme = preview.theme || {};
  return `<div class="capability-preview" style="--pack-accent:${capabilityEscape(theme.primary || "#1677ff")};--pack-radius:${capabilityEscape(theme.radius || "8px")}"><strong>${capabilityEscape(preview.label)}</strong><div><i></i><span></span><span></span><span></span></div></div>`;
}

function renderCapabilityRecommendations(items, current) {
  const active = current.active_lock;
  const draft = (current.draft_locks || [])[0];
  const display = draft || active;
  const design = current.design_contract;
  const activeMatchesSpec = active && active.spec_id === capabilitySpec?.spec_id
    && active.spec_version === capabilitySpec?.version;
  const state = capabilityById("active-design-state");
  state.textContent = draft ? "待激活" : activeMatchesSpec ? "已锁定" : active ? "需重新锁定" : "未锁定";
  state.className = `card-state ${activeMatchesSpec && !draft ? "ok" : "warn"}`;
  capabilityById("capability-summary").textContent = draft
    ? `v${draft.version} · 待激活` : activeMatchesSpec ? `v${active.version} · ${active.pack_refs.length} 个精确版本` : active ? "当前规格尚未锁定" : "尚未选择";
  capabilityById("active-design-detail").textContent = draft
    ? "新组合尚未影响项目；检查迁移任务后明确激活。"
    : design ? `${design.contract_id} · v${design.version}` : "从下方兼容方案中选择并生成版本锁。";
  capabilityById("active-design-facts").innerHTML = display ? [
    ["版本锁", `${display.lock_id} · v${display.version}`],
    ["能力版本", `${display.pack_refs.length} 个`],
    ["规格版本", `${display.spec_id} · v${display.spec_version}`],
    ["验证证据", `${(design?.validation_evidence || []).length} 类`],
  ].map(([label, value]) => `<div><span>${label}</span><strong>${capabilityEscape(value)}</strong></div>`).join("") : "";
  const migrations = draft?.migration_tasks || [];
  const migrationHost = capabilityById("capability-migrations");
  migrationHost.classList.toggle("hidden", !draft);
  migrationHost.innerHTML = draft ? `<h4>升级迁移计划</h4>${migrations.length ? `<ul>${migrations.map((item) => `<li>${capabilityEscape(item)}</li>`).join("")}</ul>` : "<p>首次锁定，无历史迁移任务。</p>"}<button data-activate-lock="${draft.version}" data-permission="projects:manage">激活版本锁 v${draft.version}</button>` : "";
  const maySelect = typeof canPermission === "function" && canPermission("projects:manage");
  const specEditable = ["draft", "in_review"].includes(capabilitySpec?.status);
  capabilityById("capability-recommendations").innerHTML = items.length ? items.map((item) => {
    const selected = active && active.spec_id === capabilitySpec?.spec_id
      && active.spec_version === capabilitySpec?.version
      && JSON.stringify(active.pack_refs) === JSON.stringify(item.pack_refs);
    const actionLabel = selected ? "当前方案" : !specEditable ? "先创建规格修订" : active ? "生成升级计划" : "选择并锁定";
    return `<article class="management-card capability-option-card"><header><div><h3>${capabilityEscape(item.name)}</h3><p>${capabilityEscape(item.summary)}</p></div><span class="card-state ok">兼容</span></header><div class="capability-previews">${item.previews.map(previewMarkup).join("")}</div><details><summary>查看 4 个精确版本与验证范围</summary><ul>${item.pack_refs.map((ref) => `<li>${capabilityEscape(ref)}</li>`).join("")}</ul></details>${maySelect ? `<footer class="management-card-actions"><button data-select-packs="${capabilityEscape(item.recommendation_id)}" ${selected || !specEditable ? "disabled" : ""}>${actionLabel}</button></footer>` : ""}</article>`;
  }).join("") : '<p class="empty-state">当前项目尚无可推荐方案；请先激活前端项目契约并生成产品规格。</p>';
  capabilityById("capability-recommendations").dataset.items = JSON.stringify(items);
}

async function loadCapabilityCenter(projectId, spec = null, contract = null) {
  capabilityProjectId = projectId;
  capabilitySpec = spec;
  capabilityContract = contract;
  const inventory = await capabilityRequest("/api/capability-packs");
  capabilityInventory = inventory.capability_packs || [];
  renderCapabilityInventory();
  const disclosure = capabilityById("capability-disclosure");
  disclosure.classList.toggle("hidden", !projectId);
  if (!projectId) return;
  const current = await capabilityRequest(`/api/projects/${encodeURIComponent(projectId)}/capability-lock`);
  let recommendations = [];
  if (spec && contract?.status === "active") {
    const query = `spec_id=${encodeURIComponent(spec.spec_id)}&spec_version=${spec.version}`;
    const response = await capabilityRequest(`/api/projects/${encodeURIComponent(projectId)}/capability-recommendations?${query}`).catch((error) => {
      capabilityById("capability-message").textContent = error.message;
      return {recommendations: []};
    });
    recommendations = response.recommendations || [];
  }
  renderCapabilityRecommendations(recommendations, current);
}
window.loadCapabilityCenter = loadCapabilityCenter;

capabilityById("capability-recommendations").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-select-packs]");
  if (!button || !capabilityProjectId || !capabilitySpec) return;
  const items = JSON.parse(capabilityById("capability-recommendations").dataset.items || "[]");
  const selected = items.find((item) => item.recommendation_id === button.dataset.selectPacks);
  if (!selected) return;
  button.disabled = true;
  try {
    await capabilityRequest(`/api/projects/${encodeURIComponent(capabilityProjectId)}/capability-locks`, {method: "POST", body: JSON.stringify({spec_id: capabilitySpec.spec_id, spec_version: capabilitySpec.version, pack_refs: selected.pack_refs})});
    capabilityById("capability-message").textContent = "版本锁草稿已生成；明确激活前不会改变项目";
    await loadCapabilityCenter(capabilityProjectId, capabilitySpec, capabilityContract);
  } catch (error) { capabilityById("capability-message").textContent = error.message; button.disabled = false; }
});

capabilityById("capability-migrations").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-activate-lock]");
  if (!button || !capabilityProjectId) return;
  button.disabled = true;
  try {
    await capabilityRequest(`/api/projects/${encodeURIComponent(capabilityProjectId)}/capability-locks/${button.dataset.activateLock}/activate`, {method: "POST"});
    capabilityById("capability-message").textContent = "能力版本锁和项目设计合同已激活";
    await loadCapabilityCenter(capabilityProjectId, capabilitySpec, capabilityContract);
  } catch (error) { capabilityById("capability-message").textContent = error.message; button.disabled = false; }
});

capabilityById("capability-import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = capabilityById("capability-inventory-message");
  try {
    const manifest = JSON.parse(capabilityById("capability-import-manifest").value);
    await capabilityRequest("/api/capability-packs/import", {method: "POST", body: JSON.stringify({manifest})});
    event.target.reset();
    message.textContent = "清单已安全导入为待审查草稿";
    await loadCapabilityCenter(capabilityProjectId, capabilitySpec, capabilityContract);
  } catch (error) { message.textContent = error.message; }
});

capabilityById("capability-inventory").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-pack-action]");
  if (!button) return;
  button.disabled = true;
  const root = `/api/capability-packs/${encodeURIComponent(button.dataset.packId)}/versions/${encodeURIComponent(button.dataset.packVersion)}`;
  try {
    if (button.dataset.packAction === "trust") {
      const executable = button.dataset.packExecutable === "true";
      const confirmed = capabilityById("capability-inventory").querySelector(`[data-executable-confirm="${CSS.escape(`${button.dataset.packId}@${button.dataset.packVersion}`)}"]`)?.checked || false;
      if (executable && !confirmed) throw new Error("请先确认已审查可执行内容与所需权限");
      await capabilityRequest(`${root}/trust`, {method: "POST", body: JSON.stringify({executable_confirmed: confirmed})});
    } else {
      await capabilityRequest(`${root}/availability`, {method: "POST", body: JSON.stringify({enabled: button.dataset.packAction === "enable", reason: "管理员从能力包库存变更"})});
    }
    await loadCapabilityCenter(capabilityProjectId, capabilitySpec, capabilityContract);
  } catch (error) { capabilityById("capability-inventory-message").textContent = error.message; button.disabled = false; }
});
