import json

import streamlit as st

from db import init_db, fetch_all, execute
from compliance_validator import validate_posts
from fca_reference import refresh_fca_references
from fca_monitor import (
    FCA_SECTIONS,
    ensure_sections_seeded,
    get_current_snapshot,
    check_for_updates,
)
from generator import generate_posts

st.set_page_config(page_title="FCA Compliant Marketing Generator", layout="wide")


def _coerce_json_list(value):
    """Return a list for JSONB/string values stored in Postgres history rows."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


@st.cache_resource
def _startup():
    init_db()
    ensure_sections_seeded()
    return True



try:
    _startup()
except RuntimeError as exc:
    st.error(str(exc))
    st.stop()

st.title("FCA-Compliant Marketing Text Generator")
st.caption(
    "Generates UK financial-promotion marketing text for advisers, grounded in the FCA "
    "Handbook, and logs every generation so affected posts can be flagged if the underlying "
    "FCA content later changes."
)
st.info(
    "This tool assists drafting only. All generated text should still be reviewed and "
    "signed off by your firm's compliance function before use, in line with your usual "
    "financial promotion approval process.",
    icon="⚠️",
)

CATEGORIES = [
    "Retirement",
    "Investment",
    "Protection",
    "Mortgage",
    "Employee Benefit",
    "Investment and Estate Planning",
    "Pension",
    "Lifestyle",
]

tab_generate, tab_history, tab_monitor, tab_flagged = st.tabs(
    ["Generate", "History", "Compliance Monitor", "Flagged Posts"]
)

# ---------------------------------------------------------------- Generate
with tab_generate:
    st.subheader("Post details")
    format_type = st.selectbox("Format", ["Post", "Carousel"])
    category = st.selectbox("Category", CATEGORIES)
    guideline = st.text_area(
        "Optional guideline",
        placeholder="e.g. tone, campaign name, specific product to mention, target platform...",
    )
    num_posts = st.number_input(
        "Number of posts to generate", min_value=1, max_value=20, value=3, step=1
    )

    if st.button("Generate", type="primary"):
        with st.spinner("Grounding prompt in current FCA reference material and generating text..."):
            try:
                refresh_fca_references()
                snapshot = get_current_snapshot()
                posts = generate_posts(format_type, category, guideline, int(num_posts))
                validation_results = validate_posts(posts, format_type, category)
            except Exception as e:
                st.error(f"Generation stopped: {e}")
                posts = None

            if posts:
                failed_results = [
                    result for result in validation_results if not result["passed"]
                ]
                if failed_results:
                    st.error(
                        "The generated content did not pass independent compliance "
                        "validation and was not saved."
                    )
                    for index, result in enumerate(validation_results, start=1):
                        if not result["passed"]:
                            issues = "; ".join(result["issues"]) or result["summary"]
                            st.warning(f"Post {index}: {issues}")
                    posts = None

            if posts:
                used_clauses = [
                    {
                        "section_key": key,
                        "section_name": meta.get("name", key),
                        "url": meta.get("url"),
                        "content_hash": snapshot.get(key),
                    }
                    for key, meta in FCA_SECTIONS.items()
                    if key in snapshot
                ]
                batch = execute(
                    """INSERT INTO generation_batches
                           (advisor_id, format_type, category, guideline, num_posts,
                            fca_sections_snapshot, used_clauses)
                       VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (
                        None,
                        format_type,
                        category,
                        guideline,
                        len(posts),
                        json.dumps(snapshot),
                        json.dumps(used_clauses),
                    ),
                    returning=True,
                )
                batch_id = batch["id"]

                for i, text in enumerate(posts):
                    execute(
                        "INSERT INTO generated_posts (batch_id, post_index, post_text) "
                        "VALUES (%s, %s, %s)",
                        (batch_id, i + 1, text),
                    )

                st.success(f"Generated {len(posts)} post(s) — Batch ID {batch_id}")
                for i, text in enumerate(posts):
                    st.markdown(f"**Post {i + 1}**")
                    st.text_area(
                        f"post_{batch_id}_{i + 1}",
                        value=text,
                        height=150,
                        label_visibility="collapsed",
                    )

# ----------------------------------------------------------------- History
with tab_history:
    st.subheader("Generation history")
    rows = fetch_all(
        """SELECT gb.id AS batch_id, gb.created_at, gb.format_type, gb.category, gb.guideline,
                  gb.num_posts, gb.used_clauses, gp.id AS post_id, gp.post_index, gp.post_text, gp.status
           FROM generation_batches gb
           JOIN generated_posts gp ON gp.batch_id = gb.id
           ORDER BY gb.created_at DESC, gp.post_index ASC"""
    )
    if not rows:
        st.info("No posts generated yet.")
    for r in rows:
        status_badge = "⚠️ FLAGGED FOR REVIEW" if r["status"] == "flagged_for_review" else "✅ active"
        header = (
            f"[{r['created_at']}] Batch {r['batch_id']} / Post {r['post_id']} — "
            f"{r['category']} ({r['format_type']}) — {status_badge}"
        )
        with st.expander(header):
            st.write(f"Guideline: {r['guideline'] or '(none)'}")
            used_clauses = _coerce_json_list(r["used_clauses"])
            if used_clauses:
                st.write("FCA reference material used:")
                for clause in used_clauses:
                    if not isinstance(clause, dict):
                        continue
                    section_key = clause.get("section_key")
                    section_name = clause.get("section_name") or section_key
                    section_url = clause.get("url")
                    content_hash = clause.get("content_hash")
                    text = f"- {section_key}: {section_name}"
                    if section_url:
                        text += f" — {section_url}"
                    if content_hash:
                        text += f" | hash: {content_hash[:12]}"
                    st.caption(text)
            else:
                st.write("FCA reference material used: not recorded")
            st.write("Generated text:")
            st.write(r["post_text"])

# ------------------------------------------------------------ Monitor tab
with tab_monitor:
    st.subheader("FCA Handbook sections tracked")
    sections = fetch_all(
        "SELECT section_key, section_name, url, last_checked, last_changed FROM fca_sections"
    )
    if not sections:
        st.info("Sections not seeded yet.")
    else:
        for s in sections:
            st.write(f"**{s['section_name']}** — [{s['url']}]({s['url']})")
            st.caption(
                f"Last checked: {s['last_checked'] or 'never'} · "
                f"Last changed: {s['last_changed'] or 'never'}"
            )

    st.divider()
    st.write(
        "Run this manually here, or schedule `monitor_cli.py` (see README) to run it "
        "automatically, e.g. daily."
    )
    if st.button("Run compliance check now"):
        with st.spinner("Fetching latest FCA pages and comparing to stored versions..."):
            changes = check_for_updates()
        if not changes:
            st.success("No changes detected — all generated posts remain aligned with current FCA content.")
        else:
            for c in changes:
                if "error" in c:
                    st.error(f"Could not check {c['section']} ({c['url']}): {c['error']}")
                else:
                    st.warning(
                        f"Change detected in **{c['section']}** ({c['url']}). "
                        f"{len(c.get('flagged_post_ids', []))} previously generated post(s) "
                        f"flagged for review."
                    )

    st.subheader("Change log")
    log = fetch_all(
        """SELECT fcl.id, fcl.detected_at, fs.section_name, fs.url, fcl.summary
           FROM fca_change_log fcl
           JOIN fca_sections fs ON fs.id = fcl.section_id
           ORDER BY fcl.detected_at DESC"""
    )
    if not log:
        st.info("No changes logged yet.")
    for l in log:
        st.write(f"[{l['detected_at']}] **{l['section_name']}** — {l['summary']} ({l['url']})")

# ------------------------------------------------------------- Flagged tab
with tab_flagged:
    st.subheader("Posts flagged for review due to FCA changes")
    flagged = fetch_all(
        """SELECT gp.id AS post_id, gp.post_text, gp.status, gb.category, gb.format_type,
                  pf.flagged_at, pf.resolved,
                  fs.section_name, fs.url
           FROM post_flags pf
           JOIN generated_posts gp ON gp.id = pf.post_id
           JOIN generation_batches gb ON gb.id = gp.batch_id
           JOIN fca_change_log fcl ON fcl.id = pf.change_log_id
           JOIN fca_sections fs ON fs.id = fcl.section_id
           WHERE pf.resolved = FALSE
           ORDER BY pf.flagged_at DESC"""
    )
    if not flagged:
        st.info("No flagged posts right now.")
    for f in flagged:
        header = f"Post {f['post_id']} — {f['category']} ({f['format_type']}) — flagged {f['flagged_at']}"
        with st.expander(header):
            st.write(f"Reason: change detected in **{f['section_name']}** ({f['url']})")
            st.write("Original text:")
            st.write(f["post_text"])
            if st.button(f"Mark resolved #{f['post_id']}", key=f"resolve_{f['post_id']}"):
                execute(
                    "UPDATE post_flags SET resolved = TRUE WHERE post_id = %s",
                    (f["post_id"],),
                )
                execute(
                    "UPDATE generated_posts SET status = 'active' WHERE id = %s",
                    (f["post_id"],),
                )
                st.rerun()
