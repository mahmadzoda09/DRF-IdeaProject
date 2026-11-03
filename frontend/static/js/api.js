// Клиент для Pharmacy API: JWT в localStorage, автообновление access токена.
const api = (() => {
  const BASE = "/api";

  const tokens = {
    get access() { return localStorage.getItem("access"); },
    get refresh() { return localStorage.getItem("refresh"); },
    set(access, refresh) {
      localStorage.setItem("access", access);
      if (refresh) localStorage.setItem("refresh", refresh);
    },
    clear() {
      localStorage.removeItem("access");
      localStorage.removeItem("refresh");
    },
  };

  class ApiError extends Error {
    constructor(status, data) {
      super(formatError(data) || t("error", { status }));
      this.status = status;
      this.data = data;
    }
  }

  // {"items": ["..."], "price": ["..."]} -> читаемый текст
  function formatError(data) {
    if (!data) return "";
    if (typeof data === "string") return data;
    if (Array.isArray(data)) return data.map(formatError).join("\n");
    return Object.entries(data)
      .map(([key, val]) => {
        const text = formatError(val);
        return key === "detail" || key === "non_field_errors" ? text : `${key}: ${text}`;
      })
      .join("\n");
  }

  let onUnauthorized = () => {};

  async function refreshAccess() {
    if (!tokens.refresh) return false;
    const res = await fetch(`${BASE}/auth/refresh/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh: tokens.refresh }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    tokens.set(data.access, data.refresh);
    return true;
  }

  async function request(method, path, body, retry = true) {
    const url = path.startsWith("http") ? path : `${BASE}${path}`;
    // FormData (файлы) — браузер сам ставит multipart с границей; остальное — JSON
    const form = body instanceof FormData;
    const headers = { "Accept-Language": i18n.lang };
    if (!form) headers["Content-Type"] = "application/json";
    if (tokens.access) headers.Authorization = `Bearer ${tokens.access}`;

    const res = await fetch(url, {
      method,
      headers,
      body: body === undefined || form ? body : JSON.stringify(body),
    });

    if (res.status === 401 && retry && (await refreshAccess())) {
      return request(method, path, body, false);
    }
    if (res.status === 401) {
      tokens.clear();
      onUnauthorized();
    }

    const data = res.status === 204 ? null : await res.json().catch(() => null);
    if (!res.ok) throw new ApiError(res.status, data);
    return data;
  }

  function query(params = {}) {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== "" && v !== null && v !== undefined) q.set(k, v);
    });
    const s = q.toString();
    return s ? `?${s}` : "";
  }

  // Загрузить все страницы списка (для выпадающих списков)
  async function getAll(path, params) {
    let data = await request("GET", path + query(params));
    const results = [...data.results];
    while (data.next) {
      data = await request("GET", data.next);
      results.push(...data.results);
    }
    return results;
  }

  // Скачать файл (отчёт): нужен токен, поэтому fetch + blob, а не обычная ссылка
  async function download(path, params, retry = true) {
    const headers = { "Accept-Language": i18n.lang };
    if (tokens.access) headers.Authorization = `Bearer ${tokens.access}`;
    const res = await fetch(`${BASE}${path}${query(params)}`, { headers });
    if (res.status === 401 && retry && (await refreshAccess())) return download(path, params, false);
    if (res.status === 401) {
      tokens.clear();
      onUnauthorized();
    }
    if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null));

    const disposition = res.headers.get("Content-Disposition") || "";
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    const plain = disposition.match(/filename="?([^";]+)"?/i);
    const name = encoded ? decodeURIComponent(encoded[1]) : plain ? plain[1] : "report";
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return name;
  }

  return {
    tokens,
    download,
    query,
    getAll,
    set onUnauthorized(fn) { onUnauthorized = fn; },
    get: (path, params) => request("GET", path + query(params)),
    post: (path, body) => request("POST", path, body ?? {}),
    patch: (path, body) => request("PATCH", path, body),
    put: (path, body) => request("PUT", path, body),
    del: (path) => request("DELETE", path),

    async login(username, password) {
      const data = await request("POST", "/auth/login/", { username, password }, false);
      tokens.set(data.access, data.refresh);
      return data.user;
    },
  };
})();
