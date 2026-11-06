import { S } from "./state.js";
import { $, toast } from "./dom.js";
import { api } from "./api.js";
import { renderAll, renderGallery } from "./views.js";
export function showAuth() {
  S.csrf = "";
  $("authScreen").hidden = false;
  $("app").hidden = true;
  clearInterval(S.poll);
}

export function showApp() {
  $("authScreen").hidden = true;
  $("app").hidden = false;
  startPolling();
}

export function startPolling() {
  clearTimeout(S.poll);
  const tick = async () => {
    try {
      if (!document.hidden) await refreshData();
    } catch (e) {
      $("runtimeBadge").textContent = "● Connection interrupted";
    } finally {
      if (S.csrf) S.poll = setTimeout(tick, 7500);
    }
  };
  S.poll = setTimeout(tick, 7500);
}
export async function boot() {
  try {
    const a = await api("/api/auth/me");
    if (a.authenticated) {
      S.csrf = a.csrf;
      showApp();
      await refreshData();
    } else showAuth();
  } catch {
    showAuth();
  }
}

export function switchView(name) {
  S.view = name;
  if (name === "gallery")
    loadGallery(!S.galleryLoaded).catch((e) => toast(e.message, true));
  else if (name === "analytics" || name === "operations")
    refreshData().catch((e) => toast(e.message, true));
  document
    .querySelectorAll("[data-page]")
    .forEach((v) => (v.hidden = v.dataset.page !== name));
  document
    .querySelectorAll(".nav-link")
    .forEach((b) => {
      const isActive = b.dataset.view === name;
      b.classList.toggle("active", isActive);
      if (isActive) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
  $("viewCrumb").textContent =
    {
      overview: "Overview",
      accounts: "Accounts",
      studio: "Image Studio",
      jobs: "Jobs",
      gallery: "Gallery",
      analytics: "Analytics",
      operations: "Operations",
      settings: "Settings",
    }[name] || name;
  window.scrollTo({ top: 0, behavior: "smooth" });
}
let pendingRefresh = null;
export function refreshData(showToast = false) {
  if (pendingRefresh) return pendingRefresh;
  pendingRefresh = (async () => {
    const [overview, accounts, jobs, settings, queue, control] =
      await Promise.all([
        api("/api/overview"),
        api("/api/accounts"),
        api("/api/jobs?limit=100"),
        api("/api/settings"),
        api("/api/queue"),
        api("/api/control"),
      ]);
    Object.assign(S, { overview, accounts, jobs, settings, queue, control });
    if (S.view === "analytics" || !S.analytics.series)
      S.analytics = await api("/api/analytics?hours=" + S.analyticsHours);
    if (S.view === "operations")
      S.audit = await api("/api/control/audit?limit=12");
    renderAll();
    // First successful refresh ends the skeleton phase (Bundle B).
    document.body.classList.remove("booting");
    const overviewAccounts = $("overviewAccounts");
    if (overviewAccounts) overviewAccounts.setAttribute("aria-busy", "false");
    if (showToast) toast("Everything is up to date.");
  })().finally(() => {
    pendingRefresh = null;
  });
  return pendingRefresh;
}
export async function loadGallery(reset = false) {
  const button = $("galleryMore");
  if (button.disabled) return;
  button.disabled = true;
  try {
    const page = await api(
      "/api/gallery?limit=24" +
        (!reset && S.galleryCursor
          ? "&cursor=" + encodeURIComponent(S.galleryCursor)
          : ""),
    );
    const unique = new Map(
      (reset ? [] : S.gallery).map((i) => [i.job_id + ":" + i.position, i]),
    );
    page.items.forEach((i) => unique.set(i.job_id + ":" + i.position, i));
    S.gallery = [...unique.values()];
    S.galleryCursor = page.next_cursor;
    S.galleryLoaded = true;
    renderGallery();
  } finally {
    button.disabled = false;
  }
}
