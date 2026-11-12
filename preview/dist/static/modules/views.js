import { S } from "./state.js";
import { uiIcon } from "./dom.js";
import { $, esc, trunc, compact, fmtSec, relativeMinutes } from "./dom.js";
import { filterJobs } from "./jobs.js";
import { chartHasData, renderAnalytics } from "./analytics_view.js";
import { renderOperations } from "./operations_view.js";
import { renderLatest } from "./latest_view.js";

export { chartHasData, renderAnalytics, renderOperations, renderLatest };

export { filterJobs } from "./jobs.js";
export function renderAll() {
  const active = S.accounts.filter((a) => !a.archived_at),
    archived = S.accounts.filter((a) => a.archived_at);
  $("navAccountCount").textContent = active.length;
  $("accountCount").textContent = active.length;
  $("archivedCount").textContent = archived.length;
  $("statAccounts").textContent = S.overview.accounts || 0;
  $("statComplete").textContent = S.overview.completed || 0;
  $("statJobs").textContent = S.overview.jobs || 0;
  $("statActive").textContent = S.overview.active || 0;
  $("runtimeBadge").textContent = S.settings.gateway_installed
    ? "● Gateway runtime installed"
    : "● Gateway runtime missing";
  $("runtimeBadge").style.color = S.settings.gateway_installed
    ? "#9cecba"
    : "#e4b977";
  $("runtimeNotice").textContent = S.settings.gateway_installed
    ? ""
    : "The image gateway is not installed yet. You can still explore the panel and add accounts. Run the one-time gateway preparation command in Settings before using Login or Generate.";
  $("overviewAccounts").innerHTML = accountCards(active.slice(0, 4));
  $("accountList").innerHTML = accountCards(active);
  $("archivedList").innerHTML = accountCards(archived, true);
  $("archivedList").hidden = !$("showArchived").checked;
  const selected = $("genAccount").value;
  $("genAccount").innerHTML =
    '<option value="">Select an account</option>' +
    active
      .map(
        (a) =>
          `<option value="${a.id}">${esc(a.name)} (${esc(a.session)})</option>`,
      )
      .join("");
  if (active.some((a) => a.id === selected)) $("genAccount").value = selected;
  else if (!selected && active.length) {
    // Convenience: default to a connected account so Generate is one click away.
    const preferred =
      active.find((a) => a.session === "connected" && a.runtime === "running") ||
      active.find((a) => a.session === "connected") ||
      active[0];
    $("genAccount").value = preferred.id;
  }
  const uploadCaps = S.settings.upload_limits;
  if (uploadCaps && $("fileLimits"))
    $("fileLimits").textContent =
      `PNG · JPEG · WebP · up to ${uploadCaps.max_files} images · ` +
      `${uploadCaps.max_file_mb} MiB each · ${uploadCaps.max_total_mb} MiB total`;
  if ($("queueNotice"))
    $("queueNotice").textContent =
      `Queue: ${S.queue.queued || 0} waiting · ${S.queue.running || 0}/${S.queue.max_global_inflight || "?"} dispatched · ${S.queue.completed || 0} completed · ${S.queue.partial || 0} partial · ${S.queue.unknown || 0} unknown. Account cooldowns and provider quotas are respected.`;
  if (S.settings.max_batch_images) {
    const sel = $("genCount");
    [...sel.options].forEach(
      (o) => (o.disabled = Number(o.value) > S.settings.max_batch_images),
    );
    if (Number(sel.value) > S.settings.max_batch_images)
      sel.value = String(S.settings.max_batch_images);
  }
  renderJobs();
  renderSettings();
  renderLatest();
  renderAnalytics();
  renderOperations();
}


function qualityBadge(j) {
  if (j.quality_score == null) return "";
  const score = Number(j.quality_score);
  const tone = score >= 8 ? "good" : score >= 6 ? "warn" : "bad";
  const refined = j.quality_meta && String(j.quality_meta).includes('"refined": true') ? " · fixed" : "";
  return `<span class="chip ${tone}" title="Quality gate score">Q ${score}/10${refined}</span>`;
}

export function accountCards(arr, archived = false) {
  if (!arr.length)
    return archived
      ? '<div class="empty"><p>No archived accounts.</p></div>'
      : `<div class="empty"><h3>No active accounts yet</h3><p>Add an account to create an isolated browser workspace.</p><button class="btn primary" data-action="add">${uiIcon("plus")} Add account</button></div>`;
  return arr
    .map((a) => {
      const p = a.plan || null;
      const planText = p
        ? `${String(p.label || p.plan || "unknown").toUpperCase()}${
            p.is_free ? " · no subscription" : ""
          }${p.days_left != null ? ` · ${p.days_left}d left` : ""}`
        : "— not detected yet";
      const planTone = p ? (p.is_free ? "warn" : "good") : "muted";
      const project = projectLabel(a.project_url);
      const buttons = archived
        ? `<button class="small-action accent" data-action="restore" data-id="${a.id}">${uiIcon("restore")} Restore</button><button class="small-action danger" data-action="purge" data-id="${a.id}">${uiIcon("trash")} Delete forever</button>`
        : `<button class="small-action primary-soft" data-action="details" data-id="${a.id}">${uiIcon("logs")} Details</button><button class="small-action" data-action="edit" data-id="${a.id}">${uiIcon("pencil")} Edit</button>${
            a.runtime === "stopped"
              ? `<button class="small-action accent" data-action="start" data-id="${a.id}">${uiIcon("play")} Start</button>`
              : `<button class="small-action" data-action="stop" data-id="${a.id}">${uiIcon("stop")} Stop</button>`
          }<button class="small-action accent" data-action="login" data-id="${a.id}">${uiIcon("login")} Login</button><details class="action-menu"><summary aria-label="More actions for ${esc(a.name)}" title="More actions">${uiIcon("more")}</summary><div class="action-menu-list"><button class="small-action" data-action="${a.dispatch_enabled ? "pause-dispatch" : "resume-dispatch"}" data-id="${a.id}">${a.dispatch_enabled ? uiIcon("pause") + " Pause queue" : uiIcon("play") + " Resume queue"}</button><button class="small-action" data-action="logs" data-id="${a.id}">${uiIcon("logs")} Logs</button><button class="small-action danger" data-action="archive" data-id="${a.id}">${uiIcon("archive")} Archive</button></div></details>`;
      return `<article class="account-card" data-account="${a.id}">
        <div class="account-top">
          <div class="account-icon">${uiIcon("account")}</div>
          <div class="identity">
            <div class="account-name">${esc(a.name)}</div>
            <div class="account-email">${esc(a.email_label || "No email label")}</div>
          </div>
          <div class="account-pill"><span class="pill ${esc(a.runtime)}">● ${esc(a.runtime)}</span><span class="pill ${esc(a.session)}">${esc(a.session)}</span></div>
        </div>
        <dl class="account-facts">
          <div class="account-fact"><dt>Plan</dt><dd><span class="chip ${planTone}">${esc(planText)}</span></dd></div>
          <div class="account-fact"><dt>Parallel</dt><dd>${a.parallel_limit || 1} job(s) · ${a.dispatch_enabled ? "dispatching" : "paused"}</dd></div>
          <div class="account-fact"><dt>Project</dt><dd>${esc(project)}</dd></div>
          <div class="account-fact"><dt>Login</dt><dd>${a.auto_login_configured ? "Auto-login saved" : "Manual / remote"}${a.session_blocker ? ` · <span class="chip warn">${esc(a.session_blocker)}</span>` : ""}</dd></div>
          <div class="account-fact"><dt>Identity</dt><dd>${identityChip(a)}</dd></div>
        </dl>
        <div class="account-buttons">${buttons}</div>
      </article>`;
    })
    .join("");
}

function identityChip(a) {
  const state = a.identity_state || "unknown";
  if (state === "verified" || state === "confirmed")
    return `<span class="chip good">${state}${a.identity_email ? ` · ${esc(a.identity_email)}` : ""}</span>`;
  if (state === "mismatch" || state === "workspace_changed")
    return `<span class="chip warn">${state.replace("_", " ")} — confirm it</span>`;
  return '<span class="chip muted">not checked yet</span>';
}

function projectLabel(url) {
  if (!url) return "— (normal chat list)";
  try {
    const parts = new URL(url).pathname.split("/").filter(Boolean);
    const slug = parts[parts.length - 2] || "";
    const name = slug.replace(/^g-p-[a-z0-9]+-?/i, "").replace(/-/g, " ").trim();
    if (name) return name;
    const id = slug.replace(/^g-p-/i, "").slice(0, 8);
    return id ? `ChatGPT project · ${id}` : "ChatGPT project";
  } catch {
    return "ChatGPT project";
  }
}

export { projectLabel };

export function statusPill(v) {
  return `<span class="pill ${esc(v)}">${esc(v)}</span>`;
}

const EFFORT_LABELS = {
  high: "High",
  extra_high: "Extra High",
  medium: "Medium",
  instant: "Instant",
  pro: "Pro",
};

function effortBadge(j) {
  if (!j.thinking_requested) return "";
  const want = EFFORT_LABELS[j.thinking_requested] || j.thinking_requested;
  const got = EFFORT_LABELS[j.thinking_applied] || j.thinking_applied || "unknown";
  const downgraded =
    j.thinking_mixed === 1 ||
    (j.thinking_applied && j.thinking_applied !== j.thinking_requested) ||
    j.thinking_verified === 0;
  const verified = j.thinking_verified === 1 && !downgraded;
  const label = j.thinking_mixed
    ? `${want} · mixed`
    : j.thinking_applied && j.thinking_applied !== j.thinking_requested
      ? `${want} → ${got}`
      : verified
        ? `${want} ✓`
        : `${want} ?`;
  const detail = `Requested ${want}; applied ${got}; verified ${
    j.thinking_verified === 1
      ? "yes"
      : j.thinking_verified === 0
        ? "no"
        : "unknown"
  }${j.thinking_fallback ? `; fallback ${j.thinking_fallback}` : ""}`;
  return `<span class="effort-badge ${downgraded ? "warn" : verified ? "ok" : "unknown"}" title="${esc(detail)}" aria-label="${esc(detail)}">${uiIcon("sparkles")} ${esc(label)}</span>`;
}

export function renderJobs() {
  const map = Object.fromEntries(S.accounts.map((a) => [a.id, a.name]));
  const all = S.jobs || [];
  const filtered = filterJobs(all, S.jobsFilter, S.jobQuery, map);
  const count = $("jobCount");
  if (count)
    count.textContent = all.length
      ? `${filtered.length} of ${all.length} jobs`
      : "0 jobs";
  $("jobsTable").innerHTML = filtered.length
    ? filtered
        .map((j) => {
          const resolved = Boolean(j.resolution);
          const resolutionLabel = resolved
            ? String(j.resolution).replaceAll("_", " ")
            : "";
          const action =
            j.status === "unknown" && !resolved
              ? `<button class="small-action accent" data-action="resolve-job" data-id="${j.id}">${uiIcon("sparkles")} Resolve</button>`
              : j.status === "queued"
                ? `<button class="small-action danger" data-action="delete-job" data-id="${j.id}">Cancel</button>`
                : j.status !== "running"
                  ? `<button class="small-action" data-action="delete-job" data-id="${j.id}">Delete</button>`
                  : "";
          const resolution = resolved
            ? `<span class="job-resolution">resolved · <strong>${esc(resolutionLabel)}</strong>${
                j.resolved_at
                  ? " · " + esc(new Date(j.resolved_at).toLocaleString())
                  : ""
              }</span>`
            : "";
          return `<tr><td data-label="JOB / PROMPT"><div class="job-prompt" title="${esc(j.prompt)}">${esc(trunc(j.prompt, 90))}</div><div class="job-id">${esc(j.id.slice(0, 8))}${j.reference_count ? " · " + j.reference_count + " ref image(s)" : ""}</div></td><td data-label="ACCOUNT">${esc(map[j.account_id] || "Deleted account")}</td><td data-label="STATUS">${statusPill(j.status)}<div class="job-id">${j.image_count || 0}/${j.n || 1} images</div>${resolution}${effortBadge(j)}${qualityBadge(j)}</td><td data-label="CREATED">${esc(new Date(j.created_at).toLocaleString())}</td><td data-label="OUTPUT">${j.image_url ? `<a href="${j.image_url}" target="_blank" rel="noopener">${uiIcon("external")} View ${j.image_count || 1} image(s)</a>` : esc(trunc(j.error || "—", 60))}</td><td data-label="ACTION">${action}</td></tr>`;
        })
        .join("")
    : `<tr><td colspan="6" class="jobs-empty">${
        all.length
          ? "No jobs match this filter or search."
          : "No jobs yet. Open Image Studio to create one."
      }</td></tr>`;
}

export function renderGallery() {
  const items = S.gallery;
  $("galleryGrid").innerHTML = items.length
    ? items
        .map(
          (im) =>
            `<article class="gallery-item"><a href="${im.url}" target="_blank" rel="noopener"><img alt="${esc(trunc(im.prompt, 80))}" src="${im.preview_url || im.url}" loading="lazy" decoding="async" width="640" height="480"></a><div class="gallery-meta"><div class="asset-kicker">ORIGINAL · ${esc(im.image_format)} <span>#${im.position + 1}</span></div><h3 title="${esc(im.prompt)}">${esc(trunc(im.prompt, 100))}</h3><p>${im.image_width} × ${im.image_height} · ${(im.image_bytes / 1048576).toFixed(2)} MiB</p><div class="asset-actions"><a class="text-button" href="${im.url}?download=true">${uiIcon("download")} Download original</a><a class="text-button" href="/api/exports/jobs/${im.job_id}.zip">${uiIcon("zip")} Job ZIP</a></div></div></article>`,
        )
        .join("")
    : '<div class="empty"><h3>Your private image archive</h3><p>Original files and their metadata will appear here after a job finishes.</p><button class="btn secondary" data-go="studio">Create your first image</button></div>';
  $("galleryMore").hidden = !S.galleryCursor;
  $("galleryCount").textContent = items.length + " images loaded";
}

export function renderSettings() {
  const s = S.settings;
  const rows = [
    ["Gateway runtime", s.gateway_installed ? "Installed" : "Not installed"],
    ["Max live workers", s.max_running_accounts],
    [
      "Worker policy",
      s.worker_policy ||
        (Number(s.idle_worker_shutdown_minutes) > 0 ? "on-demand" : "always-on"),
    ],
    ["Idle shutdown", s.idle_worker_shutdown_minutes + " min"],
    ["Database", s.database_type],
    ["Agent API", s.agent_api_enabled ? "Enabled" : "Not configured"],
    ["Secure cookies", s.cookie_secure ? "HTTPS only" : "Local HTTP only"],
    ["Gateway source", "stufently/gpt-web-gateway"],
    ["Data directory", s.data_path],
    [
      "Disk free",
      (s.disk_free_mb == null ? "unknown" : compact(s.disk_free_mb) + " MB") +
        (s.disk_low ? " · LOW (dispatch paused)" : ""),
    ],
    ["Preview retention", s.preview_retention_days + " days"],
    ["Export retention", s.export_retention_hours + " hours"],
    ["Worker log limit", s.worker_log_max_mb + " MB"],
    ["Render wait cap", (s.render_wait_sec ?? 15) + " s"],
    ["Image effort", (s.thinking_effort_label || s.thinking_effort || "High") + (s.effort_below_requested_24h ? " · " + s.effort_below_requested_24h + " fallback(s) in 24h" : "")],
    [
      "Reference uploads",
      s.upload_limits
        ? `${s.upload_limits.max_files} files · ${s.upload_limits.max_file_mb} MiB each · ${s.upload_limits.max_total_mb} MiB total`
        : "10 files",
    ],
    [
      "Upload workspace",
      s.upload_usage
        ? `${s.upload_usage.count} upload(s) · ${(s.upload_usage.bytes / 1048576).toFixed(1)} MiB`
        : "—",
    ],
  ];
  $("settingsList").innerHTML = rows
    .map(
      ([k, v]) =>
        `<div class="setting-item"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`,
    )
    .join("");
}

