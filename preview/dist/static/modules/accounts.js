import { S } from "./state.js";
import { $, toast } from "./dom.js";
import { api } from "./api.js";
import { refreshData, switchView } from "./controller.js";
let loginAccountId = null;

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
  $("openRemoteLogin").addEventListener("click", () => {
    if (!loginAccountId) return;
    window.open(
      `/accounts/${loginAccountId}/login`,
      "_blank",
      "noopener,noreferrer",
    );
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
  $("accPlan").value = "unknown";
  $("accParallel").value = "2";
  $("accProject").value = "";
  $("detectedPlan").textContent = "Detected plan: — (available after the first refresh)";
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
  $("accPlan").value = a.plan_label || "unknown";
  $("accParallel").value = String(a.parallel_limit || 2);
  $("accProject").value = a.project_url || "";
  $("detectedPlan").textContent = planSummary(a.plan);
  $("accAutostart").checked = Boolean(a.autostart);
  $("accountDialogTitle").textContent = "Edit account";
  $("saveAccountBtn").textContent = "Save changes";
  $("addDialog").showModal();
  $("accName").focus();
}
