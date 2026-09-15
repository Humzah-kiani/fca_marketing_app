"""Refresh and validate the FCA reference material used for generation."""
from fca_monitor import check_for_updates, ensure_sections_seeded
from db import fetch_all


class FCAReferenceError(RuntimeError):
    """Raised when current FCA reference material is unavailable."""


def refresh_fca_references() -> list:
    """Fetch all tracked FCA sources and require usable cached content."""
    ensure_sections_seeded()
    results = check_for_updates()
    errors = [result for result in results if "error" in result]
    if errors:
        details = "; ".join(
            f"{error['section']}: {error['error']}" for error in errors
        )
        raise FCAReferenceError(f"Could not refresh FCA references: {details}")

    missing = fetch_all(
        """SELECT section_name FROM fca_sections
           WHERE content_text IS NULL OR BTRIM(content_text) = ''
              OR content_hash IS NULL
           ORDER BY section_name"""
    )
    if missing:
        sections = ", ".join(row["section_name"] for row in missing)
        raise FCAReferenceError(
            f"FCA references are unavailable for: {sections}."
        )

    return results
