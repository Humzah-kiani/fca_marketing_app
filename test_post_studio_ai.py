import post_studio as ps
from post_studio_ai import (
    Guidelines,
    _app_copy_payload,
    _sync_fca_generated_content,
    slide_copy,
)


def test_app_post_copy_is_preserved_in_design_payload():
    text = "Plan ahead for retirement\n\nReview your options.\nPension values can fall.\nSpeak to an adviser."

    payload = _app_copy_payload(text, "Post", max_headline_words=8)

    assert payload["source"] == "fca_app"
    assert payload["slides"] == [
        {
            "badge": "",
            "headline": "Plan ahead for retirement",
            "body": "Review your options.\nPension values can fall.\nSpeak to an adviser.",
            "cta": "",
        }
    ]


def test_fca_carousel_is_loaded_into_design_studio(monkeypatch):
    session_state = {
        "generated_post_text": "Slide 1: Review your pension goals\nSlide 2: Values can fall as well as rise.",
        "generated_post_format": "Carousel",
        "generated_post_category": "Pension",
        "guide": Guidelines(),
    }

    monkeypatch.setattr(ps, "_commit", lambda _st: None)
    monkeypatch.setattr(ps, "_bump", lambda _st: None)
    _sync_fca_generated_content(type("FakeStreamlit", (), {"session_state": session_state})())

    carousel = session_state["carousel"]
    assert session_state["guide"].kind == "Carousel"
    assert session_state["guide"].topic == "Pension"
    assert len(carousel.slides) == 2
    assert slide_copy(carousel.slides[0])["headline"] == "Review your pension goals"
    assert slide_copy(carousel.slides[1])["headline"] == "Values can fall as well as rise."


def test_long_app_post_is_not_truncated_when_loaded(monkeypatch):
    body = " ".join(f"word{index}" for index in range(55))
    session_state = {
        "generated_post_text": f"Plan ahead\n\n{body}",
        "generated_post_format": "Post",
        "generated_post_category": "Retirement",
        "guide": Guidelines(),
    }

    monkeypatch.setattr(ps, "_commit", lambda _st: None)
    monkeypatch.setattr(ps, "_bump", lambda _st: None)
    _sync_fca_generated_content(type("FakeStreamlit", (), {"session_state": session_state})())

    designed_copy = slide_copy(session_state["carousel"].slides[0])
    assert designed_copy["body"] == body
