import importlib

import pytest

import generator
import post_studio
from config import _normalise_gemini_model
from generator import (
    _build_system_prompt,
    _build_user_prompt,
    _dedupe_posts,
    _generate_with_gemini,
)
from post_studio import build_post_canvas


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


def test_generate_with_gemini_tries_fallback_model_on_404_or_503(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key

        class models:
            @staticmethod
            def generate_content(model, *args, **kwargs):
                calls.append(model)
                if model == "gemini-3.8-flash":
                    raise RuntimeError("404 NOT_FOUND: This model models/gemini-2.5-flash is no longer available to new users.")
                return type("Response", (), {"text": '["Fallback post"]'})()

    monkeypatch.setattr(generator, "GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(generator, "GEMINI_FALLBACK_MODELS", ["gemini-3.8-flash-lite"])
    monkeypatch.setattr(generator, "genai", type("FakeGenAI", (), {"Client": FakeClient}))
    monkeypatch.setattr(generator, "types", type("FakeTypes", (), {"GenerateContentConfig": lambda **kwargs: kwargs}))
    monkeypatch.setattr(generator, "get_sections_reference_text", lambda: "Reference text")

    result = _generate_with_gemini("Post", "Retirement", "Campaign", 1)

    assert result == ["Fallback post"]
    assert calls == ["gemini-3.8-flash", "gemini-3.8-flash-lite"]


def test_normalise_gemini_model_preserves_explicit_model_selection():
    assert _normalise_gemini_model("gemini-3.8-flash") == "gemini-3.8-flash"
    assert _normalise_gemini_model("models/gemini-3.8-flash") == "gemini-3.8-flash"
    assert _normalise_gemini_model("gemini-3.6-flash") == "gemini-3.8-flash"


def test_build_system_prompt_uses_premium_social_style():
    prompt = _build_system_prompt(
        format_type="Post",
        category="Retirement",
        category_note="Risk statement",
        reference_text="FCA guidance placeholder",
        guideline="Campaign launch",
        num_posts=3,
    )

    assert "premium" in prompt.lower()
    assert "strong hook" in prompt.lower()
    assert "modern financial adviser social brand" in prompt.lower()


def test_post_generation_module_reexports_post_studio_api():
    assert post_studio.build_post_canvas is build_post_canvas
    assert post_studio.post_studio_ui is not None


def test_legacy_post_generation_module_reexports_post_studio_api():
    legacy = importlib.import_module("post_generation")
    assert legacy.build_post_canvas is build_post_canvas
    assert legacy.post_studio_ui is post_studio.post_studio_ui


def test_build_post_canvas_generates_square_design():
    image = build_post_canvas(
        headline="Retirement planning starts with clarity",
        body="A structured approach can help you think through the options and build a realistic plan.",
        category="Retirement",
        accent="gold",
    )

    assert image.size == (1080, 1080)
    assert image.mode == "RGB"
