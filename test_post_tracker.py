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
