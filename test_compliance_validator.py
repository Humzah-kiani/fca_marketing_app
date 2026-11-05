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
