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

export function openLoginDialog(id) {
  const acc = S.accounts.find((a) => a.id === id);
  if (!acc) return;
  loginAccountId = id;
  $("loginTitle").textContent = `Connect: ${acc.name}`;
  $("autoLoginForm").reset();
  $("autoLoginState").textContent = autoLoginStateText(
    Boolean(acc.auto_login_configured),
  );
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

export function openAdd() {
  S.editingAccountId = null;
  $("addForm").reset();
  $("accAutostart").checked = false;
  $("accPlan").value = "unknown";
  $("accParallel").value = "2";
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
  $("accAutostart").checked = Boolean(a.autostart);
  $("accountDialogTitle").textContent = "Edit account";
  $("saveAccountBtn").textContent = "Save changes";
  $("addDialog").showModal();
  $("accName").focus();
}
