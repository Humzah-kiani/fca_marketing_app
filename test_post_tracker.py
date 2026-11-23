from types import SimpleNamespace

import post_tracker
from post_tracker import carousel_slides


def test_carousel_slides_preserves_multiline_slide_content():
    assert carousel_slides(
        "Slide 1: Plan ahead\nUse your goals.\n"
        "Slide 2: Understand the risks.\n\n"
        "Slide 3: Speak to an adviser."
    ) == [
        "Plan ahead\nUse your goals.",
        "Understand the risks.",
        "Speak to an adviser.",
    ]


def test_carousel_slides_handles_unlabelled_text():
    assert carousel_slides("One single slide") == ["One single slide"]


def test_carousel_slides_splits_inline_slide_labels():
    assert carousel_slides(
        "Slide 1: Plan ahead. Slide 2: Understand the risks. "
        "Slide 3: Speak to an adviser."
    ) == [
        "Plan ahead.",
        "Understand the risks.",
        "Speak to an adviser.",
    ]


def test_open_in_design_loads_selected_record_and_navigates_to_design_post():
    state = {}
    st = SimpleNamespace(session_state=state)

    post_tracker._open_in_design(
        st,
        {
            "post_id": 8,
            "post_text": "Selected copy",
            "format_type": "Post",
            "category": "Protection",
        },
    )

    assert state["generated_post_text"] == "Selected copy"
    assert state["generated_post_format"] == "Post"
    assert state["generated_post_category"] == "Protection"
    assert state["tracker_open_post_id"] == 8
    assert state["app_section"] == "Design Post"


def test_edit_action_selects_record_and_opens_its_editor():
    state = {}
    st = SimpleNamespace(session_state=state)

    post_tracker._edit_in_tracker(
        st,
        {"post_id": 9, "format_type": "Carousel"},
    )

    assert state["app_section"] == "Post Tracker"
    assert state["tracker_selected_post_id"] == 9
    assert state["tracker_mode_9"] == "Edit slides"


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
