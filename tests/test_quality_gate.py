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
