/* Explicit, isolated browser-only demo. Never imported by the Python app. */
(() => {
  const nativeFetch = window.fetch.bind(window);
  const randomId = () => crypto.getRandomValues(new Uint32Array(4)).join("-");
  const stamp = () => new Date().toISOString();
  const defaults = {
    accounts: [
      {
        id: "11111111-1111-4111-8111-111111111111",
        name: "Creative Studio",
        email_label: "studio@example.test",
        runtime: "online",
        session: "connected",
        plan_label: "plus",
        parallel_limit: 2,
        dispatch_enabled: 1,
        autostart: 0,
        memory_mb: 864,
        created_at: stamp(),
        archived_at: null,
      },
      {
        id: "22222222-2222-4222-8222-222222222222",
        name: "Product Campaigns",
        email_label: "products@example.test",
        runtime: "stopped",
        session: "unknown",
        plan_label: "unknown",
        parallel_limit: 1,
        dispatch_enabled: 0,
        autostart: 0,
        memory_mb: null,
        created_at: stamp(),
        archived_at: null,
      },
      {
        id: "33333333-3333-4333-8333-333333333333",
        name: "Editorial Workspace",
        email_label: "editorial@example.test",
        runtime: "online",
        session: "unverified",
        plan_label: "plus",
        parallel_limit: 2,
        dispatch_enabled: 1,
        autostart: 0,
        memory_mb: 728,
        created_at: stamp(),
        archived_at: null,
      },
    ],
    jobs: [
      {
        id: "44444444-4444-4444-8444-444444444444",
        account_id: "11111111-1111-4111-8111-111111111111",
        prompt:
          "A clean editorial product composition with soft studio lighting",
        status: "queued",
        n: 3,
        created_at: stamp(),
        images: [],
        image_count: 0,
      },
      {
        id: "55555555-5555-4555-8555-555555555555",
        account_id: "22222222-2222-4222-8222-222222222222",
        prompt: "Minimal campaign visual with a warm architectural backdrop",
        status: "queued",
        n: 2,
        created_at: stamp(),
        images: [],
        image_count: 0,
      },
    ],
    config: {
      global_max_inflight: 4,
      gateway_max_tabs: 3,
      max_pending_jobs: 1000,
      max_batch_images: 4,
      provider_cooldown_minutes: 30,
      max_running_accounts: 2,
      dispatch_paused: 0,
      target_images: 500,
      target_window_hours: 5,
    },
    audit: [],
  };
  let data;
  try {
    data =
      JSON.parse(localStorage.getItem("ninja-preview-v5")) ||
      structuredClone(defaults);
  } catch {
    data = structuredClone(defaults);
  }
  const save = () =>
    localStorage.setItem("ninja-preview-v5", JSON.stringify(data));
  const reply = (value, status = 200) =>
    new Response(JSON.stringify(value), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  const failure = (message, status = 409) => reply({ detail: message }, status);
  const audit = (action, detail) => {
    data.audit.unshift({
      id: randomId(),
      action,
      detail: JSON.stringify(detail),
      created_at: stamp(),
    });
    save();
  };
  window.fetch = async (input, options = {}) => {
    const url = new URL(
      typeof input === "string" ? input : input.url,
      location.origin,
    );
    if (!url.pathname.startsWith("/api/")) return nativeFetch(input, options);
    const path = url.pathname,
      method = options.method || "GET";
    let body = {};
    try {
      body = JSON.parse(options.body || "{}");
    } catch {}
    if (path === "/api/auth/me")
      return reply({ authenticated: true, csrf: "preview-only" });
    if (path === "/api/auth/login") return reply({ csrf: "preview-only" });
    if (path === "/api/auth/logout") return reply({ ok: true });
    if (path === "/api/overview")
      return reply({
        accounts: data.accounts.filter((a) => !a.archived_at).length,
        jobs: data.jobs.length,
        completed: 0,
        active: data.jobs.filter((j) => j.status === "queued").length,
      });
    if (path === "/api/accounts" && method === "GET")
      return reply(data.accounts);
    if (path === "/api/accounts" && method === "POST") {
      const a = {
        id: randomId(),
        ...body,
        runtime: "stopped",
        session: "unknown",
        dispatch_enabled: 1,
        created_at: stamp(),
        archived_at: null,
      };
      data.accounts.push(a);
      save();
      return reply(a, 201);
    }
    const match = path.match(/^\/api\/accounts\/([^/]+)(?:\/(.*))?$/);
    if (match) {
      const account = data.accounts.find((a) => a.id === match[1]);
      if (!account) return failure("Account not found.", 404);
      const action = match[2];
      if (method === "PATCH") {
        Object.assign(account, body);
        save();
        return reply(account);
      }
      if (action === "logs")
        return reply({
          logs: "[preview] Browser workers are simulated. No account is connected.\n[preview] Run the downloaded Python application for real worker logs.",
        });
      if (action === "start") {
        account.runtime = "online";
        account.session = "unverified";
      } else if (action === "stop") {
        account.runtime = "stopped";
        account.session = "unknown";
      } else if (action === "archive") {
        account.archived_at = stamp();
        account.runtime = "archived";
      } else if (action === "restore") {
        account.archived_at = null;
        account.runtime = "stopped";
      } else if (action === "purge") {
        if (body.confirmation !== "DELETE " + account.name)
          return failure("Confirmation did not match.", 422);
        data.accounts = data.accounts.filter((a) => a.id !== account.id);
        data.jobs = data.jobs.filter((j) => j.account_id !== account.id);
      } else if (action?.startsWith("dispatch/")) {
        account.dispatch_enabled = action.endsWith("resume") ? 1 : 0;
        audit("account.dispatch", {
          account_id: account.id,
          mode: action.split("/")[1],
        });
      } else
        return failure(
          "This operation requires the downloaded local application.",
        );
      save();
      return reply({ ok: true });
    }
    if (path === "/api/jobs") return reply(data.jobs);
    if (path.startsWith("/api/jobs/") && method === "DELETE") {
      const j = data.jobs.find((j) => j.id === path.split("/").pop());
      if (j?.status === "queued") j.status = "canceled";
      else data.jobs = data.jobs.filter((x) => x !== j);
      save();
      return reply({ ok: true });
    }
    if (path === "/api/generate") {
      const existing = data.jobs.find(
        (j) => j.idempotency_key === body.idempotency_key,
      );
      if (existing) return reply(existing, 202);
      if (
        !data.accounts.find((a) => a.id === body.account_id && !a.archived_at)
      )
        return failure("Select an active sample account.", 422);
      if (body.n > data.config.max_batch_images)
        return failure(
          "The sample batch limit is " + data.config.max_batch_images + ".",
          422,
        );
      const j = {
        ...body,
        id: randomId(),
        status: "queued",
        created_at: stamp(),
        images: [],
        image_count: 0,
        error: "Preview only: this request will not be sent to a provider.",
      };
      data.jobs.unshift(j);
      save();
      return reply(j, 202);
    }
    if (path === "/api/gallery") return reply({ items: [], next_cursor: null });
    if (path === "/api/settings")
      return reply({
        gateway_installed: false,
        database_type: "Preview / browser storage",
        agent_api_enabled: false,
        cookie_secure: true,
        data_path: "Local application only",
        ...data.config,
      });
    if (path === "/api/control/audit") return reply(data.audit.slice(0, 12));
    if (path === "/api/control") {
      if (method === "PATCH") {
        const before = { ...data.config };
        Object.assign(data.config, body);
        audit("config.update", { before, after: body });
      }
      return reply({
        config: data.config,
        active_workers: 2,
        running_jobs: 0,
        restart_required: [],
      });
    }
    if (path === "/api/queue")
      return reply({
        queued: data.jobs.filter((j) => j.status === "queued").length,
        running: 0,
        max_global_inflight: data.config.global_max_inflight,
        completed: 0,
        partial: 0,
        unknown: 0,
      });
    if (path === "/api/analytics") {
      const hours = Number(url.searchParams.get("hours") || 5),
        end = new Date(),
        start = new Date(end - hours * 3600000);
      return reply({
        hours,
        window_start: start.toISOString(),
        window_end: end.toISOString(),
        images: 0,
        jobs: data.jobs.length,
        requested_images: data.jobs.reduce((n, j) => n + j.n, 0),
        throughput_images_per_hour: 0,
        target_window_projection: 0,
        target_images: data.config.target_images,
        target_window_hours: data.config.target_window_hours,
        target_progress_percent: 0,
        p50_sec: null,
        p95_sec: null,
        queued_oldest_age_min: 0,
        status_counts: {
          queued: data.jobs.filter((j) => j.status === "queued").length,
        },
        series: Array.from({ length: hours <= 24 ? hours : 28 }, (_, i) => ({
          time: new Date(
            +start + (i * hours * 3600000) / (hours <= 24 ? hours : 28),
          ).toISOString(),
          images: 0,
        })),
        accounts: data.accounts.map((a) => ({
          account_id: a.id,
          name: a.name,
          jobs: data.jobs.filter((j) => j.account_id === a.id).length,
          images: 0,
          failed: 0,
          partial: 0,
          unknown: 0,
          rate_limited: 0,
        })),
        recent_failures: [],
        note: "Interactive preview: accounts, RAM and queued requests are sample data. No image generation has been measured.",
      });
    }
    return failure(
      "This feature is available in the downloaded local application.",
      404,
    );
  };
  document.addEventListener("DOMContentLoaded", () => {
    const banner = document.createElement("div");
    banner.className = "preview-banner";
    banner.innerHTML =
      'INTERACTIVE PREVIEW · Sample accounts · No provider connected <button id="resetPreview">Reset demo</button>';
    document.body.prepend(banner);
    document.getElementById("resetPreview").onclick = () => {
      localStorage.removeItem("ninja-preview-v5");
      location.reload();
    };
    document.body.addEventListener(
      "click",
      (e) => {
        if (e.target.closest('[data-action="login"]')) {
          e.stopImmediatePropagation();
          alert(
            "Real account login is available in the downloaded local app. This preview never asks for account credentials.",
          );
        }
      },
      true,
    );
  });
})();
