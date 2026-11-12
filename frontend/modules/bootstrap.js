import { S } from "./state.js";
import { $, toast } from "./dom.js";
import { api } from "./api.js";
import { showAuth, showApp, refreshData, switchView } from "./controller.js";
import {
  onAccountAction,
  openAdd,
  initAccountDetails,
  initLoginDialog,
  initResolveDialog,
} from "./accounts.js";
import { initStudio } from "./studio.js";
import { initQualityPrompt } from "./operations_view.js";


export function initBootstrap() {
  initStudio();
  initQualityPrompt();
  initLoginDialog();
  initResolveDialog();
  initAccountDetails();

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

  document.addEventListener("session-expired", showAuth);
}
