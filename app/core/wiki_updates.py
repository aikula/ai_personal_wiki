"""wiki_updates.py — Safe page update operations with diff generation.

Extracted from wiki_fs.py. Standalone functions, no class dependency.
"""

from __future__ import annotations

import difflib
import logging
from datetime import date

import frontmatter

logger = logging.getLogger("wiki.updates")


def apply_safe_update(
    fs, slug: str, new_raw: str, reason: str
) -> tuple[bool, str | None]:
    """Apply raw content to a wiki page and return (success, diff_text).

    Reads the existing page, generates a unified diff, writes the new
    content, and logs the reason for the change.
    """
    page = fs.read_page(slug)
    if page is None:
        return False, None

    diff = _generate_diff(page.raw, new_raw)

    post = frontmatter.loads(new_raw)
    meta = {}
    for k, v in post.metadata.items():
        if isinstance(v, date):
            meta[k] = v.isoformat()
        else:
            meta[k] = v

    fs.write_page(slug, meta=meta, content=post.content)
    logger.info("Safe update applied: slug=%s reason=%s", slug, reason)
    return True, diff


def generate_update_diff(fs, slug: str, new_raw: str) -> str | None:
    """Generate a unified diff between current and proposed raw content.

    Returns None if the page does not exist.
    """
    page = fs.read_page(slug)
    if page is None:
        return None
    return _generate_diff(page.raw, new_raw)


def _generate_diff(old_raw: str, new_raw: str) -> str:
    """Return a unified-diff string between two raw page contents."""
    old_lines = old_raw.splitlines(keepends=True)
    new_lines = new_raw.splitlines(keepends=True)
    return "".join(difflib.unified_diff(old_lines, new_lines, n=3))
