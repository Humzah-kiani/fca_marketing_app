import importlib

import pytest

import generator
import post_studio
from config import _normalise_gemini_model
from generator import (
    _build_system_prompt,
    _build_user_prompt,
    _dedupe_posts,
    _gemini_model_candidates,
    _generate_with_gemini,
    _is_transient_gemini_error,
    _retry_unique_posts,
)
from post_studio import build_post_canvas, new_post


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
    assert "category: \"investment\"" in prompt.lower()
    assert "specific to investment" in prompt.lower()
    assert "different hook" in prompt.lower()


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

    def fake_gemini_text(model, system_prompt, user_prompt, max_tokens):
        calls.append(model)
        if model == "gemini-3.8-flash":
            raise RuntimeError("503 UNAVAILABLE")
        return '["Fallback post"]'

    monkeypatch.setattr(generator, "GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(generator, "GEMINI_FALLBACK_MODELS", ["fallback-test-model"])
    monkeypatch.setattr(generator, "_gemini_text", fake_gemini_text)
    monkeypatch.setattr(generator, "_reference_text", lambda: "Reference text")

    result = generator._gemini_call("system", "user", 100)

    assert result == '["Fallback post"]'
    assert calls == ["gemini-3.8-flash", "fallback-test-model"]


def test_generate_with_gemini_tries_fallback_model_on_internal_error(monkeypatch):
    calls = []

    def fake_gemini_text(model, system_prompt, user_prompt, max_tokens):
        calls.append(model)
        if model == "gemini-3.8-flash":
            raise RuntimeError(
                "500 INTERNAL: {'error': {'message': 'Internal error encountered.'}}"
            )
        return '["Fallback post"]'

    monkeypatch.setattr(generator, "GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(generator, "GEMINI_FALLBACK_MODELS", ["fallback-test-model"])
    monkeypatch.setattr(generator, "_gemini_text", fake_gemini_text)

    result = generator._gemini_call("system", "user", 100)

    assert result == '["Fallback post"]'
    assert calls == ["gemini-3.8-flash", "fallback-test-model"]


def test_generate_with_gemini_uses_one_request_for_default_post_count(monkeypatch):
    calls = []

    def fake_gemini_call(system_prompt, user_prompt, max_tokens):
        calls.append((system_prompt, user_prompt, max_tokens))
        return '["First post", "Second post", "Third post"]'

    monkeypatch.setattr(generator, "_gemini_call", fake_gemini_call)
    monkeypatch.setattr(generator, "_reference_text", lambda: "Reference text")

    result = _generate_with_gemini("Post", "Retirement", "", 3)

    assert result == ["First post", "Second post", "Third post"]
    assert len(calls) == 1
    assert calls[0][2] == generator._max_tokens_for("Post", 3)


def test_gemini_deadline_error_is_transient():
    assert _is_transient_gemini_error(RuntimeError("504 DEADLINE_EXCEEDED"))


def test_gemini_transient_failure_surfaces_api_error_instead_of_local_copy(monkeypatch):
    monkeypatch.setattr(generator, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(generator, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(generator, "_generate_with_gemini", lambda *args: (_ for _ in ()).throw(
        RuntimeError("504 DEADLINE_EXCEEDED")
    ))

    with pytest.raises(RuntimeError, match="504 DEADLINE_EXCEEDED"):
        generator.generate_posts("Post", "Retirement", "", 1)


def test_gemini_does_not_fill_in_missing_posts_with_local_copy(monkeypatch):
    monkeypatch.setattr(generator, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(generator, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        generator,
        "_generate_with_gemini",
        lambda *args: [
            "Planning starts here\n\nKnow your options.\n\n"
            "Values can fall as well as rise.\n\nSpeak to a regulated adviser."
        ],
    )

    with pytest.raises(RuntimeError, match="Gemini generation failed: AI returned 1 of 2 requested posts"):
        generator.generate_posts("Post", "Retirement", "", 2)


def test_gemini_model_candidates_use_configured_models_without_discovery(monkeypatch):
    monkeypatch.setattr(generator, "GEMINI_MODEL", "primary")
    monkeypatch.setattr(generator, "GEMINI_FALLBACK_MODELS", ["fallback", "primary"])

    assert _gemini_model_candidates() == ["primary", "fallback"]


def test_generation_uses_one_request_and_fills_missing_posts_locally():
    requested_counts = []

    def generate(_format_type, _category, _guideline, count):
        requested_counts.append(count)
        return [
            "Retirement choices start with your goals.\n\n"
            "Compare your options.\nWeigh the risks and benefits.\n\n"
            "Values can fall as well as rise.\n\n"
            "Speak to a regulated adviser."
        ]

    posts = _retry_unique_posts(generate, "Post", "Retirement", "", 3)

    assert requested_counts == [3]
    assert len(posts) == 3
    assert posts[0].startswith("Retirement choices start with your goals.")
    assert len(set(posts)) == 3


def test_invalid_gemini_api_key_is_not_transient():
    error = RuntimeError("400 INVALID_ARGUMENT: API_KEY_INVALID: API key not valid")

    assert not _is_transient_gemini_error(error)


def test_normalise_gemini_model_preserves_explicit_model_selection():
    assert _normalise_gemini_model("gemini-3.8-flash") == "gemini-3.8-flash"
    assert _normalise_gemini_model("models/gemini-3.8-flash") == "gemini-3.8-flash"
    assert _normalise_gemini_model("gemini-3.6-flash") == "gemini-3.8-flash"
    assert _normalise_gemini_model("gemini-1.5-flash") == "gemini-3.8-flash"
    assert _normalise_gemini_model("gemini-2.0-flash") == "gemini-3.8-flash"
    assert _normalise_gemini_model("gemini-3.8-flash-lite") == "gemini-3.8-flash"


def test_gemini_fallbacks_exclude_retired_models():
    assert "gemini-1.5-flash" not in generator.GEMINI_FALLBACK_MODELS
    assert "gemini-2.0-flash" not in generator.GEMINI_FALLBACK_MODELS
    assert "gemini-3.8-flash-lite" not in generator.GEMINI_FALLBACK_MODELS


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


def test_generated_copy_replaces_placeholder_text_in_post_design():
    spec = new_post("Placeholder headline", "Placeholder body")
    generated_text = "A clear plan can help you consider your options."

    body = post_studio._apply_generated_copy(spec, generated_text)

    assert body.text == generated_text
    assert body.y == 0.22
    assert next(layer for layer in spec.text_layers if layer.label == "Headline").text == ""
