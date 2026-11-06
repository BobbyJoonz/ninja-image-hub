export const $ = (id) => document.getElementById(id);
export const esc = (val) =>
  String(val ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const trunc = (v, n = 60) =>
  String(v ?? "").length > n
    ? String(v).slice(0, n - 1) + "…"
    : String(v ?? "");
export function toast(message, error = false) {
  const t = $("toast");
  t.textContent = message;
  t.classList.toggle("error", error);
  t.classList.add("visible");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("visible"), 4200);
}
export function compact(v) {
  return Number(v || 0).toLocaleString("en-US");
}
export function fmtSec(x) {
  return x == null
    ? "No samples"
    : x < 60
      ? `${x.toFixed(1)} sec`
      : `${(x / 60).toFixed(1)} min`;
}
export function relativeMinutes(x) {
  return x == null ? "None queued" : `${x.toFixed(1)} min`;
}

export function uniqueId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map((x) => x.toString(16).padStart(2, "0")).join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}


/** Inline reference to the bundled Lucide sprite; colour follows currentColor. */
export function uiIcon(name, extra = "") {
  return `<svg class="icon${extra ? " " + extra : ""}" aria-hidden="true" focusable="false"><use href="/static/icons/sprite.svg#lc-${name}"></use></svg>`;
}
