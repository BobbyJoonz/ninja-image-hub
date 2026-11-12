import { S } from "./state.js";
import { $, esc, uiIcon } from "./dom.js";


export function renderOperations() {
  const c = S.control?.config;
  if (!c) return;
  const paused = Boolean(c.dispatch_paused),
    label = paused ? "Dispatch paused" : "Dispatch active";
  $("dispatchState").textContent = label;
  $("dispatchState").classList.toggle("warn", paused);
  $("dispatchDescription").textContent = paused
    ? "New work is held in queue"
    : "Eligible work is dispatched within capacity limits";
  $("toggleDispatch").textContent = paused
    ? "Resume dispatch"
    : "Pause dispatch";
  $("toggleDispatch").classList.toggle("primary", paused);
  const frm = $("controlForm");
  if (frm.dataset.dirty !== "yes")
    Object.keys(c).forEach((key) => {
      const el = frm.elements.namedItem(key);
      if (el) el.value = c[key];
    });
  const diskFree = S.settings.disk_free_mb;
  const diskLow = Boolean(S.settings.disk_low);
  $("opsCapacity").innerHTML =
    `<div><span>Running workers</span><b>${compact(S.control.active_workers)} / ${compact(c.max_running_accounts)}</b></div><div><span>Active requests</span><b>${compact(S.queue.running)} / ${compact(c.global_max_inflight)}</b></div><div><span>Waiting jobs</span><b>${compact(S.queue.queued)} / ${compact(c.max_pending_jobs)}</b></div><div><span>Batch limit</span><b>${compact(c.max_batch_images)}</b></div><div><span>Tab limit</span><b>${compact(c.gateway_max_tabs)} / worker</b></div><div><span>Disk free</span><b class="${diskLow ? "warn" : ""}">${diskFree == null ? "unknown" : compact(diskFree) + " MB"}</b></div><div><span>Image effort</span><b>${esc(S.settings.thinking_effort_label || S.settings.thinking_effort || "High")}</b></div>${S.settings.effort_below_requested_24h ? `<div><span>Effort fallbacks (24h)</span><b class="warn">${compact(S.settings.effort_below_requested_24h)}</b></div>` : ""}`;
  const diskBlocked = Boolean(S.settings.disk_low);
  const statusText = paused
    ? "Dispatch paused"
    : diskBlocked
      ? "Dispatch paused · low disk"
      : "Queue operational";
  $("opsStrip").innerHTML =
    `<span class="live-indicator ${paused || diskBlocked ? "paused" : ""}"></span><strong>${statusText}</strong><span>${compact(S.queue.queued)} pending</span><span>${compact(S.queue.running)} active / ${compact(c.global_max_inflight)} slots</span><span>${S.settings.disk_free_mb == null ? "" : compact(S.settings.disk_free_mb) + " MB free"}</span><button class="text-button" data-go="operations">Adjust capacity ${uiIcon("arrow-right")}</button>`;
  $("controlAudit").innerHTML = S.audit.length
    ? S.audit
        .map((x) => {
          let detail = "";
          try {
            const d = JSON.parse(x.detail || "{}");
            if (x.action === "config.update") {
              detail = Object.entries(d.after || {})
                .map(([k, v]) => `${k.replaceAll("_", " ")}: ${v}`)
                .join(" · ");
            } else if (x.action === "account.dispatch")
              detail = `${d.mode || ""} · ${(d.account_id || "").slice(0, 8)}`;
          } catch {}
          return `<div class="audit-item"><strong>${esc(x.action)}</strong><small>${esc(new Date(x.created_at).toLocaleString())}</small>${detail ? `<span class="audit-detail">${esc(detail)}</span>` : ""}</div>`;
        })
        .join("")
    : '<p class="muted">No control changes recorded yet.</p>';
}
