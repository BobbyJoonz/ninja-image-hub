import { S } from "./state.js";
import { $, esc, trunc, compact, fmtSec, relativeMinutes } from "./dom.js";
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

export function accountCards(arr, archived = false) {
  if (!arr.length)
    return archived
      ? '<div class="empty"><p>No archived accounts.</p></div>'
      : '<div class="empty"><h3>No active accounts yet</h3><p>Add an account to create an isolated browser workspace.</p><button class="btn primary" data-action="add">＋ Add account</button></div>';
  return arr
    .map(
      (a) =>
        `<article class="account-card"><div class="account-top"><div class="account-icon">♙</div><div class="identity"><div class="account-name">${esc(a.name)}</div><div class="account-email">${esc(a.email_label || "No email label")}</div></div><div class="account-pill"><span class="pill ${esc(a.runtime)}">● ${esc(a.runtime)}</span><span class="pill ${esc(a.session)}">${esc(a.session)}</span></div></div><div class="account-meta"><span>Plan: <strong>${esc((a.plan_label || "unknown").toUpperCase())} · manual</strong></span><span>Autostart: <strong>${a.autostart ? "On" : "Off"}</strong></span><span>Parallel: <strong>${a.parallel_limit || 1} job(s)</strong></span><span>RAM: <strong>${a.memory_mb == null ? "—" : a.memory_mb + " MiB"}</strong></span><span>Dispatch: <strong>${a.dispatch_enabled ? "On" : "Paused"}</strong></span>${a.restart_required ? '<span class="plan-pending">Restart required</span>' : ""}${a.auto_login_configured ? '<span class="plan-pending">Auto-login</span>' : ""}${a.cooldown_until ? `<span>Cooldown: <strong>${esc(a.cooldown_until)}</strong></span>` : ""}</div><div class="account-buttons">${archived ? `<button class="small-action accent" data-action="restore" data-id="${a.id}">↶ Restore</button><button class="small-action danger" data-action="purge" data-id="${a.id}">Delete forever</button>` : `<button class="small-action" data-action="edit" data-id="${a.id}">✎ Edit</button>${a.runtime === "stopped" ? `<button class="small-action accent" data-action="start" data-id="${a.id}">▶ Start</button>` : `<button class="small-action" data-action="stop" data-id="${a.id}">■ Stop</button>`}<button class="small-action accent" data-action="login" data-id="${a.id}">↗ Login</button><button class="small-action" data-action="${a.dispatch_enabled ? "pause-dispatch" : "resume-dispatch"}" data-id="${a.id}">${a.dispatch_enabled ? "Pause queue" : "Resume queue"}</button><button class="small-action" data-action="logs" data-id="${a.id}">☷ Logs</button><button class="small-action danger" data-action="archive" data-id="${a.id}">Archive</button>`}</div></article>`,
    )
    .join("");
}

export function statusPill(v) {
  return `<span class="pill ${esc(v)}">${esc(v)}</span>`;
}

export function renderJobs() {
  const map = Object.fromEntries(S.accounts.map((a) => [a.id, a.name]));
  $("jobsTable").innerHTML = S.jobs.length
    ? S.jobs
        .map(
          (j) =>
            `<tr><td><div class="job-prompt" title="${esc(j.prompt)}">${esc(trunc(j.prompt, 70))}</div><div class="job-id">${esc(j.id.slice(0, 8))}</div></td><td>${esc(map[j.account_id] || "Deleted account")}</td><td>${statusPill(j.status)}<div class="job-id">${j.image_count || 0}/${j.n || 1} images</div></td><td>${esc(new Date(j.created_at).toLocaleString())}</td><td>${j.image_url ? `<a href="${j.image_url}" target="_blank" rel="noopener">View ${j.image_count || 1} image(s) ↗</a>` : esc(trunc(j.error || "—", 60))}</td><td>${j.status === "queued" ? `<button class="small-action danger" data-action="delete-job" data-id="${j.id}">Cancel</button>` : j.status !== "running" ? `<button class="small-action" data-action="delete-job" data-id="${j.id}">Delete</button>` : ""}</td></tr>`,
        )
        .join("")
    : '<tr><td colspan="6" style="text-align:center;color:#92a8be;padding:40px">No jobs yet. Open Image Studio to create one.</td></tr>';
}

export function renderGallery() {
  const items = S.gallery;
  $("galleryGrid").innerHTML = items.length
    ? items
        .map(
          (im) =>
            `<article class="gallery-item"><a href="${im.url}" target="_blank" rel="noopener"><img alt="${esc(trunc(im.prompt, 80))}" src="${im.preview_url || im.url}" loading="lazy" decoding="async" width="640" height="480"></a><div class="gallery-meta"><div class="asset-kicker">ORIGINAL · ${esc(im.image_format)} <span>#${im.position + 1}</span></div><h3 title="${esc(im.prompt)}">${esc(trunc(im.prompt, 100))}</h3><p>${im.image_width} × ${im.image_height} · ${(im.image_bytes / 1048576).toFixed(2)} MiB</p><div class="asset-actions"><a class="text-button" href="${im.url}?download=true">Download original ↓</a><a class="text-button" href="/api/exports/jobs/${im.job_id}.zip">Job ZIP ↗</a></div></div></article>`,
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
  ];
  $("settingsList").innerHTML = rows
    .map(
      ([k, v]) =>
        `<div class="setting-item"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`,
    )
    .join("");
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
  $("analyticsChart").innerHTML = a.series
    .map(
      (x, i) =>
        `<div class="bar-column" title="${esc(new Date(x.time).toLocaleString())}: ${x.images} image(s)" aria-label="Bucket ${i + 1}: ${x.images} images"><span class="bar-count">${x.images || ""}</span><div class="bar" style="height:${Math.max(x.images ? 8 : 2, Math.round((x.images / max) * 100))}%"></div></div>`,
    )
    .join("");
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
  $("opsCapacity").innerHTML =
    `<div><span>Running workers</span><b>${compact(S.control.active_workers)} / ${compact(c.max_running_accounts)}</b></div><div><span>Active requests</span><b>${compact(S.queue.running)} / ${compact(c.global_max_inflight)}</b></div><div><span>Waiting jobs</span><b>${compact(S.queue.queued)} / ${compact(c.max_pending_jobs)}</b></div><div><span>Batch limit</span><b>${compact(c.max_batch_images)}</b></div><div><span>Tab limit</span><b>${compact(c.gateway_max_tabs)} / worker</b></div>`;
  $("opsStrip").innerHTML =
    `<span class="live-indicator ${paused ? "paused" : ""}"></span><strong>${paused ? "Dispatch paused" : "Queue operational"}</strong><span>${compact(S.queue.queued)} pending</span><span>${compact(S.queue.running)} active / ${compact(c.global_max_inflight)} slots</span><button class="text-button" data-go="operations">Adjust capacity →</button>`;
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

export function renderLatest() {
  let j = S.jobs.find((j) => j.id === S.latestJobId) || S.jobs[0];
  if (!j) {
    renderLatest.fingerprint = null;
    $("latestStatus").textContent = "No active generation";
    $("latestPreview").innerHTML =
      '<div class="preview-graphic">✣</div><h3>Ready when you are.</h3><p>Your next image will appear here when the job completes.</p>';
    return;
  }
  const fingerprint = JSON.stringify([j.id, j.status, j.error, j.images]);
  if (renderLatest.fingerprint === fingerprint) return;
  renderLatest.fingerprint = fingerprint;
  $("latestStatus").textContent =
    `Job ${j.id.slice(0, 8)} · ${j.status} · ${j.image_count || 0}/${j.n || 1} images${j.error ? " · " + j.error : ""}`;
  if (j.images?.length) {
    $("latestPreview").innerHTML =
      `<div class="batch-preview">${j.images.map((im) => `<div><a href="${im.url}" target="_blank" rel="noopener"><img loading="lazy" decoding="async" src="${im.preview_url || im.url}" alt="Generated output #${im.position + 1}"></a><p>${im.image_width}×${im.image_height} · ${esc(im.image_format)}<br><a href="${im.url}?download=true">Download #${im.position + 1} ↧</a></p></div>`).join("")}</div>`;
  } else {
    $("latestPreview").innerHTML =
      `<div class="preview-graphic">${j.status === "failed" ? "!" : j.status === "running" ? "◌" : "✣"}</div><h3>${j.status === "running" ? "Generating image…" : j.status === "queued" ? "Waiting in queue…" : j.status === "failed" ? "Generation failed" : "Ready when you are."}</h3><p>${esc(trunc(j.error || j.prompt, 110))}</p>`;
  }
}
