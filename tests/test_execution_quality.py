"""Quality-loop branches in execution.py, hermetic (no gateway, no provider)."""
from types import SimpleNamespace

from hub import execution


class _Ctx:
    def __init__(self, mode=2, rounds=1):
        self.mode = mode
        self.rounds = rounds

    def setting(self, key):
        if key == "quality_gate":
            return self.mode
        if key == "quality_threshold":
            return 8
        return 0

    def upstream(self, acc):
        return "http://127.0.0.1:41000"

    def quality_prompt_template(self):
        return "master {request}"


def _acc():
    return {"id": "a", "api_secret": "secret"}


def _body():
    return {"data": [{"url": "http://img/1"}]}


def test_quality_gate_disabled_does_not_refine(monkeypatch):
    calls = []
    monkeypatch.setattr(execution, "_review_chat", lambda *a, **k: "review")
    monkeypatch.setattr(execution.quality, "parse_review", lambda text: {"overall": 9, "issues": [], "verdict": "keep"})
    monkeypatch.setattr(execution.quality, "needs_refinement", lambda review, threshold: calls.append(1) or True)
    body, meta = execution._apply_quality_gate(
        _Ctx(mode=0), _acc(), "p", "conv", "url", _body(), {"prompt": "p"}, "http://gen", "extended", 1
    )
    assert body == _body()
    assert meta["refines_allowed"] == 0
    assert meta["score"] == 9
    assert calls == []  # no refinement loop entered


def test_quality_gate_refines_and_keeps_the_better_image(monkeypatch):
    reviews = iter(
        [
            {"overall": 6, "issues": ["text"], "verdict": "fix", "text_score": 5, "numbers_score": 7},
            {"overall": 9, "issues": [], "verdict": "ok", "text_score": 9, "numbers_score": 9},
        ]
    )
    monkeypatch.setattr(execution, "_review_chat", lambda *a, **k: "review")
    monkeypatch.setattr(execution.quality, "parse_review", lambda text: next(reviews))
    monkeypatch.setattr(execution.quality, "needs_refinement", lambda review, threshold: review["overall"] < threshold)
    monkeypatch.setattr(execution.quality, "build_correction_prompt", lambda prompt, issues: "fix text")
    monkeypatch.setattr(execution.quality, "improved", lambda current, refined: True)
    refined_data = {"data": [{"url": "http://img/2"}]}
    monkeypatch.setattr(
        execution,
        "request_generation",
        lambda *a, **k: SimpleNamespace(status_code=200, data=refined_data),
    )
    body, meta = execution._apply_quality_gate(
        _Ctx(mode=2), _acc(), "p", "conv", "url", _body(), {"prompt": "p"}, "http://gen", "extended", 1
    )
    assert body == refined_data
    assert meta["refined"] is True
    assert meta["attempts"] == 1
    assert meta["score"] == 9


def test_quality_gate_survives_refinement_http_failure(monkeypatch):
    monkeypatch.setattr(execution, "_review_chat", lambda *a, **k: "review")
    monkeypatch.setattr(
        execution.quality,
        "parse_review",
        lambda text: {"overall": 5, "issues": ["x"], "verdict": "fix", "text_score": 5, "numbers_score": 5},
    )
    monkeypatch.setattr(execution.quality, "needs_refinement", lambda review, threshold: True)
    monkeypatch.setattr(execution.quality, "build_correction_prompt", lambda *a: "fix")
    monkeypatch.setattr(
        execution,
        "request_generation",
        lambda *a, **k: SimpleNamespace(status_code=500, data=None),
    )
    body, meta = execution._apply_quality_gate(
        _Ctx(mode=2), _acc(), "p", "conv", None, _body(), {"prompt": "p"}, "http://gen", "extended", 1
    )
    assert body == _body()  # best image so far is kept
    assert "HTTP 500" in meta["error"]


def test_quality_gate_survives_review_exception(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("review down")

    monkeypatch.setattr(execution, "_review_chat", boom)
    body, meta = execution._apply_quality_gate(
        _Ctx(mode=2), _acc(), "p", "conv", None, _body(), {"prompt": "p"}, "http://gen", "extended", 1
    )
    assert body == _body()
    assert "review down" in meta["error"]


def test_history_entry_handles_missing_review():
    assert execution._history_entry(0, None) == {"attempt": 0, "error": "review unavailable"}
    entry = execution._history_entry(1, {"overall": 7, "text_score": 6, "numbers_score": 8, "verdict": "ok", "issues": [1, 2]})
    assert entry["attempt"] == 1 and entry["overall"] == 7 and entry["issues"] == [1, 2]
