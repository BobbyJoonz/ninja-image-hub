// Headless browser smoke for the real panel. Fails on any uncaught exception, a view that
// does not paint, or a broken Studio reference picker. Runs against a fresh panel in CI.
//
//   PANEL_URL=http://127.0.0.1:8095 PANEL_PASSWORD=... REF_FILE=tests/fixtures/reference.png \
//   CHROME_PATH=/path/to/chrome node tests/browser_smoke.mjs
import { spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const PANEL_URL = (process.env.PANEL_URL || "http://127.0.0.1:8081").replace(/\/$/, "");
const PASSWORD = process.env.PANEL_PASSWORD;
const CHROME = process.env.CHROME_PATH || "chrome";
const REF_FILE = process.env.REF_FILE;
const OUT_DIR = process.env.OUT_DIR || path.join(os.tmpdir(), "ninja-browser-smoke");
const CDP_PORT = Number(process.env.CDP_PORT || 9300 + Math.floor(Math.random() * 300));
const VIEWS = [
  "overview",
  "accounts",
  "studio",
  "jobs",
  "gallery",
  "analytics",
  "operations",
  "settings",
];
const failures = [];
const exceptions = [];
let ws;
let chrome;
let messageId = 0;

function fail(message) {
  failures.push(message);
  console.error("FAIL:", message);
}

async function cdp(method, params = {}) {
  const id = ++messageId;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`CDP timeout: ${method}`)), 20000);
    const handler = (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== id) return;
      clearTimeout(timer);
      ws.removeEventListener("message", handler);
      message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
    };
    ws.addEventListener("message", handler);
  });
}

async function evaluate(expression) {
  const result = await cdp("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  return result.result.value;
}

async function waitFor(predicate, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  return false;
}

async function main() {
  if (!PASSWORD) throw new Error("PANEL_PASSWORD is required");
  if (!REF_FILE || !fs.existsSync(REF_FILE))
    throw new Error(`REF_FILE is missing: ${REF_FILE || "(unset)"}`);
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "ninja-browser-profile-"));
  chrome = spawn(
    CHROME,
    [
      "--headless=new",
      "--disable-gpu",
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--no-first-run",
      "--remote-debugging-port=" + CDP_PORT,
      "--remote-allow-origins=*",
      "--user-data-dir=" + profile,
      "about:blank",
    ],
    { stdio: "ignore" },
  );

  let version;
  for (let i = 0; i < 80; i += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`);
      version = await response.json();
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  if (!version) throw new Error("Chrome did not expose the CDP endpoint");
  console.log("chrome:", version.Browser);

  const target = await (
    await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?about:blank`, { method: "PUT" })
  ).json();
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });
  ws.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.method === "Runtime.exceptionThrown") {
      const details = message.params.exceptionDetails;
      exceptions.push(details.exception?.description || details.text || "exception");
    }
  });
  await cdp("Runtime.enable");
  await cdp("Page.enable");

  await cdp("Page.navigate", { url: PANEL_URL });
  const appReady = await waitFor(
    () => evaluate("document.documentElement.dataset.appReady === '1'"),
    30000,
  );
  if (!appReady) fail("app modules never initialised");
  if (!(await evaluate("Boolean(document.getElementById('loginForm'))")))
    fail("login form did not load");
  await evaluate(
    `document.getElementById('password').value=${JSON.stringify(PASSWORD)};` +
      "document.getElementById('loginForm').requestSubmit(); true",
  );
  await new Promise((resolve) => setTimeout(resolve, 1200));
  const loggedIn = await waitFor(
    () => evaluate("document.getElementById('app') && !document.getElementById('app').hidden"),
    30000,
  );
  if (!loggedIn) {
    fail("login did not reach the app");
  }

  for (const view of VIEWS) {
    const clicked = await evaluate(
      `document.querySelector('[data-view="${view}"]')?.click(); true`,
    );
    if (!clicked) fail(`missing nav for ${view}`);
    await new Promise((resolve) => setTimeout(resolve, 200));
    const visible = await evaluate(
      `!document.querySelector('[data-page="${view}"]')?.hidden`,
    );
    const text = await evaluate(
      `(document.querySelector('[data-page="${view}"]')?.innerText || '').trim().length`,
    );
    if (!visible) fail(`${view} page is hidden`);
    if (text < 5) fail(`${view} page painted no content`);
    console.log(`${view}: visible=${visible} text=${text}`);
  }

  await evaluate(`document.querySelector('[data-view="studio"]').click(); true`);
  const base64 = fs.readFileSync(REF_FILE).toString("base64");
  await evaluate(`(async () => {
    const blob = await (await fetch('data:image/png;base64,${base64}')).blob();
    const file = new File([blob], 'browser-smoke.png', {type: 'image/png'});
    const transfer = new DataTransfer();
    transfer.items.add(file);
    const input = document.getElementById('genFiles');
    Object.defineProperty(input, 'files', {value: transfer.files, configurable: true});
    input.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
  })()`);
  const chip = await waitFor(
    () => evaluate("document.querySelectorAll('.file-chip').length > 0"),
    10000,
  );
  if (!chip) fail("reference picker did not produce a chip");
  const optimizeDefault = await evaluate("document.getElementById('optimizeRefs').checked");
  if (optimizeDefault !== false) fail("reference downscale must stay opt-in (off by default)");

  const shot = path.join(OUT_DIR, "browser-smoke.png");
  const screenshot = await cdp("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(shot, Buffer.from(screenshot.data, "base64"));
  console.log("screenshot:", shot);

  if (exceptions.length) fail(`uncaught exceptions: ${exceptions.join(" | ")}`);
  if (failures.length) {
    console.error(`${failures.length} browser smoke failure(s)`);
    process.exitCode = 1;
  } else {
    console.log("browser smoke OK: all views painted, reference chip works, zero exceptions");
  }
}

try {
  await main();
} catch (error) {
  console.error("browser smoke crashed:", error.message);
  process.exitCode = 1;
} finally {
  try {
    ws?.close();
  } catch {}
  chrome?.kill("SIGKILL");
  await new Promise((resolve) => setTimeout(resolve, 150));
}
