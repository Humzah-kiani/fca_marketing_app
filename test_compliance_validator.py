from threading import Barrier

import compliance_validator


def test_validate_posts_runs_independently_and_preserves_input_order(monkeypatch):
    barrier = Barrier(3)

    def fake_validate_post(post_text, format_type, category):
        assert format_type == "Post"
        assert category == "Retirement"
        barrier.wait(timeout=2)
        return {"post": post_text}

    monkeypatch.setattr(compliance_validator, "validate_post", fake_validate_post)

    results = compliance_validator.validate_posts(["first", "second", "third"], "Post", "Retirement")

    assert results == [{"post": "first"}, {"post": "second"}, {"post": "third"}]


def test_validate_posts_handles_empty_input():
    assert compliance_validator.validate_posts([], "Post", "Retirement") == []


def test_validate_post_uses_live_gemini_interactions(monkeypatch):
    calls = {}

    def fake_generate_interaction(model, system_instruction, user_input, max_output_tokens):
        calls["model"] = model
        calls["system_instruction"] = system_instruction
        calls["user_input"] = user_input
        calls["max_output_tokens"] = max_output_tokens
        return '{"passed": true, "issues": [], "summary": "Reviewed."}'

    monkeypatch.setattr(compliance_validator, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(compliance_validator, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(compliance_validator, "GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(compliance_validator, "get_sections_reference_text", lambda: "FCA reference")
    monkeypatch.setattr(compliance_validator, "generate_interaction", fake_generate_interaction)

    result = compliance_validator.validate_post("Draft content", "Post", "Retirement")

    assert result == {"passed": True, "issues": [], "summary": "Reviewed."}
    assert calls["model"] == "gemini-3.8-flash"
    assert "independent UK financial promotions compliance reviewer" in calls["system_instruction"]
    assert calls["user_input"] == "Return the independent compliance review for this draft."
    assert calls["max_output_tokens"] == 1000


def test_validate_post_fails_closed_when_gemini_is_unavailable(monkeypatch):
    monkeypatch.setattr(compliance_validator, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(compliance_validator, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(compliance_validator, "get_sections_reference_text", lambda: "FCA reference")
    monkeypatch.setattr(
        compliance_validator,
        "generate_interaction",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("503 UNAVAILABLE")),
    )

    result = compliance_validator.validate_post("Draft content", "Post", "Retirement")

    assert result["passed"] is False
    assert any("503 UNAVAILABLE" in issue for issue in result["issues"]), result
