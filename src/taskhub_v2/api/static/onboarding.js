let latestOnboarding = null;

function onboardingBytes(value) {
  if (!value) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = Number(value);
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount.toFixed(unit >= 3 ? 1 : 0)} ${units[unit]}`;
}

function renderOnboarding(data) {
  latestOnboarding = data;
  const result = byId("onboarding-result");
  result.className = `onboarding-result ${data.ready ? "ready" : "pending"}`;
  byId("onboarding-message").textContent = data.message;
  byId("onboarding-progress").textContent = `${data.completed}/${data.total}`;
  const engine = data.seed?.docker?.engine || {};
  const storage = data.seed?.storage || {};
  byId("onboarding-seed-facts").innerHTML = `
    <div><span>Docker Engine</span><strong>${escapeHtml(engine.version || "不可用")}</strong><small>${escapeHtml(engine.operating_system || data.seed?.docker?.detail || "—")}</small></div>
    <div><span>CPU</span><strong>${escapeHtml(engine.cpu_count || "—")} 核</strong><small>${escapeHtml(engine.architecture || "—")}</small></div>
    <div><span>内存</span><strong>${onboardingBytes(engine.memory_total_bytes)}</strong><small>Docker 可用资源</small></div>
    <div><span>持久化磁盘</span><strong>${onboardingBytes(storage.free_bytes)} 可用</strong><small>${escapeHtml(storage.path || "—")}</small></div>`;
  byId("onboarding-steps").innerHTML = data.steps.map((step, index) => `
    <article class="onboarding-step ${step.complete ? "complete" : "pending"}">
      <span class="onboarding-step-number">${step.complete ? "✓" : index + 1}</span>
      <div><h3>${escapeHtml(step.title)}</h3><p>${escapeHtml(step.detail)}</p></div>
      ${step.complete ? '<span class="onboarding-step-state ok">已完成</span>' :
        `<button type="button" class="secondary onboarding-step-action" data-target="${escapeHtml(step.target)}">去完成</button>`}
    </article>`).join("");
  byId("onboarding-restart").classList.toggle("hidden", !data.restart_required);
  if (!byId("offline-image-reference").value) {
    byId("offline-image-reference").value = data.image?.reference || "taskhub-node:0.1.0-alpha";
  }
}

async function loadOnboarding(force = false) {
  try {
    const data = await request("/api/onboarding/status");
    renderOnboarding(data);
    const dismissed = sessionStorage.getItem("taskhub_onboarding_dismissed") === "true";
    const open = force || (!data.ready && !dismissed);
    if (open) showPage("onboarding");
    return open;
  } catch (error) {
    byId("onboarding-message").textContent = error.message;
    if (force) showPage("onboarding");
    return force;
  }
}

function openOnboardingTarget(target) {
  if (target === "image") {
    byId("offline-image-form").classList.remove("hidden");
    byId("offline-image-reference").focus();
    return;
  }
  const disclosures = {
    system: "system-disclosure", providers: "providers-disclosure",
    nodes: "nodes-disclosure", platform: "platform-disclosure",
  };
  const disclosure = byId(disclosures[target] || "system-disclosure");
  showPage("resources");
  document.querySelectorAll(".resource-disclosure").forEach((item) => {
    item.open = item === disclosure;
  });
  disclosure?.scrollIntoView({behavior: "smooth", block: "start"});
}

async function importOfflineImage(event) {
  event.preventDefault();
  const button = byId("import-offline-image");
  const file = byId("offline-image-file").files[0];
  if (!file) return;
  button.disabled = true;
  byId("offline-image-message").textContent = `正在上传并导入 ${file.name}，请勿关闭页面`;
  try {
    const result = await request("/api/onboarding/offline-image", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-tar",
        "X-TaskHub-Image": byId("offline-image-reference").value,
      },
      body: file,
    });
    byId("offline-image-message").textContent =
      `导入完成 · ${result.architecture}/${result.os} · SHA256 ${result.archive_sha256.slice(0, 12)}`;
    byId("offline-image-file").value = "";
    await loadOnboarding(true);
  } catch (error) {
    byId("offline-image-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function restartSeed() {
  if (!window.confirm("确认重启 Seed 控制器以应用配置？页面会在服务恢复后自动刷新。")) return;
  const button = byId("restart-seed");
  button.disabled = true;
  button.textContent = "正在重启";
  try {
    await request("/api/onboarding/restart", {method: "POST"});
  } catch (error) {
    button.disabled = false;
    button.textContent = error.message;
    return;
  }
  const started = Date.now();
  const deadline = started + 90000;
  let observedRestart = false;
  const waitForHealth = async () => {
    try {
      const response = await fetch("/api/health", {cache: "no-store"});
      if (response.ok && (observedRestart || Date.now() - started > 8000)) {
        window.location.reload();
        return;
      }
    } catch (_error) {
      observedRestart = true;
    }
    if (Date.now() < deadline) setTimeout(waitForHealth, 2000);
    else {
      button.disabled = false;
      button.textContent = "服务未恢复，请检查 Docker";
    }
  };
  setTimeout(waitForHealth, 2000);
}

byId("onboarding-steps").addEventListener("click", (event) => {
  const button = event.target.closest(".onboarding-step-action");
  if (button) openOnboardingTarget(button.dataset.target);
});
byId("refresh-onboarding").addEventListener("click", () => loadOnboarding(true));
byId("open-onboarding").addEventListener("click", () => {
  sessionStorage.removeItem("taskhub_onboarding_dismissed");
  loadOnboarding(true);
});
byId("onboarding-later").addEventListener("click", () => {
  sessionStorage.setItem("taskhub_onboarding_dismissed", "true");
  showPage("tasks");
});
byId("offline-image-form").addEventListener("submit", importOfflineImage);
byId("cancel-offline-image").addEventListener("click", () => {
  byId("offline-image-form").classList.add("hidden");
});
byId("restart-seed").addEventListener("click", restartSeed);
window.loadOnboarding = loadOnboarding;
if (!byId("workspace").classList.contains("hidden")) {
  setTimeout(() => loadOnboarding(), 0);
}
