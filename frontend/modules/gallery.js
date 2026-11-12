import { $, toast } from "./dom.js";
import { loadGallery } from "./controller.js";


export function initGallery() {
  $("galleryRefresh").addEventListener("click", () =>
    loadGallery(true).catch((e) => toast(e.message, true)),
  );
  $("galleryMore").addEventListener("click", () =>
    loadGallery().catch((e) => toast(e.message, true)),
  );
}
