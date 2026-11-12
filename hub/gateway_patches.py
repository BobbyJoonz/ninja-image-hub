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
