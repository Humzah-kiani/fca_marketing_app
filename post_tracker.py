"""Browse and edit generated post records."""
import re

from db import execute, fetch_all


def _carousel_slides(post_text: str) -> list[str]:
    slides = []
    current = None
    for line in (post_text or "").splitlines():
        match = re.match(r"\s*Slide\s+\d+\s*:\s*(.*)", line, re.IGNORECASE)
        if match:
            if current is not None:
                slides.append(current.strip())
            current = match.group(1).strip()
        elif current is not None and line.strip():
            current = f"{current}\n{line.strip()}".strip()
    if current is not None:
        slides.append(current.strip())
    return slides or ([post_text.strip()] if post_text.strip() else [])


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


def render_post_tracker(st) -> None:
    """Render the saved-content tracker and load selected content into Post Studio."""
    st.subheader("Post tracker")
    if st.session_state.pop("tracker_open_notice", False):
        st.success(
            "Record loaded into Post Studio. Select the Design Post tab to preview "
            "and edit the design; carousel slides appear in its slide strip."
        )
    st.caption(
        "Browse generated records by serial number, batch, category, and creation date. "
        "Edits are saved to the original record and should be reviewed by compliance."
    )
    records = fetch_all(
        """SELECT gp.id AS post_id, gb.id AS batch_id, gb.created_at,
                  gb.format_type, gb.category, gb.carousel_slide_count,
                  gp.post_index, gp.post_text, gp.status
           FROM generation_batches gb
           JOIN generated_posts gp ON gp.batch_id = gb.id
           ORDER BY gb.created_at DESC, gb.id DESC, gp.post_index ASC"""
    )
    if not records:
        st.info("No generated post records yet.")
        return

    st.dataframe(
        [
            {
                "Serial No.": record["post_id"],
                "Batch ID": record["batch_id"],
                "Category": record["category"],
                "Format": record["format_type"],
                "Created": str(record["created_at"]),
                "Status": record["status"],
            }
            for record in records
        ],
        hide_index=True,
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
        slides = _carousel_slides(record["post_text"])
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
                _save_record(st, record, edited_text)
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
                _save_record(st, record, edited_text)

    if st.button("Open in Design Post", key=f"tracker_open_{selected_id}"):
        _open_in_design(st, record)
        st.session_state["tracker_open_notice"] = True
        st.rerun()


def _save_record(st, record: dict, edited_text: str) -> None:
    edited_text = edited_text.strip()
    if not edited_text:
        st.error("Post text cannot be empty.")
        return
    if edited_text == record["post_text"]:
        st.info("No changes to save.")
        return
    execute(
        """UPDATE generated_posts
           SET post_text = %s,
               status = CASE WHEN status = 'active' THEN 'flagged_for_review' ELSE status END
           WHERE id = %s""",
        (edited_text, record["post_id"]),
    )
    st.success("Edits saved. The record is marked for compliance review.")
    st.rerun()
