from types import SimpleNamespace

import post_tracker
from post_tracker import _carousel_slides


def test_carousel_slides_preserves_multiline_slide_content():
    assert _carousel_slides(
        "Slide 1: Plan ahead\nUse your goals.\n"
        "Slide 2: Understand the risks.\n\n"
        "Slide 3: Speak to an adviser."
    ) == [
        "Plan ahead\nUse your goals.",
        "Understand the risks.",
        "Speak to an adviser.",
    ]


def test_carousel_slides_handles_unlabelled_text():
    assert _carousel_slides("One single slide") == ["One single slide"]


def test_save_record_scopes_edits_to_the_advisers_posts(monkeypatch):
    executed = {}
    monkeypatch.setattr(
        post_tracker,
        "execute",
        lambda query, params, returning: (
            executed.update(query=query, params=params, returning=returning) or {"id": 8}
        ),
    )
    st = SimpleNamespace(success=lambda message: None, rerun=lambda: None, error=lambda message: None)

    post_tracker._save_record(
        st,
        {"post_id": 8, "post_text": "Original copy"},
        advisor_id=14,
        edited_text="Updated copy",
    )

    assert "gb.advisor_id = %s" in executed["query"]
    assert executed["params"] == ("Updated copy", 8, 14)
    assert executed["returning"]
