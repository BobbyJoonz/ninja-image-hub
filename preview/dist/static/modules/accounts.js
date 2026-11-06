import { S } from "./state.js";
import { $, esc, toast } from "./dom.js";
import { api } from "./api.js";
import { projectLabel } from "./views.js";
import { refreshData, switchView } from "./controller.js";
let loginAccountId = null;

function loginIdentityText(acc) {
  if (!acc) return "Identity: not checked yet";
  if (!acc.identity_email)
    return "Identity: not checked yet — press Verify after signing in.";
  const state = acc.identity_state || "unknown";
  return `Identity: ${acc.identity_email}${
    acc.identity_workspace ? ` · workspace ${String(acc.identity_workspace).slice(0, 8)}` : ""
  } · ${state.replace("_", " ")}`;
}

function autoLoginStateText(configured) {
  return configured
    ? "✓ Credentials are configured for this account (password is never shown)."
    : "Not configured — remote page or visible window login still works.";
}

async function loadAutoLoginState(id) {
  try {
    const state = await api(`/api/accounts/${id}/auto-login`);
    $("autoLoginState").textContent = autoLoginStateText(state.configured);
  } catch (e) {
    $("autoLoginState").textContent = e.message;
  }
}

export function initLoginDialog() {
  $("closeLogin").addEventListener("click", () => $("loginDialog").close());
  $("openLiveView").addEventListener("click", () => {
    if (!loginAccountId) return;
    window.open(`/accounts/${loginAccountId}/live`, "_blank", "noopener,noreferrer");
  });
  $("openRemoteLogin").addEventListener("click", () => {
    if (!loginAccountId) return;
    window.open(
      `/accounts/${loginAccountId}/login`,
      "_blank",
      "noopener,noreferrer",
    );
  });
  $("verifyLoginIdentity").addEventListener("click", async () => {
    if (!loginAccountId) return;
    const button = $("verifyLoginIdentity");
    button.disabled = true;
    try {
      await api(`/api/accounts/${loginAccountId}/identity/verify`, {
        method: "POST",
      });
      await refreshData(true);
      const acc = S.accounts.find((x) => x.id === loginAccountId);
      $("loginIdentity").textContent = loginIdentityText(acc);
      toast("Identity read from the browser.");
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
    }
  });
  $("refreshLoginStatus").addEventListener("click", async () => {
    await refreshData(true);
    toast("Account status refreshed.");
  });
  $("autoLoginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!loginAccountId) return;
    const button = $("saveAutoLogin");
    button.disabled = true;
    try {
      await api(`/api/accounts/${loginAccountId}/auto-login`, {
        method: "PUT",
        body: JSON.stringify({
          email: $("autoEmail").value.trim(),
          password: $("autoPassword").value,
          totp_secret: $("autoTotp").value.trim(),
        }),
      });
      $("autoPassword").value = "";
      $("autoTotp").value = "";
      $("autoLoginState").textContent = autoLoginStateText(true);
      toast("Auto-login saved. Stop → Start the worker to apply it.");
      await refreshData();
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
    }
  });
  $("refreshPlan").addEventListener("click", async () => {
    if (!loginAccountId && !S.editingAccountId) return;
    const accountId = S.editingAccountId || loginAccountId;
    const button = $("refreshPlan");
    button.disabled = true;
    try {
      const plan = await api(`/api/accounts/${accountId}/plan`);
      $("detectedPlan").textContent = planSummary(plan);
      toast(`Plan: ${plan.label}${plan.days_left != null ? ` · ${plan.days_left} day(s) left` : ""}`);
      await refreshData(true);
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
    }
  });
  $("restartForAutoLogin").addEventListener("click", async () => {
    if (!loginAccountId) return;
    const button = $("restartForAutoLogin");
    button.disabled = true;
    try {
      await api(`/api/accounts/${loginAccountId}/stop`, { method: "POST" });
      await new Promise((resolve) => setTimeout(resolve, 1200));
      await api(`/api/accounts/${loginAccountId}/start`, { method: "POST" });
      toast("Worker restarted — the gateway will use the saved credentials.");
      await refreshData(true);
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
    }
  });
  $("clearAutoLogin").addEventListener("click", async () => {
    if (!loginAccountId) return;
    if (!confirm("Remove the stored auto-login credentials for this account?"))
      return;
    try {
      await api(`/api/accounts/${loginAccountId}/auto-login`, {
        method: "DELETE",
      });
      $("autoLoginState").textContent = autoLoginStateText(false);
      toast("Auto-login credentials removed.");
      await refreshData();
    } catch (err) {
      toast(err.message, true);
    }
  });
}

const DISPLAY_HINTS = {
  xvfb: "This server renders the browser on a virtual display (Xvfb): there is no window on a desk. Use method 1 or 3.",
  missing:
    "No browser display is available on this host. Install xvfb or run the panel with a real DISPLAY before starting workers.",
  x11: "The worker window is on the server's X display; use VNC to see it, or method 1 / 3.",
  desktop: "The worker Chrome window is open on this machine.",
};



let detailsAccountId = null;

function fmtDay(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString();
}

function detailRow(label, valueHtml) {
  return `<div class="detail-row"><span class="detail-label">${label}</span><span class="detail-value">${valueHtml}</span></div>`;
}


function identityStateChip(state) {
  state = state || "unknown";
  if (state === "verified" || state === "confirmed")
    return `<span class="chip good">${esc(state)}</span>`;
  if (state === "mismatch" || state === "workspace_changed")
    return `<span class="chip warn">${esc(state.replace("_", " "))}</span>`;
  return '<span class="chip muted">not checked yet</span>';
}

function planSourceText(plan) {
  if (!plan) return "—";
  if (plan.source === "chatgpt-settings-page")
    return plan.workspace_plan ? "Settings page (workspace plan)" : "Settings page";
  if (plan.source === "chatgpt-account-entitlement") return "ChatGPT account API";
  return esc(plan.source || "—");
}

function planWorkspaceText(plan) {
  if (!plan) return "—";
  if (!plan.workspace_id) return plan.workspace_plan ? "workspace" : "personal";
  return `<code>${esc(String(plan.workspace_id).slice(0, 8))}</code>${plan.workspace_plan ? " · workspace" : " · personal"}`;
}
function planChip(plan, planLabel, checkedAt) {
  if (!plan) {
    const when = checkedAt ? ` · checked ${fmtDay(checkedAt)}` : "";
    return `<span class="chip muted">not detected yet</span><span class="fine-print">${when}</span>`;
  }
  const tone = plan.is_free ? "warn" : "good";
  const bits = [String(plan.label || plan.plan || "unknown").toUpperCase()];
  if (plan.is_free) bits.push("no active subscription");
  if (plan.days_left != null) bits.push(`${plan.days_left} day(s) left`);
  return `<span class="chip ${tone}">${esc(bits.join(" · "))}</span>`;
}

function renewalText(plan) {
  if (!plan) return "—";
  if (plan.ends_at) return `${fmtDay(plan.ends_at)} (ends)`;
  if (plan.renews_at) return `${fmtDay(plan.renews_at)} (next renewal)`;
  if (plan.workspace_plan) return "managed by the workspace — not exposed to members";
  return plan.active ? "unknown" : "no renewal — free plan";
}

function projectLink(url) {
  if (!url) return '<span class="fine-print">normal chat list</span>';
  const label = esc(projectLabel(url));
  return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
}

export function openAccountDetails(id) {
  const a = S.accounts.find((x) => x.id === id);
  if (!a) return;
  detailsAccountId = id;
  $("accountDetailsTitle").textContent = a.name;
  const plan = a.plan || null;
  $("accountDetailsBody").innerHTML = `
    <div class="detail-grid">
      <section class="detail-section">
        <h3>ChatGPT account</h3>
        ${detailRow("Plan", planChip(plan, a.plan_label, a.plan_checked_at))}
        ${detailRow("Renewal / expiry", esc(renewalText(plan)))}
        ${detailRow("Plan checked", a.plan_checked_at ? esc(fmtDay(a.plan_checked_at)) : "never")}
        ${detailRow("Plan source", planSourceText(plan))}
        ${detailRow("Workspace", planWorkspaceText(plan))}
        ${detailRow("Session", `<span class="pill ${esc(a.session)}">${esc(a.session)}</span>${a.session_blocker ? ` <span class="chip warn">${esc(a.session_blocker)}</span>` : ""}`)}
        ${detailRow("Browser session", plan && plan.session_expires ? esc(fmtDay(plan.session_expires)) : "—")}
        ${detailRow("Project", projectLink(a.project_url))}
        ${detailRow("Credentials", a.auto_login_configured ? "auto-login saved" : "manual / remote login")}
      </section>
      <section class="detail-section">
        <h3>Identity</h3>
        ${detailRow("Signed-in e-mail", a.identity_email ? esc(a.identity_email) : "not checked yet")}
        ${detailRow("Workspace", a.identity_workspace ? `<code>${esc(String(a.identity_workspace).slice(0, 8))}</code>` : "—")}
        ${detailRow("Checked", a.identity_checked_at ? esc(fmtDay(a.identity_checked_at)) : "never")}
        ${detailRow("State", identityStateChip(a.identity_state))}
        <div class="detail-actions detail-actions-inline">
          <button type="button" class="btn secondary btn-small" data-detail-action="verify-identity">Verify now</button>
          <button type="button" class="btn secondary btn-small" data-detail-action="confirm-identity">This is the right account</button>
        </div>
      </section>
      <section class="detail-section detail-section-wide">
        <h3>Runtime</h3>
        ${detailRow("Status", `<span class="pill ${esc(a.runtime)}">● ${esc(a.runtime)}</span>`)}
        ${detailRow("Memory", a.memory_mb == null ? "—" : esc(`${a.memory_mb} MiB`))}
        ${detailRow("Parallel jobs", esc(String(a.parallel_limit || 1)))}
        ${detailRow("Dispatch", a.dispatch_enabled ? "on" : "paused")}
        ${detailRow("Cooldown", a.cooldown_until ? esc(a.cooldown_until) : "—")}
        ${detailRow("Autostart", a.autostart ? "on" : "off")}
        ${detailRow("Display", a.display ? `<code>${esc(a.display)}</code>` : "—")}
        ${detailRow("Restart required", a.restart_required ? '<span class="chip warn">yes</span>' : "no")}
      </section>
    </div>
    <div class="detail-actions">
      <button type="button" class="btn secondary btn-small" data-detail-action="refresh-plan">Refresh plan</button>
      <button type="button" class="btn secondary btn-small" data-detail-action="login">Login</button>
      <button type="button" class="btn secondary btn-small" data-detail-action="edit">Edit</button>
      <button type="button" class="btn secondary btn-small" data-detail-action="logs">Logs</button>
      <button type="button" class="btn secondary btn-small" data-detail-action="toggle">${a.runtime === "stopped" ? "Start" : "Stop"}</button>
    </div>`;
  $("accountDetailsDialog").showModal();
}

async function detailAction(action) {
  if (!detailsAccountId) return;
  const acc = S.accounts.find((x) => x.id === detailsAccountId);
  try {
    if (action === "refresh-plan") {
      await api(`/api/accounts/${detailsAccountId}/plan`);
      toast("Plan refreshed from ChatGPT.");
      await refreshData(true);
      openAccountDetails(detailsAccountId);
      return;
    }
    if (action === "verify-identity") {
      await api(`/api/accounts/${detailsAccountId}/identity/verify`, {
        method: "POST",
      });
      toast("Identity read from the browser.");
      await refreshData(true);
      return openAccountDetails(detailsAccountId);
    }
    if (action === "confirm-identity") {
      await api(`/api/accounts/${detailsAccountId}/identity/confirm`, {
        method: "POST",
      });
      toast("Identity confirmed for this account.");
      await refreshData(true);
      return openAccountDetails(detailsAccountId);
    }
    $("accountDetailsDialog").close();
    if (action === "login") return openLoginDialog(detailsAccountId);
    if (action === "edit") return openEdit(detailsAccountId);
    if (action === "logs") return onAccountAction("logs", detailsAccountId);
    if (action === "toggle") {
      const verb = acc && acc.runtime === "stopped" ? "start" : "stop";
      await api(`/api/accounts/${detailsAccountId}/${verb}`, { method: "POST" });
      toast(verb === "start" ? "Worker starting." : "Worker stopped.");
      await refreshData(true);
      return;
    }
  } catch (err) {
    toast(err.message, true);
  }
}

export function initAccountDetails() {
  $("closeAccountDetails").addEventListener("click", () =>
    $("accountDetailsDialog").close(),
  );
  $("accountDetailsBody").addEventListener("click", (event) => {
    const button = event.target.closest("[data-detail-action]");
    if (button) detailAction(button.dataset.detailAction);
  });
}

const PLAN_TONE = (plan) => {
  if (!plan) return "";
  if (plan.is_free) return "warn";
  if (plan.days_left != null && plan.days_left <= 3) return "warn";
  return "good";
};

function statusChips(acc) {
  const chips = [];
  const runtime = acc.runtime || "stopped";
  const runtimeTone =
    runtime === "online" ? "good" : runtime === "starting" ? "warn" : "";
  chips.push(
    `<span class="chip ${runtimeTone}"><span class="dot ${runtimeTone}"></span>${esc(runtime)}</span>`,
  );
  if (acc.session && acc.session !== "unknown")
    chips.push(
      `<span class="chip ${acc.session === "connected" ? "good" : "warn"}">${esc(acc.session)}</span>`,
    );
  const plan = acc.plan;
  if (plan) {
    const days = plan.days_left != null ? ` · ${plan.days_left}d` : "";
    chips.push(
      `<span class="chip ${PLAN_TONE(plan)}">${esc(String(plan.label || plan.plan).toUpperCase())}${days}</span>`,
    );
  }
  if (acc.project_url) chips.push('<span class="chip muted">project</span>');
  if (acc.auto_login_configured) chips.push('<span class="chip muted">auto-login</span>');
  if (acc.dispatch_enabled === false)
    chips.push('<span class="chip muted">dispatch paused</span>');
  return chips.join("");
}

function renderExistingAccounts() {
  const list = $("existingAccounts");
  if (!list) return;
  const accounts = (S.accounts || []).filter((a) => !a.archived_at);
  $("existingCount").textContent = String(accounts.length);
  if (!accounts.length) {
    list.innerHTML =
      '<p class="existing-empty">No accounts yet — this will be the first one.</p>';
    return;
  }
  list.innerHTML = accounts
    .map(
      (a) => `
      <div class="existing-row${a.plan && a.plan.is_free ? " is-free" : ""}">
        <span class="existing-name">${esc(a.name)}</span>
        <span class="existing-chips">${statusChips(a)}</span>
      </div>`,
    )
    .join("");
}

function planSummary(plan) {
  if (!plan) return "Detected plan: —";
  const parts = [`Detected plan: ${plan.label || plan.plan || "unknown"}`];
  if (plan.active === false) parts.push("no active subscription");
  if (plan.days_left != null) parts.push(`${plan.days_left} day(s) left`);
  if (plan.will_renew) parts.push("will renew");
  if (plan.session_expires) parts.push(`browser session until ${plan.session_expires.slice(0, 10)}`);
  return parts.join(" · ");
}

function setLoginHints(acc) {
  const mode =
    (S.settings && S.settings.display_mode) ||
    (S.settings && S.settings.worker_policy ? "desktop" : "desktop");
  $("displayHint").textContent = DISPLAY_HINTS[mode] || DISPLAY_HINTS.desktop;
  const hint = $("loginSessionHint");
  if (acc && acc.session_blocker) {
    hint.hidden = false;
    hint.textContent =
      "Gateway reports a login blocker: “" +
      acc.session_blocker +
      "”. Fix the credentials/2FA, then reopen the login page.";
  } else {
    hint.hidden = true;
    hint.textContent = "";
  }
}

export function openLoginDialog(id) {
  const acc = S.accounts.find((a) => a.id === id);
  if (!acc) return;
  loginAccountId = id;
  $("loginTitle").textContent = `Connect: ${acc.name}`;
  $("autoLoginForm").reset();
  $("autoLoginState").textContent = autoLoginStateText(
    Boolean(acc.auto_login_configured),
  );
  setLoginHints(acc);
  $("loginIdentity").textContent = loginIdentityText(acc);
  $("loginDialog").showModal();
  loadAutoLoginState(id);
}

export async function onAccountAction(action, id) {
  try {
    if (action === "add") {
      openAdd();
      return;
    }
    if (action === "edit") {
      openEdit(id);
      return;
    }
    if (action === "details") {
      openAccountDetails(id);
      return;
    }
    if (action === "login") {
      openLoginDialog(id);
      return;
    }
    if (action === "pause-dispatch" || action === "resume-dispatch") {
      await api(
        `/api/accounts/${id}/dispatch/${action === "pause-dispatch" ? "pause" : "resume"}`,
        { method: "POST" },
      );
      toast(
        action === "pause-dispatch"
          ? "This account will receive no new jobs."
          : "Account dispatch resumed; cooldown still applies.",
      );
      await refreshData();
      return;
    }
    if (action === "logs") {
      const acc = S.accounts.find((a) => a.id === id);
      $("logsTitle").textContent = (acc?.name || "Worker") + " logs";
      $("logsContent").textContent = "Loading…";
      $("logsDialog").showModal();
      const data = await api(`/api/accounts/${id}/logs`);
      $("logsContent").textContent = data.logs;
      return;
    }
    if (action === "archive") {
      if (
        !confirm(
          "Archive this account? Worker will stop, and all jobs/images/session files stay safe. You can restore it later.",
        )
      )
        return;
      await api(`/api/accounts/${id}/archive`, { method: "POST" });
      toast("Account archived. Data preserved.");
    } else if (action === "restore") {
      await api(`/api/accounts/${id}/restore`, { method: "POST" });
      toast("Account restored. Start and verify login.");
    } else if (action === "purge") {
      const acc = S.accounts.find((a) => a.id === id);
      if (!acc) return;
      const phrase = "DELETE " + acc.name;
      const confirmation = prompt(
        "PERMANENT REMOVAL\n\nThis deletes local session files, related jobs and images. Cannot undo.\n\nType exactly: " +
          phrase,
      );
      if (confirmation === null) return;
      await api(`/api/accounts/${id}/purge`, {
        method: "POST",
        body: JSON.stringify({ confirmation }),
      });
      toast("Account and all local data removed.");
    } else if (action === "delete-job") {
      if (
        !confirm(
          "Cancel queued work or delete the finished job and its outputs? This cannot be undone.",
        )
      )
        return;
      await api(`/api/jobs/${id}`, { method: "DELETE" });
      toast("Job and image deleted.");
    } else if (action === "resolve-job") {
      openResolveDialog(id);
      return;
    } else {
      await api(`/api/accounts/${id}/${action}`, { method: "POST" });
      toast(
        action === "start" ? "Worker startup requested." : "Worker stopped.",
      );
    }
    await refreshData();
  } catch (e) {
    toast(e.message, true);
  }
}

export function initResolveDialog() {
  $("closeResolve").addEventListener("click", () => $("resolveDialog").close());
  $("cancelResolve").addEventListener("click", () => $("resolveDialog").close());
  $("resolveForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const jobId = $("resolveJobId").value;
    const chosen = document.querySelector('input[name="resolution"]:checked');
    if (!chosen) {
      toast("Choose what you verified before saving.", true);
      return;
    }
    const button = $("saveResolve");
    button.disabled = true;
    try {
      await api(`/api/jobs/${jobId}/resolve`, {
        method: "POST",
        body: JSON.stringify({
          resolution: chosen.value,
          note: $("resolveNote").value.trim(),
        }),
      });
      $("resolveDialog").close();
      toast("Reconciliation saved. Nothing was re-submitted upstream.");
      await refreshData();
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
    }
  });
}

export function openResolveDialog(jobId) {
  const job = (S.jobs || []).find((j) => j.id === jobId);
  $("resolveForm").reset();
  $("resolveJobId").value = jobId;
  $("resolveNote").value = "";
  const heading = $("resolveDialog").querySelector("h2");
  if (heading)
    heading.textContent = job
      ? `Resolve unknown job ${job.id.slice(0, 8)}`
      : "Resolve unknown job";
  $("resolveDialog").showModal();
}

export function openAdd() {
  S.editingAccountId = null;
  $("addForm").reset();
  $("accAutostart").checked = false;
  $("accParallel").value = "2";
  $("accProject").value = "";
  $("detectedPlan").textContent = "Detected plan: — (available after the first refresh)";
  $("detectedPlanRow").hidden = true;
  $("existingAccountsSection").hidden = false;
  renderExistingAccounts();
  $("accountDialogTitle").textContent = "Add an account";
  $("saveAccountBtn").textContent = "Create account";
  $("addDialog").showModal();
  $("accName").focus();
}

export function openEdit(id) {
  const a = S.accounts.find((x) => x.id === id);
  if (!a) return;
  S.editingAccountId = id;
  $("addForm").reset();
  $("accName").value = a.name;
  $("accEmail").value = a.email_label || "";
  $("accParallel").value = String(a.parallel_limit || 2);
  $("accProject").value = a.project_url || "";
  $("detectedPlan").textContent = planSummary(a.plan);
  $("detectedPlanRow").hidden = false;
  $("existingAccountsSection").hidden = true;
  $("accAutostart").checked = Boolean(a.autostart);
  $("accountDialogTitle").textContent = "Edit account";
  $("saveAccountBtn").textContent = "Save changes";
  $("addDialog").showModal();
  $("accName").focus();
}
