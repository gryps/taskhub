let projectActivationState = null;
let projectActivationProjectId = null;
let seedInfrastructureReady = false;
let activationRefreshTimer = null;

function activationProjectId() {
  return byId("workflow-project")?.value || null;
}

function renderProjectActivation(data) {
  projectActivationState = data;
  projectActivationProjectId = data?.project_id || null;
  const state = byId("project-activation-state");
  const next = byId("project-activation-next");
  if (!data) {
    state.textContent = "等待项目";
    state.className = "card-state warn";
    byId("project-activation-detail").textContent = "先创建或接入 Git 项目，再配置该项目的开发边界。";
    byId("project-activation-message").textContent = "请选择或接入项目";
    byId("project-activation-count").textContent = "0/5";
    byId("project-activation-steps").innerHTML = "";
    next.textContent = "接入项目";
    next.dataset.target = "attach-project";
    return;
  }
  state.textContent = data.ready ? "可以开发" : `${data.total - data.completed} 步待完成`;
  state.className = `card-state ${data.ready ? "ok" : "warn"}`;
  byId("project-activation-detail").textContent = `${data.project_name} · 仓库接入后独立激活，不改变 Seed 全局配置。`;
  byId("project-activation-message").textContent = data.message;
  byId("project-activation-count").textContent = `${data.completed}/${data.total}`;
  byId("project-activation-steps").innerHTML = data.steps.map((step, index) => `
    <article class="project-activation-step ${step.complete ? "complete" : "pending"}">
      <header><span>${step.complete ? "✓" : index + 1}</span><h3>${escapeHtml(step.title)}</h3></header>
      <p>${escapeHtml(step.detail)}</p>
      ${step.optional ? "<small>按项目需要启用</small>" : ""}
      ${step.complete ? "" : `<button type="button" class="secondary" data-activation-target="${escapeHtml(step.target)}">去完成</button>`}
    </article>`).join("");
  next.textContent = data.ready ? "填写首个开发需求" : "继续下一步";
  next.dataset.target = data.next_target;
  renderContractQualityEditor(data);
}

function renderContractQualityEditor(data) {
  const editor = byId("project-contract-quality-editor");
  const contract = data?.contract;
  editor.classList.toggle("hidden", !contract);
  if (!contract) return;
  const textarea = byId("project-contract-quality-commands");
  if (document.activeElement !== textarea) textarea.value = data.quality_commands || "";
  textarea.disabled = !contract.editable || !canPermission("projects:manage");
  byId("save-project-contract-quality").disabled = textarea.disabled;
  byId("project-contract-quality-message").textContent = contract.editable
    ? "核对后保存，再提交评审并批准生效。"
    : "已生效契约不可直接修改；如需调整，请先创建修订版本。";
}

async function loadProjectActivation({autoOpen = false} = {}) {
  const projectId = activationProjectId();
  if (!projectId) {
    renderProjectActivation(null);
    return null;
  }
  byId("project-activation-status").textContent = "正在读取项目激活状态";
  try {
    const data = await request(`/api/projects/${encodeURIComponent(projectId)}/activation`);
    if (projectId !== activationProjectId()) return null;
    renderProjectActivation(data);
    byId("project-activation-status").textContent = data.ready ? "项目激活完成" : "按顺序处理未完成步骤";
    const dismissed = sessionStorage.getItem("taskhub_project_activation_dismissed") === projectId;
    if (autoOpen && seedInfrastructureReady && !data.ready && !dismissed) {
      showPage("workflow");
      byId("project-activation").scrollIntoView({block: "start"});
    }
    return data;
  } catch (error) {
    byId("project-activation-status").textContent = error.message;
    return null;
  }
}

function openProjectActivationTarget(target) {
  if (target === "attach-project") {
    byId("show-attach-project-form").click();
    byId("attach-project-name").focus();
    return;
  }
  if (target === "requirement") {
    byId("requirement").scrollIntoView({behavior: "smooth", block: "center"});
    byId("requirement").focus();
    return;
  }
  if (target === "project-preflight") {
    byId("project-preflight").scrollIntoView({behavior: "smooth", block: "start"});
    return;
  }
  const acceptance = target === "project-acceptance" || target === "test-environment";
  const disclosureId = acceptance || target === "repository"
    ? "project-repository-disclosure" : "project-contract-disclosure";
  const disclosure = byId(disclosureId);
  disclosure.open = true;
  disclosure.scrollIntoView({behavior: "smooth", block: "start"});
  if (acceptance) window.setTimeout(() => byId("project-quality-windows-nodes").focus(), 100);
  if (target === "project-contract-quality") {
    window.setTimeout(() => byId("project-contract-quality-commands").focus(), 100);
  }
}

function openPreflightTarget(target) {
  if (target === "model-services" || target === "nodes") {
    showPage("resources");
    const disclosure = byId(target === "model-services" ? "providers-disclosure" : "nodes-disclosure");
    disclosure.open = true;
    disclosure.scrollIntoView({behavior: "smooth", block: "start"});
    return;
  }
  openProjectActivationTarget(target);
}

async function saveContractQualityCommands() {
  const contract = projectActivationState?.contract;
  const projectId = projectActivationState?.project_id;
  if (!contract?.editable || !projectId) return;
  const button = byId("save-project-contract-quality");
  button.disabled = true;
  byId("project-contract-quality-message").textContent = "正在保存质量命令";
  try {
    await request(`/api/projects/${encodeURIComponent(projectId)}/project-contracts/${encodeURIComponent(contract.contract_id)}/versions/${contract.version}/quality-commands`, {
      method: "PUT",
      body: JSON.stringify({quality_commands: byId("project-contract-quality-commands").value}),
    });
    await Promise.all([loadCurrentProjectContract(), loadProjectPreflight()]);
    await loadProjectActivation();
    byId("project-contract-quality-message").textContent = "质量命令已写入当前契约";
  } catch (error) {
    byId("project-contract-quality-message").textContent = error.message;
  } finally {
    button.disabled = !projectActivationState?.contract?.editable || !canPermission("projects:manage");
  }
}

function scheduleActivationRefresh(autoOpen = false) {
  window.clearTimeout(activationRefreshTimer);
  activationRefreshTimer = window.setTimeout(() => loadProjectActivation({autoOpen}), 0);
}

byId("project-activation-steps").addEventListener("click", (event) => {
  const button = event.target.closest("[data-activation-target]");
  if (button) openProjectActivationTarget(button.dataset.activationTarget);
});
byId("project-activation-next").addEventListener("click", (event) => openProjectActivationTarget(event.currentTarget.dataset.target));
byId("project-activation-later").addEventListener("click", () => {
  if (projectActivationProjectId) sessionStorage.setItem("taskhub_project_activation_dismissed", projectActivationProjectId);
  showPage("tasks");
});
byId("save-project-contract-quality").addEventListener("click", saveContractQualityCommands);
window.addEventListener("taskhub:projects", () => scheduleActivationRefresh(true));
window.addEventListener("taskhub:contract", () => scheduleActivationRefresh(false));
window.addEventListener("taskhub:onboarding", (event) => {
  seedInfrastructureReady = Boolean(event.detail?.ready);
  if (seedInfrastructureReady) scheduleActivationRefresh(true);
});
window.loadProjectActivation = loadProjectActivation;
