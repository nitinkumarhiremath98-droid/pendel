// API client + auth/session store
const listeners = new Set();
export const session = {
  token: localStorage.getItem("rf_token") || null,
  user: null,
  config: null,
  get premium() { return this.user?.plan === "premium"; },
  get isAdmin() { return !!this.user?.admin_role; },
};

export function onSession(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function emitSession() { listeners.forEach((fn) => fn(session)); }

export function setAuth(token, user) {
  session.token = token;
  session.user = user;
  if (token) localStorage.setItem("rf_token", token); else localStorage.removeItem("rf_token");
  emitSession();
}

export class ApiError extends Error {
  constructor(status, message, data) { super(message); this.status = status; this.data = data; }
}

let inflight = 0;
function loading(delta) {
  inflight += delta;
  let bar = document.getElementById("loading-line");
  if (!bar) { bar = document.createElement("div"); bar.id = "loading-line"; bar.className = "loading-line"; document.body.append(bar); }
  if (inflight > 0) { bar.style.opacity = "1"; bar.style.width = "70%"; }
  else { bar.style.width = "100%"; setTimeout(() => { if (inflight === 0) { bar.style.opacity = "0"; bar.style.width = "0"; } }, 250); }
}

export async function api(path, { method = "GET", body, headers = {}, raw = false, quiet = false } = {}) {
  const h = { ...headers };
  if (session.token) h.Authorization = `Bearer ${session.token}`;
  let payload = body;
  if (body !== undefined && !raw) { h["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  if (!quiet) loading(1);
  let res;
  try {
    res = await fetch(path, { method, headers: h, body: payload });
  } catch (e) {
    throw new ApiError(0, "You appear to be offline. Check your connection and try again.");
  } finally {
    if (!quiet) loading(-1);
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) {
    if (res.status === 401 && session.token && data?.code !== "auth_required") setAuth(null, null);
    if (res.status === 401 && session.token && data?.code === "auth_required") setAuth(null, null);
    throw new ApiError(res.status, data?.error || `Request failed (${res.status})`, data);
  }
  return data;
}

export const get = (p, o) => api(p, o);
export const post = (p, body, o) => api(p, { method: "POST", body, ...o });
export const put = (p, body, o) => api(p, { method: "PUT", body, ...o });
export const patch = (p, body, o) => api(p, { method: "PATCH", body, ...o });
export const del = (p, o) => api(p, { method: "DELETE", ...o });
export const upload = (p, file, { method = "PUT", query = "" } = {}) =>
  api(p + query, { method, body: file, raw: true, headers: { "Content-Type": file.type || "application/octet-stream", "X-Filename": encodeURIComponent(file.name || "file") } });

export async function loadSession() {
  const [cfg, me] = await Promise.all([get("/api/config", { quiet: true }), session.token ? get("/api/auth/me", { quiet: true }).catch(() => ({ user: null })) : { user: null }]);
  session.config = cfg;
  session.user = me.user;
  if (!me.user && session.token) setAuth(null, null);
  emitSession();
}
