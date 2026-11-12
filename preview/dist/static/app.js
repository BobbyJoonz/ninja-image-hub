import { boot } from "./modules/controller.js";
import { initBootstrap } from "./modules/bootstrap.js";
import { initGenerate } from "./modules/generate.js";
import { initGallery } from "./modules/gallery.js";
import { initJobsToolbar } from "./modules/jobs_toolbar.js";
import { initOperations } from "./modules/operations.js";

initBootstrap();
initGenerate();
initGallery();
initJobsToolbar();
initOperations();
boot();
