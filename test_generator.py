import pytest

import generator
from generator import _build_system_prompt, _build_user_prompt, _dedupe_posts


def test_generate_posts_requires_live_ai_when_gemini_is_selected(monkeypatch):
    monkeypatch.setattr(generator, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(generator, "GEMINI_API_KEY", "")

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        generator.generate_posts("Post", "Retirement", "Campaign", 2)


def test_build_system_prompt_requires_category_specific_output():
    prompt = _build_system_prompt(
        format_type="Post",
        category="Investment",
        category_note="Capital at risk.",
        reference_text="FCA guidance placeholder",
        guideline="Product launch",
        num_posts=3,
    )

    assert "Investment" in prompt
    assert "distinct" in prompt.lower()
    assert "same hook" in prompt.lower()
    assert "category-specific" in prompt.lower()


def test_build_user_prompt_uses_fresh_angle_every_time():
    prompt_1 = _build_user_prompt("Post", "Investment", 3, variation_token="abc123", angle_seed=1)
    prompt_2 = _build_user_prompt("Post", "Investment", 3, variation_token="xyz999", angle_seed=2)

    assert "Investment" in prompt_1
    assert "abc123" in prompt_1
    assert "xyz999" in prompt_2
    assert prompt_1 != prompt_2


def test_dedupe_posts_removes_duplicates():
    posts = [
        "Investment can be a long-term option.",
        "Investment can be a long-term option.",
        "Understanding your risk profile matters.",
    ]

    assert _dedupe_posts(posts) == [
        "Investment can be a long-term option.",
        "Understanding your risk profile matters.",
    ]
