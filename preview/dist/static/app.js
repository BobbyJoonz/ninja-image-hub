import { S } from "./modules/state.js";
import { $, toast, uniqueId } from "./modules/dom.js";
import { api } from "./modules/api.js";
import {
  showAuth,
  showApp,
  boot,
  refreshData,
  switchView,
  loadGallery,
} from "./modules/controller.js";
import { renderAnalytics, renderJobs } from "./modules/views.js";
import {
  onAccountAction,
  openAdd,
  initLoginDialog,
  initResolveDialog,
} from "./modules/accounts.js";

initLoginDialog();
initResolveDialog();

$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("authError").textContent = "";
  try {
    const r = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ password: $("password").value }),
    });
    S.csrf = r.csrf;
    $("password").value = "";
    showApp();
    await refreshData();
  } catch (err) {
    $("authError").textContent = err.message;
  }
});
$("logoutBtn").addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch {}
  showAuth();
});
document
  .querySelectorAll("[data-view]")
  .forEach((btn) =>
    btn.addEventListener("click", () => switchView(btn.dataset.view)),
  );
document.body.addEventListener("click", (e) => {
  const go = e.target.closest("[data-go]");
  if (go) {
    switchView(go.dataset.go);
    return;
  }
  const act = e.target.closest("[data-action]");
  if (act) {
    onAccountAction(act.dataset.action, act.dataset.id);
    const menu = act.closest("details.action-menu");
    if (menu) menu.removeAttribute("open");
    return;
  }
  // Clicking anywhere else closes any open account action menu.
  if (!e.target.closest(".action-menu"))
    document
      .querySelectorAll("details.action-menu[open]")
      .forEach((d) => d.removeAttribute("open"));
});
["headerNew", "accountsAdd"].forEach((id) =>
  $(id).addEventListener("click", openAdd),
);
$("showArchived").addEventListener("change", () => {
  $("archivedList").hidden = !$("showArchived").checked;
});
["refreshBtn", "accountsRefresh", "jobsRefresh"].forEach((id) =>
  $(id).addEventListener("click", () =>
    refreshData(true).catch((e) => toast(e.message, true)),
  ),
);
["cancelModal", "cancelModal2"].forEach((id) =>
  $(id).addEventListener("click", () => $("addDialog").close()),
);
$("closeLogs").addEventListener("click", () => $("logsDialog").close());
$("addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    name: $("accName").value,
    email_label: $("accEmail").value,
    autostart: $("accAutostart").checked,
    plan_label: $("accPlan").value,
    parallel_limit: Number($("accParallel").value),
    project_url: $("accProject").value.trim(),
  };
  try {
    if (S.editingAccountId) {
      await api(`/api/accounts/${S.editingAccountId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
    } else {
      await api("/api/accounts", {
        method: "POST",
        body: JSON.stringify(body),
      });
    }
    $("addDialog").close();
    toast(
      S.editingAccountId
        ? "Account updated."
        : "Account created. Start it and open Login.",
    );
    S.editingAccountId = null;
    await refreshData();
    switchView("accounts");
  } catch (err) {
    toast(err.message, true);
  }
});
document.querySelectorAll("[data-ratio]").forEach((b) => {
  b.setAttribute("aria-pressed", String(b.classList.contains("active")));
  b.addEventListener("click", () => {
    S.ratio = b.dataset.ratio;
    document
      .querySelectorAll("[data-ratio]")
      .forEach((a) => {
        const active = a === b;
        a.classList.toggle("active", active);
        a.setAttribute("aria-pressed", String(active));
      });
  });
});
$("genPrompt").addEventListener("input", () => {
  $("charCount").textContent = $("genPrompt").value.length + " / 4000";
});
// An ambiguous submission (network drop, timeout) must be retried with the SAME
// idempotency key. Keeping it only in memory lost the key on reload, so a retry minted
// a new key and produced a duplicate job. sessionStorage survives a reload in this tab.
const PENDING_KEY = "ninja.pendingGeneration";
function loadPendingGeneration() {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}
function savePendingGeneration(value) {
  try {
    if (value) sessionStorage.setItem(PENDING_KEY, JSON.stringify(value));
    else sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* storage may be disabled; the in-memory key still works for this page */
  }
}
$("generateForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = $("generateBtn");
  button.disabled = true;
  const payload = {
    account_id: $("genAccount").value,
    prompt: $("genPrompt").value,
    aspect_ratio: S.ratio,
    quality_preference: $("qualityPreference").value,
    n: Number($("genCount").value),
  };
  const fingerprint = JSON.stringify(payload);
  S.pendingGeneration = S.pendingGeneration || loadPendingGeneration();
  if (S.pendingGeneration?.fingerprint !== fingerprint)
    S.pendingGeneration = { fingerprint, key: "ui-" + uniqueId() };
  savePendingGeneration(S.pendingGeneration);
  try {
    const job = await api("/api/generate", {
      method: "POST",
      body: JSON.stringify({
        ...payload,
        idempotency_key: S.pendingGeneration.key,
      }),
    });
    S.pendingGeneration = null;
    savePendingGeneration(null);
    S.latestJobId = job.id;
    toast("Image job queued.");
    await refreshData();
  } catch (err) {
    toast(
      err.message + " Retry the same request to reuse its submission key.",
      true,
    );
  } finally {
    button.disabled = false;
  }
});
document.querySelectorAll("[data-hours]").forEach((button) => {
  button.setAttribute("aria-pressed", String(button.classList.contains("active")));
  button.addEventListener("click", async () => {
    S.analyticsHours = Number(button.dataset.hours);
    document
      .querySelectorAll("[data-hours]")
      .forEach((b) => {
        const active = b === button;
        b.classList.toggle("active", active);
        b.setAttribute("aria-pressed", String(active));
      });
    try {
      S.analytics = await api("/api/analytics?hours=" + S.analyticsHours);
      renderAnalytics();
    } catch (e) {
      toast(e.message, true);
    }
  });
});
$("controlForm").addEventListener("input", () => {
  $("controlForm").dataset.dirty = "yes";
  $("controlDirty").textContent =
    "Unsaved changes — select Save controls to apply.";
});
$("controlForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  if (!form.reportValidity()) return;
  const payload = Object.fromEntries(
    [...new FormData(form)].map(([k, v]) => [k, Number(v)]),
  );
  const btn = $("saveControl");
  btn.disabled = true;
  try {
    const result = await api("/api/control", {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    form.dataset.dirty = "no";
    $("controlDirty").textContent = result.restart_required.length
      ? "Saved. Restart idle account workers to apply: " +
        result.restart_required.join(", ")
      : "Saved. Changes take effect on the next scheduling cycle.";
    toast("Operation controls saved.");
    await refreshData();
  } catch (err) {
    $("controlDirty").textContent = err.message;
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});
$("toggleDispatch").addEventListener("click", async () => {
  const c = S.control.config;
  const requested = !Boolean(c.dispatch_paused);
  if (
    requested &&
    !confirm(
      "Pause NEW job dispatch? Already running jobs will continue; queued jobs will be retained.",
    )
  )
    return;
  try {
    await api("/api/control", {
      method: "PATCH",
      body: JSON.stringify({ dispatch_paused: requested }),
    });
    await refreshData();
    toast(
      requested
        ? "Dispatch paused; running jobs unaffected."
        : "Dispatch resumed.",
    );
  } catch (e) {
    toast(e.message, true);
  }
});
document.addEventListener("session-expired", showAuth);
// Jobs toolbar: status chips + live search (client-side over the last 100 jobs).
let jobSearchTimer = null;
const applyJobFilter = () => {
  S.jobsFilter = $("jobFilters")?.querySelector(".chip.active")?.dataset.status || "all";
  S.jobQuery = $("jobSearch")?.value || "";
  renderJobs();
};
$("jobFilters")?.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  $("jobFilters")
    .querySelectorAll(".chip")
    .forEach((c) => {
      const active = c === chip;
      c.classList.toggle("active", active);
      c.setAttribute("aria-pressed", String(active));
    });
  applyJobFilter();
});
$("jobFilters")
  ?.querySelectorAll(".chip")
  .forEach((c) => c.setAttribute("aria-pressed", String(c.classList.contains("active"))));
$("jobSearch")?.addEventListener("input", () => {
  clearTimeout(jobSearchTimer);
  jobSearchTimer = setTimeout(applyJobFilter, 120);
});
$("galleryRefresh").addEventListener("click", () =>
  loadGallery(true).catch((e) => toast(e.message, true)),
);
$("galleryMore").addEventListener("click", () =>
  loadGallery().catch((e) => toast(e.message, true)),
);
boot();
