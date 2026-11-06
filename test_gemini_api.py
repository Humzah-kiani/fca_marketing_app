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
    assert calls["generation_config"] == {
        "max_output_tokens": 384,
        "thinking_level": "minimal",
    }
    assert calls["api_version"] == "v1beta"
    assert calls["response_mime_type"] == "application/json"


def test_client_uses_v1beta_for_json_response_mode(monkeypatch):
    calls = {}

    class FakeGenAI:
        @staticmethod
        def Client(**kwargs):
            calls.update(kwargs)
            return object()

    monkeypatch.setattr(gemini_api, "genai", FakeGenAI)
    monkeypatch.setattr(gemini_api, "GEMINI_API_KEY", "test-key")
    gemini_api._client.cache_clear()
    try:
        gemini_api._client()
    finally:
        gemini_api._client.cache_clear()

    assert calls["api_key"] == "test-key"
    assert calls["http_options"].api_version == "v1beta"
    assert calls["http_options"].timeout == gemini_api.GEMINI_TIMEOUT_MS
