"""
Tracks a fixed set of FCA Handbook / guidance pages (drawn from the source
links you supplied), detects when their content changes, and flags any
previously generated posts that were produced using the now-outdated
version of that content.

How the flagging works:
  1. Every time text is generated, we snapshot {section_key: content_hash}
     for all tracked sections and store it on the generation_batches row.
  2. When check_for_updates() runs (manually from the UI, or on a schedule
     via monitor_cli.py) it re-fetches each section, hashes it, and compares
     to the stored hash.
  3. If a section's hash changed, we look for any generation_batches whose
     snapshot recorded the *old* hash for that section — those batches were
     generated against content that is now stale — and flag their posts.
"""
import datetime
import hashlib
import io

import requests
from bs4 import BeautifulSoup

from db import fetch_all, fetch_one, execute

# Sections drawn from the "FCA Handbook etc links" reference doc.
FCA_SECTIONS = {
    "COBS_2_1": {
        "name": "COBS 2.1 — Acting honestly, fairly and professionally",
        "url": "https://www.handbook.fca.org.uk/handbook/COBS/2/1.html",
    },
    "COBS_3": {
        "name": "COBS 3 — Financial promotions",
        "url": "https://www.handbook.fca.org.uk/handbook/COBS/3/",
    },
    "COBS_4": {
        "name": "COBS 4 — Communicating with clients, including financial promotions",
        "url": "https://www.handbook.fca.org.uk/handbook/COBS/4/",
    },
    "COBS_5": {
        "name": "COBS 5 — Communications to clients",
        "url": "https://www.handbook.fca.org.uk/handbook/COBS/5/",
    },
    "COBS_11": {
        "name": "COBS 11 — Records and compliance monitoring",
        "url": "https://www.handbook.fca.org.uk/handbook/COBS/11/",
    },
    "PRIN": {
        "name": "PRIN — Principles for Businesses",
        "url": "https://www.handbook.fca.org.uk/handbook/PRIN/",
    },
    "FG15_4": {
        "name": "FG15/4 — Social Media and Customer Communications",
        "url": "https://www.fca.org.uk/publication/finalised-guidance/fg15-04.pdf",
    },
    "FG22_5": {
        "name": "FG22/5 — Consumer Duty Guidance",
        "url": "https://www.fca.org.uk/publication/finalised-guidance/fg22-5.pdf",
    },
}


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e:  # pragma: no cover - defensive
        return f"[Could not extract PDF text: {e}]"


def _fetch_text(url: str) -> str:
    headers = {"User-Agent": "FCA-Compliance-Monitor/1.0 (contact: adviser-ops)"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")

    if "pdf" in content_type.lower() or url.lower().endswith(".pdf"):
        return _extract_pdf_text(resp.content)

    soup = BeautifulSoup(resp.content, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_sections_seeded():
    """Insert a row for every tracked section if it doesn't exist yet."""
    for key, meta in FCA_SECTIONS.items():
        row = fetch_one("SELECT id FROM fca_sections WHERE section_key = %s", (key,))
        if not row:
            execute(
                """INSERT INTO fca_sections
                       (section_key, section_name, url, content_text, content_hash,
                        last_checked, last_changed)
                   VALUES (%s, %s, %s, NULL, NULL, NULL, NULL)""",
                (key, meta["name"], meta["url"]),
            )


def get_current_snapshot() -> dict:
    """{section_key: content_hash} — recorded against each new generation batch."""
    rows = fetch_all("SELECT section_key, content_hash FROM fca_sections")
    return {r["section_key"]: r["content_hash"] for r in rows}


def get_sections_reference_text() -> str:
    """Concatenated cached text used to ground the copywriting prompt."""
    rows = fetch_all(
        "SELECT section_name, url, content_text FROM fca_sections WHERE content_text IS NOT NULL"
    )
    parts = []
    for r in rows:
        snippet = (r["content_text"] or "")[:4000]
        parts.append(f"### {r['section_name']} ({r['url']})\n{snippet}")
    return "\n\n".join(parts)


def check_for_updates() -> list:
    """
    Re-fetch every tracked section, compare to the stored hash, log any
    changes, and flag previously generated posts whose snapshot recorded the
    old hash. Returns a list of change/error dicts for display in the UI.
    """
    ensure_sections_seeded()
    changes = []
    sections = fetch_all(
        "SELECT id, section_key, section_name, url, content_hash FROM fca_sections"
    )
    now = datetime.datetime.utcnow()

    for sec in sections:
        try:
            new_text = _fetch_text(sec["url"])
        except Exception as e:
            execute(
                "UPDATE fca_sections SET last_checked = %s WHERE id = %s",
                (now, sec["id"]),
            )
            changes.append({"section": sec["section_name"], "url": sec["url"], "error": str(e)})
            continue

        new_hash = _hash(new_text)
        old_hash = sec["content_hash"]

        if old_hash is not None and old_hash != new_hash:
            change_row = execute(
                """INSERT INTO fca_change_log (section_id, old_hash, new_hash, summary)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (
                    sec["id"],
                    old_hash,
                    new_hash,
                    f"Content change detected in {sec['section_name']}",
                ),
                returning=True,
            )
            change_id = change_row["id"]

            affected_batches = fetch_all(
                """SELECT id FROM generation_batches
                   WHERE fca_sections_snapshot ->> %s = %s""",
                (sec["section_key"], old_hash),
            )

            flagged_post_ids = []
            for b in affected_batches:
                posts = fetch_all(
                    "SELECT id FROM generated_posts WHERE batch_id = %s AND status = 'active'",
                    (b["id"],),
                )
                for p in posts:
                    execute(
                        "UPDATE generated_posts SET status = 'flagged_for_review' WHERE id = %s",
                        (p["id"],),
                    )
                    execute(
                        "INSERT INTO post_flags (post_id, change_log_id) VALUES (%s, %s)",
                        (p["id"], change_id),
                    )
                    flagged_post_ids.append(p["id"])

            changes.append(
                {
                    "section": sec["section_name"],
                    "url": sec["url"],
                    "change_log_id": change_id,
                    "flagged_post_ids": flagged_post_ids,
                }
            )

        execute(
            """UPDATE fca_sections
               SET content_text = %s,
                   content_hash = %s,
                   last_checked = %s,
                   last_changed = CASE WHEN content_hash IS DISTINCT FROM %s
                                        THEN %s ELSE last_changed END
               WHERE id = %s""",
            (new_text, new_hash, now, new_hash, now, sec["id"]),
        )

    return changes
