(() => {
  const registries = [
    {
      name: "GitHub Container Registry",
      shortName: "GHCR",
      images: [
        ["Seed", "ghcr.io/gryps/taskhub-seed:0.1.0-alpha"],
        ["Node", "ghcr.io/gryps/taskhub-node:0.1.0-alpha"],
      ],
    },
    {
      name: "阿里云容器镜像服务",
      shortName: "杭州 ACR",
      images: [
        ["Seed", "crpi-kgqnka7pmz9f3sml.cn-hangzhou.personal.cr.aliyuncs.com/taskhub-v2/taskhub-seed:0.1.0-alpha"],
        ["Node", "crpi-kgqnka7pmz9f3sml.cn-hangzhou.personal.cr.aliyuncs.com/taskhub-v2/taskhub-node:0.1.0-alpha"],
      ],
    },
  ];

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);

  function render() {
    document.querySelectorAll("[data-public-image-downloads]").forEach((host) => {
      const requestedRole = host.dataset.imageRole;
      const groups = registries.map((registry) => `
        <section class="image-registry-group">
          <div><strong>${escapeHtml(registry.name)}</strong><span>${escapeHtml(registry.shortName)}</span></div>
          ${registry.images.filter(([role]) => !requestedRole || role.toLowerCase() === requestedRole)
            .map(([role, reference]) => `
          <div class="image-download-row">
            <span>${escapeHtml(role)}</span>
            <code>${escapeHtml(reference)}</code>
            <button type="button" class="secondary copy-image-reference" data-image-reference="${escapeHtml(reference)}" aria-label="复制 ${escapeHtml(role)} 镜像拉取命令" aria-live="polite">复制 pull</button>
          </div>`).join("")}
        </section>`).join("");
      const title = requestedRole === "node" ? "公开工作节点镜像" : "公开镜像下载";
      const description = requestedRole === "node"
        ? "初始化时由 Seed 拉取，创建本机节点时直接使用；国内网络可改用阿里云 ACR 地址。"
        : "两个镜像仓库均支持匿名拉取。国内网络优先使用阿里云 ACR。";
      host.innerHTML = `<details class="public-image-downloads">
        <summary><span><strong>${title}</strong><small>0.1.0-alpha · linux/amd64</small></span><span>GHCR · 阿里云 ACR</span></summary>
        <div class="image-download-content">
          <p>${description}</p>
          <div class="image-registry-grid">${groups}</div>
        </div>
      </details>`;
    });
  }

  async function copyReference(button) {
    const command = `docker pull ${button.dataset.imageReference}`;
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(command);
    } else {
      const input = document.createElement("textarea");
      input.value = command;
      input.setAttribute("readonly", "");
      document.body.appendChild(input);
      input.select();
      document.execCommand("copy");
      input.remove();
    }
    button.textContent = "已复制";
    window.setTimeout(() => { button.textContent = "复制 pull"; }, 1600);
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".copy-image-reference");
    if (button) copyReference(button).catch(() => { button.textContent = "复制失败"; });
  });
  render();
})();
