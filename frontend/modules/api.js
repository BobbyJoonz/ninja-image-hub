import { S } from "./state.js";

async function handleResponse(response, path) {
  let data;
  try {
    data = await response.json();
  } catch {
    data = { detail: "Unexpected server response." };
  }
  if (response.status === 401 && path !== "/api/auth/login") {
    document.dispatchEvent(new Event("session-expired"));
    throw Error("Session expired. Please sign in.");
  }
  if (!response.ok) {
    const detail = data.detail || data.error || "Request failed";
    throw Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

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
  return handleResponse(r, path);
}

/** Multipart upload for reference images (the only body allowed above 64 KiB). */
export async function apiUpload(path, formData, opts = {}) {
  const r = await fetch(path, {
    method: "POST",
    body: formData,
    headers: { Accept: "application/json", "X-CSRF-Token": S.csrf },
    credentials: "same-origin",
    signal: opts.signal || AbortSignal.timeout(180000),
  });
  return handleResponse(r, path);
}
