import { S } from "./state.js";
import { $, toast } from "./dom.js";
import { api } from "./api.js";
import { refreshData } from "./controller.js";


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
