import { S } from "./modules/state.js";
import { $, toast, uniqueId, uiIcon } from "./modules/dom.js";
import { api, apiUpload } from "./modules/api.js";
import {
  showAuth,
  showApp,
  boot,
  refreshData,
  switchView,
  loadGallery,
} from "./modules/controller.js";
import { renderAnalytics, renderJobs } from "./modules/views.js";
import {
  onAccountAction,
  openAdd,
  initAccountDetails,
  initLoginDialog,
  initResolveDialog,
} from "./modules/accounts.js";

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

// ---------------------------------------------------------------- reference images
const fileInput = $("genFiles");
const fileDrop = $("fileDrop");
const fileChips = $("fileChips");

function uploadLimits() {
  return (
    S.settings.upload_limits || {
      max_files: 10,
      max_file_mb: 20,
      max_total_mb: 50,
      allowed_types: ["image/png", "image/jpeg", "image/webp"],
    }
  );
}

function humanBytes(bytes) {
  if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MiB";
  return Math.max(1, Math.round(bytes / 1024)) + " KiB";
}

function clearReferenceFiles() {
  for (const file of S.referenceFiles) {
    if (file.previewUrl) URL.revokeObjectURL(file.previewUrl);
  }
  S.referenceFiles = [];
  if (fileInput) fileInput.value = "";
  renderFileChips();
}

async function prepareReference(file, optimize) {
  // Decode once per file, sequentially: draw a 96 px chip preview, and (optionally)
  // downscale large references to <=2048 px WebP before they are uploaded. The original
  // File is only replaced when the optimized blob is actually smaller; turning the toggle
  // off uploads the selected bytes unchanged.
  const prepared = {
    file,
    name: file.name,
    size: file.size,
    lastModified: file.lastModified,
    originalName: file.name,
    originalSize: file.size,
    originalLastModified: file.lastModified,
    optimizedFrom: null,
    previewUrl: null,
  };
  try {
    if (typeof createImageBitmap === "function" && typeof document === "object") {
      const bitmap = await createImageBitmap(file);
      const maxEdge = Math.max(bitmap.width, bitmap.height);
      if (optimize && (maxEdge > 2048 || file.size > 2.5 * 1024 * 1024)) {
        const scale = Math.min(1, 2048 / maxEdge);
        const width = Math.max(1, Math.round(bitmap.width * scale));
        const height = Math.max(1, Math.round(bitmap.height * scale));
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        canvas.getContext("2d").drawImage(bitmap, 0, 0, width, height);
        const blob = await new Promise((resolve) =>
          canvas.toBlob(resolve, "image/webp", 0.85),
        );
        if (blob && blob.size < file.size) {
          const base =
            (file.name || "reference").replace(/\.[^.]+$/, "") || "reference";
          const optimized = new File([blob], base + ".webp", {
            type: "image/webp",
            lastModified: file.lastModified,
          });
          prepared.file = optimized;
          prepared.name = optimized.name;
          prepared.size = optimized.size;
          prepared.optimizedFrom = file.size;
        }
      }
      const previewScale = Math.max(1, maxEdge / 96);
      const previewWidth = Math.max(1, Math.round(bitmap.width / previewScale));
      const previewHeight = Math.max(1, Math.round(bitmap.height / previewScale));
      const previewCanvas = document.createElement("canvas");
      previewCanvas.width = previewWidth;
      previewCanvas.height = previewHeight;
      previewCanvas.getContext("2d").drawImage(bitmap, 0, 0, previewWidth, previewHeight);
      if (typeof bitmap.close === "function") bitmap.close();
      const preview = await new Promise((resolve) =>
        previewCanvas.toBlob(resolve, "image/jpeg", 0.72),
      );
      if (preview) prepared.previewUrl = URL.createObjectURL(preview);
    }
  } catch {
    /* older browser or a corrupt file: fall back to the original object URL */
  }
  if (!prepared.previewUrl) prepared.previewUrl = URL.createObjectURL(prepared.file);
  return prepared;
}

function renderFileChips() {
  if (!fileChips) return;
  fileChips.innerHTML = S.referenceFiles
    .map(
      (item, index) =>
        `<span class="file-chip">${
          item.previewUrl
            ? `<img src="${item.previewUrl}" alt="" width="34" height="34" />`
            : ""
        }<span class="file-meta"><strong title="${escAttr(item.originalName)}">${escAttr(item.originalName)}</strong><small>${humanBytes(item.size)}${item.optimizedFrom ? ` · optimized from ${humanBytes(item.optimizedFrom)}` : ""}</small></span><button type="button" data-file-index="${index}" aria-label="Remove ${escAttr(item.originalName)}">${uiIcon("x")}</button></span>`,
    )
    .join("");
}

function escAttr(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function validateSelection(files) {
  const caps = uploadLimits();
  if (files.length > caps.max_files)
    return `Attach at most ${caps.max_files} images per request.`;
  let total = 0;
  for (const file of files) {
    const type = (file.type || "").toLowerCase();
    const allowed =
      caps.allowed_types.includes(type) ||
      /\.(png|jpe?g|webp)$/i.test(file.name || "");
    if (!allowed)
      return `${file.name}: only PNG, JPEG or WebP images are supported.`;
    if (file.size > caps.max_file_mb * 1024 * 1024)
      return `${file.name}: larger than the ${caps.max_file_mb} MiB per-file limit.`;
    if (file.size === 0) return `${file.name}: the file is empty.`;
    total += file.size;
  }
  if (total > caps.max_total_mb * 1024 * 1024)
    return `Attachments exceed the ${caps.max_total_mb} MiB total limit.`;
  return "";
}

let referenceBusy = false;

async function addReferenceFiles(list) {
  if (referenceBusy) {
    toast("Still preparing the previous selection…", true);
    return;
  }
  const merged = [...S.referenceFiles];
  const added = [];
  for (const file of list) {
    const duplicate = merged.some(
      (item) =>
        item.originalName === file.name &&
        item.originalSize === file.size &&
        item.originalLastModified === file.lastModified,
    );
    if (!duplicate) added.push(file);
  }
  referenceBusy = true;
  try {
    for (const file of added) {
      merged.push(await prepareReference(file, S.optimizeReferences !== false));
    }
    const error = validateSelection(merged.map((item) => item.file));
    if (error) {
      toast(error, true);
      for (const item of merged) {
        if (!S.referenceFiles.includes(item) && item.previewUrl)
          URL.revokeObjectURL(item.previewUrl);
      }
      return;
    }
    S.referenceFiles = merged;
    renderFileChips();
  } finally {
    referenceBusy = false;
  }
}

if (fileInput) {
  fileInput.addEventListener("change", async () => {
    await addReferenceFiles([...fileInput.files]);
    fileInput.value = "";
  });
}
if (fileDrop) {
  ["dragenter", "dragover"].forEach((name) =>
    fileDrop.addEventListener(name, (event) => {
      event.preventDefault();
      fileDrop.classList.add("dragover");
    }),
  );
  ["dragleave", "drop"].forEach((name) =>
    fileDrop.addEventListener(name, (event) => {
      event.preventDefault();
      fileDrop.classList.remove("dragover");
    }),
  );
  fileDrop.addEventListener("drop", async (event) => {
    await addReferenceFiles([...(event.dataTransfer?.files || [])]);
  });
}
if (fileChips) {
  fileChips.addEventListener("click", (event) => {
    const button = event.target.closest("[data-file-index]");
    if (!button) return;
    const index = Number(button.dataset.fileIndex);
    const [removed] = S.referenceFiles.splice(index, 1);
    if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
    renderFileChips();
  });
}
const optimizeToggle = $("optimizeRefs");
try {
  S.optimizeReferences =
    localStorage.getItem("ninja.optimizeReferences") === "1";
} catch {
  S.optimizeReferences = false;
}
if (optimizeToggle) {
  optimizeToggle.checked = S.optimizeReferences;
  optimizeToggle.addEventListener("change", () => {
    S.optimizeReferences = optimizeToggle.checked;
    try {
      localStorage.setItem(
        "ninja.optimizeReferences",
        S.optimizeReferences ? "1" : "0",
      );
    } catch {
      /* storage may be disabled */
    }
    toast(
      S.optimizeReferences
        ? "Large references will be resized to WebP before upload."
        : "References will be uploaded exactly as selected.",
    );
  });
}
// An ambiguous submission (network drop, timeout) must be retried with the SAME
// idempotency key. Keeping it only in memory lost the key on reload, so a retry minted
// a new key and produced a duplicate job. sessionStorage survives a reload in this tab.
const PENDING_KEY = "ninja.pendingGeneration";
function loadPendingGeneration() {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}
function savePendingGeneration(value) {
  try {
    if (value) sessionStorage.setItem(PENDING_KEY, JSON.stringify(value));
    else sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* storage may be disabled; the in-memory key still works for this page */
  }
}
$("generateForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = $("generateBtn");
  const idleMarkup = button.innerHTML;
  button.disabled = true;
  const payload = {
    account_id: $("genAccount").value,
    prompt: $("genPrompt").value,
    aspect_ratio: S.ratio,
    quality_preference: $("qualityPreference").value,
    n: Number($("genCount").value),
  };
  const fileSignature = S.referenceFiles
    .map((item) => `${item.name}:${item.size}:${item.lastModified}`)
    .join("|");
  const fingerprint = JSON.stringify({ ...payload, files: fileSignature });
  S.pendingGeneration = S.pendingGeneration || loadPendingGeneration();
  if (S.pendingGeneration?.fingerprint !== fingerprint)
    S.pendingGeneration = { fingerprint, key: "ui-" + uniqueId(), uploadId: null };
  savePendingGeneration(S.pendingGeneration);
  try {
    let uploadId = S.pendingGeneration.uploadId;
    if (S.referenceFiles.length && !uploadId) {
      button.innerHTML = `Uploading ${S.referenceFiles.length} image(s)…`;
      const form = new FormData();
      for (const item of S.referenceFiles) form.append("files", item.file, item.name);
      const uploaded = await apiUpload("/api/uploads", form);
      uploadId = uploaded.upload_id;
      S.pendingGeneration.uploadId = uploadId;
      savePendingGeneration(S.pendingGeneration);
      button.innerHTML = "Queueing image job…";
    } else if (S.referenceFiles.length) {
      button.innerHTML = "Queueing image job…";
    }
    const job = await api("/api/generate", {
      method: "POST",
      body: JSON.stringify({
        ...payload,
        ...(uploadId ? { upload_id: uploadId } : {}),
        idempotency_key: S.pendingGeneration.key,
      }),
    });
    S.pendingGeneration = null;
    savePendingGeneration(null);
    S.latestJobId = job.id;
    const count = S.referenceFiles.length;
    clearReferenceFiles();
    toast(
      count
        ? `Image job queued with ${count} reference image(s).`
        : "Image job queued.",
    );
    await refreshData();
  } catch (err) {
    toast(
      err.message + " Retry the same request to reuse its submission key.",
      true,
    );
  } finally {
    button.disabled = false;
    button.innerHTML = idleMarkup;
  }
});
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
document.addEventListener("session-expired", showAuth);
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
$("galleryRefresh").addEventListener("click", () =>
  loadGallery(true).catch((e) => toast(e.message, true)),
);
$("galleryMore").addEventListener("click", () =>
  loadGallery().catch((e) => toast(e.message, true)),
);
boot();
