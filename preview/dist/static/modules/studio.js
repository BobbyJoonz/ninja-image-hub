import { S } from "./state.js";
import { $, toast, uiIcon } from "./dom.js";

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

export function clearReferenceFiles() {
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

export function initStudio() {
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
}
