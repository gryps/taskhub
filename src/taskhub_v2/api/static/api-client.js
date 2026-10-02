(() => {
  const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

  function cookie(name) {
    const item = document.cookie.split("; ").find((value) => value.startsWith(`${name}=`));
    return item ? decodeURIComponent(item.split("=").slice(1).join("=")) : "";
  }

  function errorDetail(payload, status) {
    const raw = payload.detail || `HTTP ${status}`;
    if (Array.isArray(raw)) {
      return raw.map((item) => item?.msg || item?.detail || String(item)).join("；");
    }
    if (typeof raw === "object") return raw.message || JSON.stringify(raw);
    return raw;
  }

  async function request(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const headers = {"Content-Type": "application/json", ...(options.headers || {})};
    if (!SAFE_METHODS.has(method)) headers["X-CSRF-Token"] = cookie("taskhub_v2_csrf");
    const response = await fetch(path, {...options, headers});
    if (!response.ok) {
      const responseText = await response.text();
      let payload = {};
      try {
        payload = responseText ? JSON.parse(responseText) : {};
      } catch (_error) {
        payload = {};
      }
      if (response.status === 401 && !path.startsWith("/api/auth/")) {
        window.dispatchEvent(new CustomEvent("taskhub:unauthorized"));
      }
      throw new Error(errorDetail(payload, response.status));
    }
    return response.json();
  }

  window.taskhubApi = Object.freeze({cookie, request});
})();
