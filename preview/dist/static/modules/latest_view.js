import { S } from "./state.js";
import { $, esc, compact, fmtSec, relativeMinutes, uiIcon } from "./dom.js";


export function renderLatest() {
  let j = S.jobs.find((j) => j.id === S.latestJobId) || S.jobs[0];
  if (!j) {
    renderLatest.fingerprint = null;
    $("latestStatus").textContent = "No active generation";
    $("latestPreview").innerHTML =
      '<div class="preview-graphic">' + uiIcon("mask") + '</div><h3>Ready when you are.</h3><p>Your next image will appear here when the job completes.</p>';
    return;
  }
  const fingerprint = JSON.stringify([j.id, j.status, j.error, j.images]);
  if (renderLatest.fingerprint === fingerprint) return;
  renderLatest.fingerprint = fingerprint;
  $("latestStatus").textContent =
    `Job ${j.id.slice(0, 8)} · ${j.status} · ${j.image_count || 0}/${j.n || 1} images${j.error ? " · " + j.error : ""}`;
  if (j.images?.length) {
    $("latestPreview").innerHTML =
      `<div class="batch-preview">${j.images.map((im) => `<div><a href="${im.url}" target="_blank" rel="noopener"><img loading="lazy" decoding="async" src="${im.preview_url || im.url}" alt="Generated output #${im.position + 1}"></a><p>${im.image_width}×${im.image_height} · ${esc(im.image_format)}<br><a href="${im.url}?download=true">${uiIcon("download")} Download #${im.position + 1}</a></p></div>`).join("")}</div>`;
  } else {
    $("latestPreview").innerHTML =
      `<div class="preview-graphic">${j.status === "failed" ? uiIcon("alert") : j.status === "running" ? uiIcon("loading") : j.status === "queued" ? uiIcon("hourglass") : uiIcon("mask")}</div><h3>${j.status === "running" ? "Generating image…" : j.status === "queued" ? "Waiting in queue…" : j.status === "failed" ? "Generation failed" : "Ready when you are."}</h3><p>${esc(trunc(j.error || j.prompt, 110))}</p>`;
  }
}
