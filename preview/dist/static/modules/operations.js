import { S } from "./state.js";
import { $, toast } from "./dom.js";
import { api } from "./api.js";
import { refreshData } from "./controller.js";
import { renderAnalytics } from "./views.js";


export function initOperations() {
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
    const payload = {};
    for (const [key, value] of new FormData(form)) {
      payload[key] = key === "thinking_effort" ? String(value) : Number(value);
    }
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
}
