from types import SimpleNamespace

import gemini_api


def test_generate_interaction_uses_interactions_api_and_requested_model(monkeypatch):
    calls = {}

    class FakeInteractions:
        def create(self, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(output_text='["Generated post"]')

    monkeypatch.setattr(
        gemini_api,
        "_client",
        lambda: SimpleNamespace(interactions=FakeInteractions()),
    )

    result = gemini_api.generate_interaction(
        "gemini-3.8-flash",
        "Return a JSON array.",
        "Write one post.",
        max_output_tokens=384,
    )

    assert result == '["Generated post"]'
    assert calls["model"] == "gemini-3.8-flash"
    assert calls["system_instruction"] == "Return a JSON array."
    assert calls["input"] == "Write one post."
    assert calls["generation_config"].max_output_tokens == 384
    assert calls["generation_config"].response_mime_type == "application/json"
