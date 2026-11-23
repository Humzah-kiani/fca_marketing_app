"""Browse and edit generated post records."""
import re

from db import execute, fetch_all


def carousel_slides(post_text: str) -> list[str]:
    text = (post_text or "").strip()
    markers = list(re.finditer(r"(?<!\w)Slide\s+\d+\s*:\s*", text, re.IGNORECASE))
    if not markers:
        return [text] if text else []
    slides = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else None
        slide = text[marker.end():end].strip()
        if slide:
            slides.append(slide)
    return slides or [text]


def _record_label(record: dict) -> str:
    created = record["created_at"]
    return (
        f"#{record['post_id']} · Batch {record['batch_id']} · "
        f"{record['category']} · {record['format_type']} · {created}"
    )


def _open_in_design(st, record: dict) -> None:
    st.session_state["generated_post_text"] = record["post_text"]
    st.session_state["generated_post_format"] = record["format_type"]
    st.session_state["generated_post_category"] = record["category"]
    st.session_state["tracker_open_post_id"] = record["post_id"]
    st.session_state["app_section"] = "Design Post"
    st.session_state["design_post_notice"] = True


def _edit_in_tracker(st, record: dict) -> None:
    st.session_state["app_section"] = "Post Tracker"
    st.session_state["tracker_selected_post_id"] = record["post_id"]
    mode = "Edit slides" if str(record["format_type"]).lower() == "carousel" else "Edit text"
    st.session_state[f"tracker_mode_{record['post_id']}"] = mode


def render_post_tracker(st, advisor_id: int) -> None:
    """Render saved content with actions for viewing or editing each record."""
    st.subheader("Post tracker")
    st.caption(
        "Browse generated records by serial number, batch, category, and creation date. "
        "Edits are saved to the original record and should be reviewed by compliance."
    )
    records = fetch_all(
        """SELECT gp.id AS post_id, gb.id AS batch_id, gb.advisor_id, gb.created_at,
                  gb.format_type, gb.category, gb.carousel_slide_count,
                  gp.post_index, gp.post_text, gp.status
           FROM generation_batches gb
           JOIN generated_posts gp ON gp.batch_id = gb.id
           WHERE gb.advisor_id = %s
           ORDER BY gb.created_at DESC, gb.id DESC, gp.post_index ASC""",
        (advisor_id,),
    )
    if not records:
        st.info("No generated post records yet.")
        return

    headers = st.columns([0.8, 0.8, 1.2, 0.8, 1.5, 1.0, 0.7, 0.7])
    for column, label in zip(
        headers,
        ["Serial No.", "Batch ID", "Category", "Format", "Created", "Status", "View", "Edit"],
    ):
        column.markdown(f"**{label}**")
    for record in records:
        columns = st.columns([0.8, 0.8, 1.2, 0.8, 1.5, 1.0, 0.7, 0.7])
        columns[0].write(record["post_id"])
        columns[1].write(record["batch_id"])
        columns[2].write(record["category"])
        columns[3].write(record["format_type"])
        columns[4].write(str(record["created_at"]))
        columns[5].write(record["status"])
        columns[6].button(
            "View",
            key=f"tracker_view_{record['post_id']}",
            on_click=_open_in_design,
            args=(st, record),
            use_container_width=True,
        )
        columns[7].button(
            "Edit",
            key=f"tracker_edit_action_{record['post_id']}",
            on_click=_edit_in_tracker,
            args=(st, record),
            use_container_width=True,
        )

    by_id = {record["post_id"]: record for record in records}
    selected_id = st.selectbox(
        "Select a record",
        options=list(by_id),
        format_func=lambda post_id: _record_label(by_id[post_id]),
        key="tracker_selected_post_id",
    )
    record = by_id[selected_id]
    st.markdown(
        f"**Serial No.:** {record['post_id']} · **Batch ID:** {record['batch_id']} · "
        f"**Category:** {record['category']} · **Format:** {record['format_type']}"
    )
    st.caption(f"Created: {record['created_at']} · Status: {record['status']}")

    if str(record["format_type"]).lower() == "carousel":
        slides = carousel_slides(record["post_text"])
        st.markdown("#### Carousel preview")
        for index, slide in enumerate(slides, start=1):
            with st.container(border=True):
                st.markdown(f"**Slide {index} of {len(slides)}**")
                st.write(slide)
        editor_mode = st.radio(
            "Carousel action",
            ["View", "Edit slides"],
            horizontal=True,
            key=f"tracker_mode_{selected_id}",
        )
        if editor_mode == "Edit slides":
            with st.form(f"tracker_edit_{selected_id}"):
                edited_slides = [
                    st.text_area(
                        f"Slide {index}",
                        value=slide,
                        key=f"tracker_slide_{selected_id}_{index}",
                        height=110,
                    )
                    for index, slide in enumerate(slides, start=1)
                ]
                save = st.form_submit_button("Save edits")
            if save:
                edited_text = "\n".join(
                    f"Slide {index}: {text.strip()}"
                    for index, text in enumerate(edited_slides, start=1)
                )
                _save_record(st, record, advisor_id, edited_text)
    else:
        editor_mode = st.radio(
            "Record action",
            ["View", "Edit text"],
            horizontal=True,
            key=f"tracker_mode_{selected_id}",
        )
        if editor_mode == "View":
            st.markdown("#### Generated text")
            st.write(record["post_text"])
        else:
            with st.form(f"tracker_edit_{selected_id}"):
                edited_text = st.text_area(
                    "Edit generated text",
                    value=record["post_text"],
                    height=220,
                    key=f"tracker_text_{selected_id}",
                )
                save = st.form_submit_button("Save edits")
            if save:
                _save_record(st, record, advisor_id, edited_text)


def _save_record(st, record: dict, advisor_id: int, edited_text: str) -> None:
    edited_text = edited_text.strip()
    if not edited_text:
        st.error("Post text cannot be empty.")
        return
    if edited_text == record["post_text"]:
        st.info("No changes to save.")
        return
    saved = execute(
        """UPDATE generated_posts gp
           SET post_text = %s,
               status = CASE WHEN status = 'active' THEN 'flagged_for_review' ELSE status END
           FROM generation_batches gb
           WHERE gp.id = %s AND gp.batch_id = gb.id AND gb.advisor_id = %s
           RETURNING gp.id""",
        (edited_text, record["post_id"], advisor_id),
        returning=True,
    )
    if not saved:
        st.error("Could not save this post: it is unavailable or does not belong to your account.")
        return
    st.success("Edits saved. The record is marked for compliance review.")
    st.rerun()
