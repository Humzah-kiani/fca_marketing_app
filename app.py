import json
import tempfile
from io import BytesIO

import streamlit as st
from PIL import Image

from post_studio import build_post_canvas, post_studio_ui

from config import AI_PROVIDER, GEMINI_MODEL, OLLAMA_MODEL
from db import init_db, fetch_all, execute
from compliance_validator import validate_posts
from fca_monitor import (
    FCA_SECTIONS,
    ensure_sections_seeded,
    get_current_snapshot,
    get_sections_reference_text,
    check_for_updates,
)
from generator import generate_posts

st.set_page_config(
    page_title="FCA Compliant Marketing Generator",
    page_icon="🛡️",
    layout="wide",
)

active_model = OLLAMA_MODEL if AI_PROVIDER == "ollama" else GEMINI_MODEL


def _inject_theme():
    st.markdown(
        """
        <style>
        :root {
            --navy-900: #0f1f33;
            --navy-800: #16283f;
            --navy-700: #1e3a5f;
            --blue-600: #2563eb;
            --blue-50:  #eef4ff;
            --slate-900: #1e2a3a;
            --slate-600: #52657a;
            --slate-400: #8a9bb0;
            --slate-200: #e3e9f0;
            --slate-100: #eef2f7;
            --card-bg: #ffffff;
            --green-600: #15803d;
            --green-50: #eafaf0;
            --amber-600: #b45309;
            --amber-50: #fff7e6;
        }

        html, body, [class*="css"]  {
            font-family: "Inter", "Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif;
        }

        .stApp {
            background: linear-gradient(180deg, #f4f7fb 0%, #f7f9fc 260px, #f7f9fc 100%);
        }

        /* ---- Top banner ---- */
        .app-banner {
            background: linear-gradient(120deg, var(--navy-900) 0%, var(--navy-700) 100%);
            border-radius: 14px;
            padding: 28px 34px;
            margin-bottom: 22px;
            box-shadow: 0 8px 24px rgba(15, 31, 51, 0.18);
        }
        .app-banner h1 {
            color: #ffffff;
            font-size: 1.65rem;
            font-weight: 700;
            margin: 0 0 6px 0;
            letter-spacing: 0.2px;
        }
        .app-banner p {
            color: #c7d4e6;
            font-size: 0.95rem;
            margin: 0;
            max-width: 780px;
            line-height: 1.5;
        }
        .app-banner .tag {
            display: inline-block;
            background: rgba(255,255,255,0.12);
            color: #dce8fb;
            font-size: 0.72rem;
            font-weight: 600;
            letter-spacing: 0.6px;
            text-transform: uppercase;
            padding: 4px 10px;
            border-radius: 999px;
            margin-bottom: 12px;
        }

        /* ---- Notice / info card ---- */
        .notice-card {
            background: var(--amber-50);
            border: 1px solid #f3d9a8;
            border-left: 4px solid var(--amber-600);
            border-radius: 10px;
            padding: 14px 18px;
            margin-bottom: 22px;
            color: #6b4a12;
            font-size: 0.9rem;
            line-height: 1.5;
        }

        /* ---- Sidebar ---- */
        section[data-testid="stSidebar"] {
            background: var(--navy-900);
        }
        section[data-testid="stSidebar"] * {
            color: #dce8fb !important;
        }
        section[data-testid="stSidebar"] .stCaption, 
        section[data-testid="stSidebar"] p {
            color: #aebedb !important;
        }
        section[data-testid="stSidebar"] h3 {
            color: #ffffff !important;
            font-weight: 700;
            border-bottom: 1px solid rgba(255,255,255,0.12);
            padding-bottom: 8px;
        }

        /* ---- Tabs ---- */
        .stTabs [data-baseweb="tab-list"] {
            gap: 4px;
            border-bottom: 1px solid var(--slate-200);
        }
        .stTabs [data-baseweb="tab"] {
            height: 42px;
            border-radius: 8px 8px 0 0;
            padding: 0 18px;
            background-color: transparent;
            color: var(--slate-600);
            font-weight: 600;
            font-size: 0.92rem;
        }
        .stTabs [aria-selected="true"] {
            background-color: var(--blue-50) !important;
            color: var(--blue-600) !important;
            box-shadow: inset 0 -2px 0 var(--blue-600);
        }

        /* ---- Section headers ---- */
        h2, h3 {
            color: var(--navy-900);
            font-weight: 700;
        }

        /* ---- Buttons ---- */
        .stButton > button {
            border-radius: 8px;
            font-weight: 600;
            border: 1px solid var(--slate-200);
            padding: 0.5rem 1.2rem;
            transition: all 0.15s ease;
        }
        .stButton > button[kind="primary"] {
            background: var(--blue-600);
            border: 1px solid var(--blue-600);
            box-shadow: 0 2px 6px rgba(37, 99, 235, 0.25);
        }
        .stButton > button[kind="primary"]:hover {
            background: #1d4fd1;
            border-color: #1d4fd1;
        }
        .stButton > button:hover {
            border-color: var(--blue-600);
            color: var(--blue-600);
        }

        /* ---- Inputs ---- */
        .stTextInput input, .stTextArea textarea, .stNumberInput input, 
        div[data-baseweb="select"] > div {
            border-radius: 8px !important;
            border-color: var(--slate-200) !important;
        }

        /* ---- Expanders (history / flagged cards) ---- */
        details {
            background: var(--card-bg);
            border: 1px solid var(--slate-200);
            border-radius: 10px;
            margin-bottom: 10px;
            padding: 2px 6px;
            box-shadow: 0 1px 3px rgba(15, 31, 51, 0.04);
        }
        summary {
            font-weight: 600;
            color: var(--navy-800);
        }

        /* ---- Status pills ---- */
        .pill {
            display: inline-block;
            padding: 2px 10px;
            border-radius: 999px;
            font-size: 0.74rem;
            font-weight: 700;
            letter-spacing: 0.3px;
            text-transform: uppercase;
        }
        .pill-active {
            background: var(--green-50);
            color: var(--green-600);
            border: 1px solid #bfe8cf;
        }
        .pill-flagged {
            background: var(--amber-50);
            color: var(--amber-600);
            border: 1px solid #f3d9a8;
        }

        /* ---- Generated post cards ---- */
        .post-label {
            font-weight: 700;
            color: var(--navy-800);
            margin-top: 10px;
            margin-bottom: 2px;
            font-size: 0.95rem;
        }

        .stTextArea textarea {
            background: var(--slate-100);
        }

        hr, .stDivider {
            border-color: var(--slate-200) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


_inject_theme()


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

st.markdown(
    """
    <div class="app-banner">
        <span class="tag">Regulated financial promotions</span>
        <h1>🛡️ FCA-Compliant Marketing Text Generator</h1>
        <p>Generates UK financial-promotion marketing text for advisers, grounded in the FCA
        Handbook, and logs every generation so affected posts can be flagged if the underlying
        FCA content later changes.</p>
    </div>
    """,
    unsafe_allow_html=True,
)
st.markdown(
    """
    <div class="notice-card">
        ⚠️&nbsp; <strong>This tool assists drafting only.</strong> All generated text should
        still be reviewed and signed off by your firm's compliance function before use, in
        line with your usual financial promotion approval process.
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.subheader("⚙️ AI configuration")
    st.caption(f"Provider: **{AI_PROVIDER.title()}**")
    st.caption(f"Model: `{active_model}`")
    if AI_PROVIDER == "ollama":
        st.caption("Using the local Ollama service at http://localhost:11434")

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

tab_generate, tab_history, tab_monitor, tab_flagged, tab_design = st.tabs(
    ["Generate", "History", "Compliance Monitor", "Flagged Posts", "Design Post"]
)

# ---------------------------------------------------------------- Generate
with tab_generate:
    st.subheader("Post details")
    format_type = st.selectbox("Format", ["Post", "Carousel"])
    category = st.selectbox("Category", CATEGORIES, key="generate_category")
    guideline = st.text_area(
        "Optional guideline",
        placeholder="e.g. tone, campaign name, specific product to mention, target platform...",
    )
    num_posts = st.number_input(
        "Number of posts to generate", min_value=1, max_value=20, value=3, step=1
    )

    if st.button("Generate", type="primary"):
        with st.spinner("Generating compliant text..."):
            try:
                ensure_sections_seeded()
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

                st.success(f"✅ Generated {len(posts)} post(s) — Batch ID {batch_id}")
                for i, text in enumerate(posts):
                    st.markdown(
                        f'<div class="post-label">Post {i + 1}</div>',
                        unsafe_allow_html=True,
                    )
                    st.text_area(
                        f"post_{batch_id}_{i + 1}",
                        value=text,
                        height=150,
                        label_visibility="collapsed",
                    )

# -------------------------------------------------------------- Design Post
with tab_design:
    post_studio_ui()

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
        is_flagged = r["status"] == "flagged_for_review"
        pill_class = "pill-flagged" if is_flagged else "pill-active"
        pill_text = "⚠ Flagged for review" if is_flagged else "✓ Active"
        header = (
            f"[{r['created_at']}] Batch {r['batch_id']} / Post {r['post_id']} — "
            f"{r['category']} ({r['format_type']})"
        )
        with st.expander(header):
            st.markdown(
                f'<span class="pill {pill_class}">{pill_text}</span>',
                unsafe_allow_html=True,
            )
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
            with st.container(border=True):
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
            st.markdown(
                '<span class="pill pill-flagged">⚠ Flagged for review</span>',
                unsafe_allow_html=True,
            )
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