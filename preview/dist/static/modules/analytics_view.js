import { S } from "./state.js";
import { $, esc, compact, uiIcon } from "./dom.js";


export function chartHasData(series) {
  return Array.isArray(series) && series.some((x) => Number(x.images) > 0);
}

export function renderAnalytics() {
  const a = S.analytics || {};
  if (!a.series) return;
  const metrics = [
    ["Stored images", compact(a.images), "Actual gallery outputs"],
    [
      "Jobs accepted",
      compact(a.jobs),
      "Requested " + compact(a.requested_images) + " images",
    ],
    [
      "Observed rate",
      compact(a.throughput_images_per_hour) + "/h",
      "From selected time window",
    ],
    [
      "Window projection",
      compact(a.target_window_projection) + "/" + compact(a.target_images),
      "For " + a.target_window_hours + "h · not a guarantee",
    ],
  ];
  $("analyticsMetrics").innerHTML = metrics
    .map(
      ([label, value, desc]) =>
        `<div class="signal-stat"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(desc)}</small></div>`,
    )
    .join("");
  const max = Math.max(1, ...a.series.map((x) => x.images));
  const hasData = chartHasData(a.series);
  $("analyticsChart").innerHTML = hasData
    ? `<div class="chart-body"><div class="chart-y" aria-hidden="true"><span>${compact(max)}</span><span>${compact(Math.round(max / 2))}</span><span>0</span></div><div class="bar-chart" role="img" aria-label="Stored images by time bucket, ${compact(a.images)} in this window">${a.series
        .map(
          (x, i) =>
            `<div class="bar-column" title="${esc(new Date(x.time).toLocaleString())}: ${x.images} image(s)" aria-label="Bucket ${i + 1}: ${x.images} images"><span class="bar-count">${x.images || ""}</span><div class="bar" style="height:${x.images ? Math.max(6, Math.round((x.images / max) * 100)) : 0}%"></div></div>`,
        )
        .join("")}</div></div>`
    : `<div class="chart-empty">${uiIcon("image")}<strong>No images stored in this window</strong><p>Change the range above, or queue a job in Image Studio. Nothing is estimated here.</p><button class="btn secondary sm" data-go="studio">Open Image Studio</button></div>`;
  $("chartFrom").textContent = new Date(a.window_start).toLocaleString();
  $("chartTo").textContent = new Date(a.window_end).toLocaleString();
  const statuses = [
    "completed",
    "partial",
    "queued",
    "running",
    "failed",
    "unknown",
    "rate_limited",
  ];
  $("statusBreakdown").innerHTML = statuses
    .map(
      (s) =>
        `<div class="outcome"><span class="outcome-dot ${s}"></span><span>${s.replace("_", " ")}</span><strong>${compact(a.status_counts?.[s])}</strong></div>`,
    )
    .join("");
  $("oldestJob").textContent = relativeMinutes(a.queued_oldest_age_min);
  $("latencyP50").textContent = fmtSec(a.p50_sec);
  $("latencyP95").textContent = fmtSec(a.p95_sec);
  if ($("queueWaitP50"))
    $("queueWaitP50").textContent = fmtSec(a.queue_wait_p50_sec);
  if ($("unresolvedUnknown"))
    $("unresolvedUnknown").textContent = compact(a.unresolved_unknown);
  $("analyticsAccounts").innerHTML = a.accounts.length
    ? a.accounts
        .map(
          (x) =>
            `<tr><td><strong>${esc(x.name)}</strong><small class="meta-detail">${esc(x.account_id.slice(0, 8))}</small></td><td>${compact(x.jobs)}</td><td><strong>${compact(x.images)}</strong></td><td>${compact(x.failed)}</td><td>${compact(x.partial)}</td><td>${compact(x.unknown)}</td><td>${compact(x.rate_limited)}</td></tr>`,
        )
        .join("")
    : '<tr><td colspan="7" class="muted" style="text-align:center">No account activity in this period</td></tr>';
  $("incidentList").innerHTML = a.recent_failures.length
    ? a.recent_failures
        .map(
          (x) =>
            `<div class="incident"><span class="outcome-dot ${esc(x.status)}"></span><div><strong>${esc(x.status)} · ${esc(x.account)}</strong><p>${esc(x.error || "No error message reported")}</p></div><small>${x.time ? esc(new Date(x.time).toLocaleString()) : "—"}</small></div>`,
        )
        .join("")
    : '<div class="empty">No recorded exceptions in this window.</div>';
  $("analyticsCaveat").textContent = a.note || "";
  $("heroTargetTitle").textContent =
    a.target_window_hours + "-HOUR DELIVERY TARGET";
  $("heroTargetValue").textContent = compact(a.target_images);
  $("overviewPace").textContent = compact(a.throughput_images_per_hour);
  $("overviewProgress").style.width =
    Math.min(100, a.target_progress_percent || 0) + "%";
  $("overviewProjection").textContent =
    `${compact(a.target_window_projection)} projected over ${a.target_window_hours}h · not guaranteed`;
}
