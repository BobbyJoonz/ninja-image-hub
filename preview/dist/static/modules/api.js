import { S } from "./state.js";
export async function api(path, opts = {}) {
  const method = opts.method || "GET",
    headers = {
      Accept: "application/json",
      ...(opts.body ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "X-CSRF-Token": S.csrf } : {}),
    };
  const r = await fetch(path, {
    ...opts,
    method,
    headers: { ...headers, ...opts.headers },
    credentials: "same-origin",
    signal: opts.signal || AbortSignal.timeout(15000),
  });
  let data;
  try {
    data = await r.json();
  } catch {
    data = { detail: "Unexpected server response." };
  }
  if (r.status === 401 && path !== "/api/auth/login") {
    document.dispatchEvent(new Event("session-expired"));
    throw Error("Session expired. Please sign in.");
  }
  if (!r.ok) {
    const detail = data.detail || data.error || "Request failed";
    throw Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}
