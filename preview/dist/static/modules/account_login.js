import { S } from "./state.js";
import { $, esc, toast } from "./dom.js";
import { api } from "./api.js";
import { refreshData } from "./controller.js";
import { DISPLAY_HINTS, planSummary } from "./account_shared.js";


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
