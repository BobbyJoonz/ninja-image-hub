import { S } from "./state.js";
import { $, toast, uniqueId } from "./dom.js";
import { api, apiUpload } from "./api.js";
import { refreshData } from "./controller.js";
import { clearReferenceFiles } from "./studio.js";

// ---------------------------------------------------------------- reference images
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


export function initGenerate() {
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
}
