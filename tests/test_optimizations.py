"""Adapters that keep reference jobs fast and fail open when upstream changes."""
from pathlib import Path

from hub import workers

ANCHOR = """      typedPerKey = true;
      await typeLines(text,
        (line) => p.keyboard.type(line, { delay: TYPE_DELAY_MS }),
        () => p.keyboard.press('Shift+Enter'));
    } else {
      await textareaLocator.first().fill(text, { timeout: fillTimeoutMs(text) });
    }
"""


def _worker(tmp_path, body):
    worker = tmp_path / "worker"
    (worker / "src").mkdir(parents=True)
    (worker / "src" / "chatgpt.js").write_text(body, encoding="utf-8")
    return worker


def test_fast_prompt_insert_patch_is_idempotent(tmp_path):
    worker = _worker(tmp_path, ANCHOR)
    assert workers._patch_fast_prompt_insert(worker) is True
    content = (worker / "src" / "chatgpt.js").read_text(encoding="utf-8")
    assert "ninja:fast-prompt-insert" in content
    assert "keyboard.insertText(line)" in content
    assert "keyboard.type(line, { delay: TYPE_DELAY_MS })" not in content
    assert "[fast-insert]" in content
    # Second start must not stack a second patch.
    assert workers._patch_fast_prompt_insert(worker) is False
    assert content.count("ninja:fast-prompt-insert") == 1


def test_fast_prompt_insert_is_fail_open_when_upstream_changes(tmp_path):
    original = "async function typeAndSubmit() { /* upstream changed */ }\n"
    worker = _worker(tmp_path, original)
    assert workers._patch_fast_prompt_insert(worker) is False
    assert (worker / "src" / "chatgpt.js").read_text(encoding="utf-8") == original
    # No file at all must also be a no-op, not a crash.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert workers._patch_fast_prompt_insert(empty) is False
