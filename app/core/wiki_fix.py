"""wiki_fix.py — Repair operations for wiki consistency.

Extracted from wiki_fs.py. Standalone functions, no class dependency.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("wiki.fix")


def fix_broken_wikilinks(fs, project: str | None = None) -> int:
    """Remove broken [[wikilinks]] from all pages in the given project (or all).

    A broken wikilink is one whose target slug does not correspond to
    an existing page. Before removing, attempts to normalize the slug
    (lowercase, replace underscores with hyphens) and check again.
    Returns the number of pages modified.

    Index and log pages are always skipped: log.md is append-only history
    and stripping its wikilinks would silently erase references to pages
    that were later removed/superseded.
    """
    all_pages = fs.list_pages()
    existing_slugs = {p.slug for p in all_pages}
    wikilink_re = re.compile(r"\[\[([^\]|#]+)(?:(#)([^\]|]+))?(?:\|[^\]]+)?\]\]")
    modified_count = 0

    def _normalize_slug(slug: str) -> str:
        return slug.strip().lower().replace("_", "-")

    for page in all_pages:
        if page.page_type in ("index", "log"):
            continue
        if project and page.project != project:
            continue
        original = page.raw

        def _fix_link(m: re.Match) -> str:
            target = m.group(1).strip()
            display = None
            full = m.group(0)
            pipe_match = re.match(r"\[\[([^\]|]+)\|([^\]]+)\]\]", full)
            if pipe_match:
                display = pipe_match.group(2)
            if target in existing_slugs:
                return full
            normalized = _normalize_slug(target)
            if normalized in existing_slugs:
                anchor = f"#{m.group(3)}" if m.group(2) else ""
                display_part = f"|{display}" if display else ""
                return f"[[{normalized}{anchor}{display_part}]]"
            return display if display else ""

        new_content = wikilink_re.sub(_fix_link, original)
        if new_content != original:
            path = fs._slug_to_path(page.slug)
            path.write_text(new_content, encoding="utf-8")
            modified_count += 1

    if modified_count:
        logger.info("Fixed broken wikilinks in %d pages", modified_count)
    return modified_count
