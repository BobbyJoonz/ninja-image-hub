"""Runtime patches applied to the vendored gateway workspace.

The vendored gateway is pinned but its Playwright selectors are not an API; every patch is a
literal anchor replacement and must stay idempotent and fail-open (an upstream change keeps
the worker usable, just without the optimisation). Keeping them in one registry lets the
doctor verify the same markers that the worker applies.
"""

import pathlib
import re
import sys

PATCH_ORDER = (
    "_patch_popup_follow",
    "_patch_project_url",
    "_patch_login_diagnostics",
    "_patch_plan_probe",
    "_patch_settings_probe",
    "_patch_live_view",
    "_patch_launch_args",
    "_patch_image_failure_detect",
    "_patch_fast_prompt_insert",
    "_patch_quality_gate",
    "_patch_quality_gate_v2",
)

PATCH_MARKERS = {
    "popup_follow": "ninja:popup-follow",
    "fast_prompt_insert": "ninja:fast-prompt-insert",
    "project_url": "ninja:project-url",
    "login_diagnostics": "ninja:pages-routes",
    "plan_probe": "ninja:plan-probe",
    "settings_probe": "ninja:settings-probe",
    "live_view": "ninja:live-view",
    "launch_args": "ninja:launch-args",
    "image_failure": "ninja:image-failure-detect",
    "quality_gate": "ninja:quality-gate",
}


_ASSET_DIR = pathlib.Path(__file__).resolve().parent / "patches"


def _asset(name):
    """Load JS injected into the vendored gateway, keeping this module readable."""
    return (_ASSET_DIR / name).read_text(encoding="utf-8")


DIAGNOSTICS_JS = _asset("diagnostics.js")
PLAN_PROBE_JS = _asset("plan_probe.js")
SETTINGS_PROBE_JS = _asset("settings_probe.js")
LIVE_VIEW_JS = _asset("live_view.js")


POPUP_FOLLOW_MARKER = "ninja:popup-follow"


def _patch_popup_follow(worker):
    """Let the remote login page follow OAuth popups (Google/Apple/phone).

    Upstream keeps one stored page and screenshots/clicks it. Clicking
    "Continue with Google" opens a popup in the same browser context, so the remote
    page stayed on the old tab and the user could not finish signing in. This small,
    guarded patch makes getLoginPage() prefer the newest open page. When upstream
    changes, the pattern simply does not match and the worker is left untouched.
    """
    path = pathlib.Path(worker) / "src" / "routes" / "login.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    if POPUP_FOLLOW_MARKER in content:
        return False
    old = (
        "async function getLoginPage() {\n"
        "  if (loginPage && !loginPage.isClosed()) return loginPage;\n"
    )
    if content.count(old) != 1:
        return False
    new = (
        "async function getLoginPage() {\n"
        "  // ninja:popup-follow — follow the newest open page so OAuth popups work.\n"
        "  try {\n"
        "    const context = await getContext();\n"
        "    const open = context.pages().filter((p) => p && !p.isClosed());\n"
        "    const newest = open[open.length - 1];\n"
        "    if (newest && newest !== loginPage) loginPage = newest;\n"
        "  } catch (err) {}\n"
        "  if (loginPage && !loginPage.isClosed()) return loginPage;\n"
    )
    path.write_text(content.replace(old, new, 1), encoding="utf-8")
    return True



FAST_INSERT_MARKER = "ninja:fast-prompt-insert"


def _patch_fast_prompt_insert(worker):
    """Insert the prompt in one CDP call when attachments must be preserved.

    `fill()` would wipe ChatGPT attachments, so upstream falls back to per-key typing at
    TYPE_DELAY_MS plus CDP round-trip overhead. Measured on this host: a 3,126-character
    prompt took 95.5 s to type and dominated the whole job (144.9 s gateway time).
    `keyboard.insertText` dispatches the same input events in one call and keeps the
    composer's attachment chips; if the text does not land the upstream verification path
    still falls back to per-key typing, so this patch is fail-open.
    """
    path = pathlib.Path(worker) / "src" / "chatgpt.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    if FAST_INSERT_MARKER in content:
        return False
    old = (
        "      typedPerKey = true;\n"
        "      await typeLines(text,\n"
        "        (line) => p.keyboard.type(line, { delay: TYPE_DELAY_MS }),\n"
        "        () => p.keyboard.press('Shift+Enter'));"
    )
    if content.count(old) != 1:
        return False
    new = (
        "      typedPerKey = true;\n"
        "      // ninja:fast-prompt-insert — keep attachments, skip the per-key delay.\n"
        "      const __insertStart = Date.now();\n"
        "      await typeLines(text,\n"
        "        (line) => p.keyboard.insertText(line),\n"
        "        () => p.keyboard.press('Shift+Enter'));\n"
        "      console.log(`[fast-insert] ${text.length} chars in ${Date.now() - __insertStart} ms`);"
    )
    path.write_text(content.replace(old, new, 1), encoding="utf-8")
    return True


PROJECT_URL_MARKER = "ninja:project-url"


def _patch_project_url(worker):
    """Route every new ChatGPT conversation into the account's project.

    Upstream opens a new chat by navigating to the CHATGPT_URL constant. Rewriting that
    one constant to honour CHATGPT_PROJECT_URL keeps every generation inside the project
    instead of the normal chat list. Guarded and idempotent: an upstream change simply
    skips the patch.
    """
    path = pathlib.Path(worker) / "src" / "chatgpt.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    if PROJECT_URL_MARKER in content:
        return False
    old = "const CHATGPT_URL = 'https://chatgpt.com';"
    if content.count(old) != 1:
        return False
    new = (
        "// ninja:project-url — new conversations open inside the configured project.\n"
        "const CHATGPT_URL = process.env.CHATGPT_PROJECT_URL || 'https://chatgpt.com';"
    )
    path.write_text(content.replace(old, new, 1), encoding="utf-8")
    return True


PAGES_PATCH_MARKER = "ninja:pages-routes"


# The first release wrote enterManualLogin() into a variable; upgrade those copies.
BROKEN_URL_ROUTE = (
    "    const p = await enterManualLogin({ acquire: false });\n"
    "    res.json({ url: p.url(), title: await p.title().catch(() => '') });"
)
FIXED_URL_ROUTE = (
    "    await enterManualLogin({ acquire: false });\n"
    "    const page = await getLoginPage();\n"
    "    res.json({ url: page.url(), title: await page.title().catch(() => '') });"
)


def _patch_login_diagnostics(worker):
    """Expose the login page URL and every open page of the browser context.

    Lets the operator confirm that generation chats land inside the configured project
    and read the project URL after opening it. Guarded: any upstream change leaves the
    worker untouched.
    """
    path = pathlib.Path(worker) / "src" / "routes" / "login.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    if PAGES_PATCH_MARKER in content:
        if BROKEN_URL_ROUTE in content:
            path.write_text(content.replace(BROKEN_URL_ROUTE, FIXED_URL_ROUTE, 1), encoding="utf-8")
            return True
        return False
    anchor = "module.exports = router;"
    if content.count(anchor) != 1:
        return False
    path.write_text(content.replace(anchor, DIAGNOSTICS_JS, 1), encoding="utf-8")
    return True


PLAN_PROBE_MARKER = "ninja:plan-probe-v2"

_RE_ROUTE_BLOCK = re.compile(r"// ninja:(plan-probe|settings-probe)[\s\S]*?\n\}\);\n")


def _strip_probe_routes(content, family):
    """Remove previously generated route blocks of one family only."""
    pattern = re.compile(r"// ninja:" + family + r"[\s\S]*?\n\}\);\n")
    return pattern.sub("", content)



SETTINGS_PROBE_MARKER = "ninja:settings-probe-v2"



LIVE_VIEW_MARKER = "ninja:live-view"



def _patch_live_view(worker):
    """Install the live-view routes once; both routes belong to the same block."""
    path = pathlib.Path(worker) / "src" / "routes" / "login.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    if LIVE_VIEW_MARKER in content:
        # v3 marker: cursors + quality presets. Older blocks get upgraded once.
        if "/login/cursor" in content and "LIVE_PRESETS" in content:
            return False
        start = content.index("// ninja:live-view")
        end = content.index("module.exports = router;", start)
        content = (
            content[:start]
            + "module.exports = router;"
            + content[end + len("module.exports = router;") :]
        )
    anchor = "module.exports = router;"
    if content.count(anchor) != 1:
        return False
    path.write_text(content.replace(anchor, LIVE_VIEW_JS, 1), encoding="utf-8")
    return True


def _install_probe(worker, js, marker, family):
    path = pathlib.Path(worker) / "src" / "routes" / "login.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    stripped = _strip_probe_routes(content, family)
    if marker in stripped and stripped == content:
        return False
    anchor = "module.exports = router;"
    if stripped.count(anchor) != 1:
        return False
    path.write_text(stripped.replace(anchor, js, 1), encoding="utf-8")
    return True


def _patch_plan_probe(worker):
    return _install_probe(worker, PLAN_PROBE_JS, PLAN_PROBE_MARKER, "plan-probe")


def _patch_settings_probe(worker):
    return _install_probe(worker, SETTINGS_PROBE_JS, SETTINGS_PROBE_MARKER, "settings-probe")


OCCLUSION_MARKER = "ninja:no-occlusion-throttle"


def _patch_launch_args(worker):
    """Disable Chrome's occlusion/renderer backgrounding for live streaming + timers."""
    path = pathlib.Path(worker) / "src" / "browser-fingerprint.js"
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    if OCCLUSION_MARKER in content:
        return False
    old = (
        "function launchArgs(engine = 'stealth') {\n"
        "  return engine === 'patchright'\n"
        "    ? COMMON_CHROMIUM_ARGS.filter((a) => !a.startsWith('--disable-blink-features='))\n"
        "    : [...COMMON_CHROMIUM_ARGS];\n"
        "}"
    )
    if content.count(old) != 1:
        return False
    new = (
        "// ninja:no-occlusion-throttle — keep painting and timers alive when the window\n"
        "// is covered or minimized, so the live screencast stays smooth and automation\n"
        "// timers do not get backgrounded.\n"
        "const NINJA_LIVE_ARGS = [\n"
        "  '--disable-backgrounding-occluded-windows',\n"
        "  '--disable-renderer-backgrounding',\n"
        "  '--disable-background-timer-throttling',\n"
        "  '--disable-features=CalculateNativeWinOcclusion',\n"
        "];\n"
        "\n"
        "function launchArgs(engine = 'stealth') {\n"
        "  const base = engine === 'patchright'\n"
        "    ? COMMON_CHROMIUM_ARGS.filter((a) => !a.startsWith('--disable-blink-features='))\n"
        "    : [...COMMON_CHROMIUM_ARGS];\n"
        "  return [\n"
        "    ...base.filter((arg) => !NINJA_LIVE_ARGS.includes(arg)),\n"
        "    ...NINJA_LIVE_ARGS,\n"
        "  ];\n"
        "}"
    )
    path.write_text(content.replace(old, new, 1), encoding="utf-8")
    return True


IMAGE_FAILURE_MARKER = "ninja:image-failure-detect"


def _patch_image_failure_detect(worker):
    """Treat ChatGPT's 'Image generation failed' card as a failure, not a success.

    The failed turn can still render a large <img>, so the plain image count in
    imageOutcomePredicate would report success and a stale/failed image could be
    delivered. Detection is textual, scoped to the newest assistant turn and
    guarded/idempotent; an upstream change simply skips the patch.
    """
    chatgpt = pathlib.Path(worker) / "src" / "chatgpt.js"
    images = pathlib.Path(worker) / "src" / "routes" / "images.js"
    if not chatgpt.is_file() or not images.is_file():
        return False
    content = chatgpt.read_text(encoding="utf-8")
    if IMAGE_FAILURE_MARKER in content:
        return False

    success_anchor = "  // --- Success -------------------------------------------------------------------------"
    failure_probe = (
        "  // ninja:image-failure-detect — provider-side failure must beat the image count: "
        "the failed card\n"
        "  // can contain a large <img>, and a stale image must never look like success.\n"
        "  if (tailChanged) {\n"
        "    const failedPhrases = ['image generation failed', 'generation failed', "
        "'не удалось создать изображение', 'не удалось сгенерировать изображение'];\n"
        "    if (failedPhrases.some((phrase) => isNew(phrase))) return 'generation_failed';\n"
        "  }\n\n"
    )
    if content.count(success_anchor) != 1:
        return False
    content = content.replace(success_anchor, failure_probe + success_anchor, 1)

    # readProgressIndicators: detect the same failure during adaptive waits.
    progress_anchor = "    const refusal = /i'm not able to|i am not able to|i can't|i cannot|unable to (generate|create|edit|help)|can't help|cannot help|i won't|i will not|не могу|не получится|не в состоянии/i;"
    if content.count(progress_anchor) != 1:
        return False
    content = content.replace(
        progress_anchor,
        progress_anchor
        + "\n    const generationFailed = /image generation failed|generation failed|"
        "не удалось (создать|сгенерировать) изображение/i;",
        1,
    )
    content = content.replace(
        "      refusalLikely: false,\n      activeGeneration: stopBtn || activeText,",
        "      refusalLikely: false,\n      generationFailed: isNewMatch(generationFailed),\n      activeGeneration: stopBtn || activeText,",
        1,
    )

    adaptive_anchor = "            if (progress.refusalLikely) {"
    if content.count(adaptive_anchor) != 1:
        return False
    content = content.replace(
        adaptive_anchor,
        "            if (progress.generationFailed) {\n"
        "              clearInterval(waitInterval);\n"
        "              await saveDebugSnapshot(p, 'generation_failed').catch(() => {});\n"
        "              throw buildError('image_generation_failed', "
        "'ChatGPT reported \"Image generation failed\" — no image was produced', "
        "await readLastAssistantMessage(p));\n"
        "            }\n"
        + adaptive_anchor,
        1,
    )

    # Result handling (non-success outcome map).
    content = content.replace(
        "      refused: 'ChatGPT refused to generate this image',",
        '''      refused: 'ChatGPT refused to generate this image',
      generation_failed:
        'ChatGPT reported "Image generation failed" — no usable image was produced. Open the chat and press Try again, or retry the job.',''',
        1,
    )
    content = content.replace(
        "    if (result_type === 'policy' || result_type === 'refused') {",
        "    if (result_type === 'policy' || result_type === 'refused' || result_type === 'generation_failed') {",
        1,
    )
    content = content.replace(
        "    const codeMap = { policy: 'policy_violation', refused: 'refused', limit: limitKind };",
        "    const codeMap = { policy: 'policy_violation', refused: 'refused', limit: limitKind, generation_failed: 'image_generation_failed' };",
        1,
    )
    content = content.replace(
        "      refused: tail || '',",
        "      refused: tail || '',\n      generation_failed: tail || '',",
        1,
    )

    images_content = images.read_text(encoding="utf-8")
    images_content = images_content.replace(
        "  const statusByKind = {\n    refused: 422,",
        "  const statusByKind = {\n    refused: 422,\n"
        "    // Definitive provider-side failure: the image was not produced. 422 means\n"
        "    // 'do not auto-retry' for shipped clients, which is what we want.\n"
        "    image_generation_failed: 422,",
        1,
    )
    chatgpt.write_text(content, encoding="utf-8")
    images.write_text(images_content, encoding="utf-8")
    return True


QUALITY_GATE_MARKER = "ninja:quality-gate"


def _patch_quality_gate(worker):
    """Let image turns continue an existing conversation and report its real id."""
    chatgpt_path = pathlib.Path(worker) / "src" / "chatgpt.js"
    route_path = pathlib.Path(worker) / "src" / "routes" / "images.js"
    if not chatgpt_path.is_file() or not route_path.is_file():
        return False
    content = chatgpt_path.read_text(encoding="utf-8")
    route = route_path.read_text(encoding="utf-8")
    if QUALITY_GATE_MARKER in content and QUALITY_GATE_MARKER in route:
        return False

    anchor = (
        "  let p = await ensureLoggedIn(slot);\n"
        "\n"
        "  if (!options.reuseChat) await ensureNewChat(p);\n"
        "  else await composerPreflight(p);"
    )
    if content.count(anchor) != 2:
        return False
    replacement = (
        "  let p = await ensureLoggedIn(slot);\n"
        "\n"
        "  // ninja:quality-gate — continue an existing conversation when asked.\n"
        "  if (options.conversationId) {\n"
        "    await openConversation(p, options.conversationId);\n"
        "    options.reuseChat = true;\n"
        "  }\n"
        "\n"
        "  if (!options.reuseChat) await ensureNewChat(p);\n"
        "  else await composerPreflight(p);"
    )
    content = content.replace(anchor, replacement)

    return_anchor = "  return {\n    ...result,\n    applied: {"
    if content.count(return_anchor) not in (1, 2):
        return False
    content = content.replace(
        return_anchor,
        "  return {\n    ...result,\n    // ninja:quality-gate — the chat this turn actually used.\n"
        "    conversationId: result.conversationId || conversationIdFromUrl(p.url()),\n"
        "    applied: {",
    )
    chatgpt_path.write_text(content, encoding="utf-8")

    route_anchor = "    let firstApplied = null;"
    if route.count(route_anchor) != 1:
        return False
    route = route.replace(
        route_anchor,
        "    let firstApplied = null;\n    let firstConversationId = conversationId;",
        1,
    )
    route = route.replace(
        "            reuseChat: i > 0,",
        "            reuseChat: i > 0,\n            conversationId: i === 0 ? conversationId : undefined,",
        1,
    )
    route = route.replace(
        "          if (i === 0 && result.applied) firstApplied = result.applied;",
        "          if (i === 0 && result.applied) firstApplied = result.applied;\n"
        "          if (i === 0 && result.conversationId) firstConversationId = result.conversationId;",
        1,
    )
    route = route.replace(
        "        conversation_id: conversationId,",
        "        conversation_id: firstConversationId || conversationId,",
        1,
    )
    route_path.write_text(route, encoding="utf-8")
    return True


QUALITY_GATE_V2_MARKER = "ninja:quality-gate-v2"

QUALITY_GATE_HELPERS = "// ninja:quality-gate-v2 — project-aware conversation URLs.\n// Project chats live at /g/<project>/c/<uuid>; opening /c/<uuid> returns 404.\nfunction normalizeConversationUrl(raw) {\n  try {\n    const url = new URL(String(raw || ''));\n    if (url.hostname !== 'chatgpt.com') return null;\n    if (!/^(\\/g\\/[^/]+)?\\/c\\/[0-9a-f-]{36}$/i.test(url.pathname)) return null;\n    return url.origin + url.pathname;\n  } catch (err) {\n    return null;\n  }\n}\n\nfunction conversationUrlFromPage(raw) {\n  return normalizeConversationUrl(raw);\n}\n\nasync function openConversationUrl(page, raw) {\n  const target = normalizeConversationUrl(raw);\n  if (!target) {\n    const err = new Error('Invalid conversation_url');\n    err.status = 400;\n    err.code = 'invalid_request';\n    throw err;\n  }\n  const response = await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 45000 });\n  if (!response || response.status() >= 400) {\n    const err = new Error('Conversation did not load (project-aware URL)');\n    err.status = 404;\n    err.code = 'conversation_not_found';\n    throw err;\n  }\n  return target;\n}\n\n"




def _patch_quality_gate_v2(worker):
    """Upgrade the v1 quality-gate patch to project-aware conversation URLs.

    v1 recorded only the UUID and reopened /c/<uuid>, which 404s for chats that live
    inside a ChatGPT project (/g/<project>/c/<uuid>). v2 carries the full page URL
    from generation through review and refinement. Every fragment is guarded, so this
    also upgrades already-patched worker copies.
    """
    chatgpt_path = pathlib.Path(worker) / "src" / "chatgpt.js"
    route_path = pathlib.Path(worker) / "src" / "routes" / "images.js"
    if not chatgpt_path.is_file() or not route_path.is_file():
        return False
    content = chatgpt_path.read_text(encoding="utf-8")
    route = route_path.read_text(encoding="utf-8")
    changed = False

    helper_anchor = "async function completeText(prompt, options = {}) {"
    if QUALITY_GATE_HELPERS not in content and content.count(helper_anchor) == 1:
        content = content.replace(helper_anchor, QUALITY_GATE_HELPERS + helper_anchor, 1)
        changed = True

    v1_block = (
        "  // ninja:quality-gate — continue an existing conversation when asked.\n"
        "  if (options.conversationId) {\n"
        "    await openConversation(p, options.conversationId);\n"
        "    options.reuseChat = true;\n"
        "  }\n"
    )
    v2_block = (
        "  // ninja:quality-gate-v2 — continue an existing conversation when asked (project-aware).\n"
        "  if (options.conversationUrl) {\n"
        "    await openConversationUrl(p, options.conversationUrl);\n"
        "    options.reuseChat = true;\n"
        "  } else if (options.conversationId) {\n"
        "    await openConversation(p, options.conversationId);\n"
        "    options.reuseChat = true;\n"
        "  }\n"
    )
    if v1_block in content:
        content = content.replace(v1_block, v2_block)
        changed = True

    return_line = (
        "    conversationId: result.conversationId || conversationIdFromUrl(p.url()),"
    )
    if return_line in content and "conversationUrl: result.conversationUrl" not in content:
        content = content.replace(
            return_line,
            return_line
            + "\n    conversationUrl: result.conversationUrl || conversationUrlFromPage(p.url()),",
        )
        changed = True

    text_open = "      if (options.conversationId) await openConversation(p, options.conversationId);"
    if text_open in content and "openConversationUrl(p, options.conversationUrl)" not in content:
        content = content.replace(
            text_open,
            "      if (options.conversationUrl) await openConversationUrl(p, options.conversationUrl);\n"
            "      else if (options.conversationId) await openConversation(p, options.conversationId);",
            1,
        )
        changed = True

    chat_conv = "  const conversationId = req.body.conversation_id ?? null;"
    if chat_conv in route and "req.body.conversation_url" not in route:
        route = route.replace(
            chat_conv,
            chat_conv
            + "\n  const conversationUrl = typeof req.body.conversation_url === 'string' ? req.body.conversation_url : null;",
            1,
        )
        changed = True
    if "      conversationUrl,\n" not in route:
        route = route.replace(
            "      conversationId,\n      job,",
            "      conversationId,\n      conversationUrl,\n      job,",
        )
        changed = True
    response_line = "      conversation_id: result.conversation_id || null,"
    if response_line in route and "conversation_url: result.conversation_url" not in route:
        route = route.replace(
            response_line,
            response_line
            + "\n      conversation_url: result.conversation_url || conversationUrl || null,",
            1,
        )
        changed = True

    gen_conv = "  const conversationId = req.body.conversation_id || null;"
    if gen_conv in route and "req.body.conversation_url" not in route:
        route = route.replace(
            gen_conv,
            gen_conv
            + "\n  const conversationUrl = typeof req.body.conversation_url === 'string' ? req.body.conversation_url : null;",
            1,
        )
        changed = True
    option_line = "            conversationId: i === 0 ? conversationId : undefined,"
    if option_line in route and "conversationUrl: i === 0" not in route:
        route = route.replace(
            option_line,
            option_line + "\n            conversationUrl: i === 0 ? conversationUrl : undefined,",
            1,
        )
        changed = True
    first_line = "    let firstConversationId = conversationId;"
    if first_line in route and "firstConversationUrl" not in route:
        route = route.replace(
            first_line, first_line + "\n    let firstConversationUrl = conversationUrl;", 1
        )
        changed = True
    capture = "          if (i === 0 && result.conversationId) firstConversationId = result.conversationId;"
    if capture in route and "result.conversationUrl" not in route:
        route = route.replace(
            capture,
            capture
            + "\n          if (i === 0 && result.conversationUrl) firstConversationUrl = result.conversationUrl;",
            1,
        )
        changed = True
    gen_response = "        conversation_id: firstConversationId || conversationId,"
    if gen_response in route and "conversation_url: firstConversationUrl" not in route:
        route = route.replace(
            gen_response,
            gen_response + "\n        conversation_url: firstConversationUrl || conversationUrl,",
            1,
        )
        changed = True

    if changed:
        chatgpt_path.write_text(content, encoding="utf-8")
        route_path.write_text(route, encoding="utf-8")
    return changed


def apply_worker_patches(worker):
    """Apply every patch to ``worker``; never raises, returns the applied names."""
    applied = []
    for name in PATCH_ORDER:
        function = globals()[name]
        try:
            if function(worker):
                applied.append(name)
        except Exception as exc:  # noqa: BLE001 - a patch must never break the worker
            print(f"[patches] {name} failed (ignored): {exc}", file=sys.stderr)
    return applied
