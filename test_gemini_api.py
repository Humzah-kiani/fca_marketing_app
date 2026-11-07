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
        "max_output_tokens": 896,
        "thinking_level": "low",
    }
    assert calls["api_version"] == "v1beta"
    assert calls["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
    }
    assert "response_mime_type" not in calls


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


def test_generate_interaction_passes_response_schema_when_requested(monkeypatch):
    calls = {}

    class FakeInteractions:
        def create(self, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(output_text='{"posts":["Generated post"]}')

    monkeypatch.setattr(
        gemini_api,
        "_client",
        lambda: SimpleNamespace(interactions=FakeInteractions()),
    )

    gemini_api.generate_interaction(
        "gemini-3.8-flash",
        "Return posts.",
        "Write one post.",
        max_output_tokens=384,
        response_schema=gemini_api.POSTS_SCHEMA,
    )

    assert calls["response_format"]["schema"] == gemini_api.POSTS_SCHEMA
