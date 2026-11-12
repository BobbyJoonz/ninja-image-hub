import { S } from "./state.js";
import { $ } from "./dom.js";
import { renderJobs } from "./views.js";


export function initJobsToolbar() {
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
}
