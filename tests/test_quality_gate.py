"""Unit tests for the post-generation quality gate."""

import pytest

from hub import quality_gate as quality


def test_strict_review_prompt_mentions_literals_and_json():
    prompt = quality.build_review_prompt("بنر با عدد ۱۵ و متن «خبر فوری»")
    assert "single wrong character fails" in prompt
    assert "numbers_score" in prompt and "verdict" in prompt
    assert "RTL" in prompt
    assert "۱۵" in prompt


def test_parse_review_normalises_and_caps_scores():
    review = quality.parse_review(
        'noise {"text_score": 6, "numbers_score": 3, "overall": 9, '
        '"issues": ["15 is 51"], "verdict": "pass"} tail'
    )
    assert review["text_score"] == 6
    assert review["numbers_score"] == 3
    # overall cannot exceed the weakest content score.
    assert review["overall"] == 3
    assert review["issues"] == ["15 is 51"]


def test_parse_review_rejects_non_json():
    with pytest.raises(ValueError):
        quality.parse_review("the image is fine, 10/10")


def test_needs_refinement_any_wrong_literal_forces_fix():
    review = quality.parse_review(
        '{"text_score": 6, "numbers_score": 3, "overall": 9, "verdict": "pass"}'
    )
    assert quality.needs_refinement(review, 8) is True
    clean = quality.parse_review(
        '{"text_score": 9, "numbers_score": 10, "overall": 9, "verdict": "pass"}'
    )
    assert quality.needs_refinement(clean, 8) is False


def test_correction_prompt_lists_issues_and_keeps_composition():
    prompt = quality.build_correction_prompt("poster", ["number 15 rendered as 51"])
    assert "SAME composition" in prompt
    assert "number 15 rendered as 51" in prompt
    assert "Change ONLY" in prompt


def test_improved_prefers_higher_overall_or_fewer_issues():
    old = {"overall": 6, "issues": ["a", "b"]}
    assert quality.improved(old, {"overall": 8, "issues": ["a"]}) is True
    assert quality.improved(old, {"overall": 6, "issues": ["a"]}) is True
    assert quality.improved(old, {"overall": 5, "issues": ["a", "b", "c"]}) is False


from helpers import auth


def test_generate_validates_quality_rounds(loaded):
    client, _ = loaded

    headers = auth(client)
    account = client.post(
        "/api/accounts", json={"name": "QL", "autostart": False}, headers=headers
    ).json()
    base = {
        "account_id": account["id"],
        "prompt": "a poster with the number 15",
    }
    assert client.post(
        "/api/generate", headers=headers, json=base | {"quality_rounds": 4}
    ).status_code == 422
    created = client.post(
        "/api/generate",
        headers=headers,
        json=base | {"quality_rounds": 2, "idempotency_key": "quality-loop-r1"},
    )
    assert created.status_code == 202
    job_id = created.json()["id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["quality_rounds"] == 2

    conflicting = client.post(
        "/api/generate",
        headers=headers,
        json=base | {"quality_rounds": 1, "idempotency_key": "quality-loop-r1"},
    )
    assert conflicting.status_code == 409


def test_quality_loop_refines_until_score_improves(monkeypatch):
    from types import SimpleNamespace

    from hub import execution

    prompts = iter(
        [
            '{"text_score": 5, "numbers_score": 2, "overall": 5, "verdict": "fix", '
            '"issues": ["15 rendered as 51"]}',
            '{"text_score": 10, "numbers_score": 10, "overall": 10, "verdict": "pass"}',
        ]
    )
    calls = {"refine": 0}

    def fake_chat(url, secret, payload, timeout=180.0):
        return SimpleNamespace(
            status_code=200,
            data={"choices": [{"message": {"content": next(prompts)}}]},
        )

    def fake_generation(url, secret, payload, n):
        calls["refine"] += 1
        return SimpleNamespace(
            status_code=200, data={"data": [{"url": "https://example.test/refined.png"}]}
        )

    monkeypatch.setattr(execution, "request_chat", fake_chat)
    monkeypatch.setattr(execution, "request_generation", fake_generation)

    ctx = SimpleNamespace(
        setting=lambda key: {"quality_gate": 0, "quality_threshold": 8}[key],
        upstream=lambda acc: "http://127.0.0.1:41000",
    )
    acc = {"api_secret": "x"}
    body, meta = execution._apply_quality_gate(
        ctx, acc, "a poster with the number 15", "conv-1", {"data": [{"url": "orig.png"}]},
        {"n": 1}, "http://127.0.0.1:41000/v1/images/generations", "extended", 1, rounds=2,
    )
    assert calls["refine"] == 1
    assert body["data"][0]["url"] == "https://example.test/refined.png"
    assert meta["refined"] is True
    assert meta["attempts"] == 1
    assert meta["score"] == 10
    assert meta["original_urls"] == ["orig.png"]


def test_quality_loop_without_rounds_uses_global_mode(monkeypatch):
    from types import SimpleNamespace

    from hub import execution

    def fake_chat(url, secret, payload, timeout=180.0):
        return SimpleNamespace(
            status_code=200,
            data={
                "choices": [
                    {
                        "message": {
                            "content": '{"text_score": 9, "numbers_score": 9, '
                            '"overall": 9, "verdict": "pass"}'
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(execution, "request_chat", fake_chat)
    ctx = SimpleNamespace(
        setting=lambda key: {"quality_gate": 1, "quality_threshold": 8}[key],
        upstream=lambda acc: "http://127.0.0.1:41000",
    )
    body, meta = execution._apply_quality_gate(
        ctx, {"api_secret": "x"}, "p", "conv-1", {"data": [{"url": "orig.png"}]},
        {"n": 1}, "http://127.0.0.1:41000/v1/images/generations", "extended", 1, rounds=0,
    )
    assert body["data"][0]["url"] == "orig.png"
    assert meta["refines_allowed"] == 0
    assert meta["score"] == 9
