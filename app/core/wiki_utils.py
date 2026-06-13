"""wiki_utils.py — Internal parsing and path resolution helpers.

Extracted from wiki_fs.py. Standalone functions, no class dependency.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import frontmatter

from app.core.utils import validate_slug
from app.core.wiki_types import WikiPage

logger = logging.getLogger("wiki.utils")


def parse_page(fs, path: Path, slug: str) -> WikiPage | None:
    """Parse a wiki page file into a WikiPage object."""
    try:
        raw = path.read_text(encoding="utf-8")
        post = frontmatter.loads(raw)
        meta = {}
        for k, v in post.metadata.items():
            if isinstance(v, date):
                meta[k] = v.isoformat()
            else:
                meta[k] = v
        return WikiPage(
            slug=slug,
            path=path,
            meta=meta,
            content=post.content,
            raw=raw,
            char_count=len(raw),
        )
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.error("Page parse error: slug=%s path=%s error=%s", slug, path, exc)
        return None


def slug_to_path(fs, slug: str) -> Path:
    """Convert a slug to an absolute filesystem path."""
    validate_slug(slug)
    return resolve_in_dir(fs.wiki_dir, f"{slug}.md")


def resolve_in_dir(base_dir: Path, relative_path: str) -> Path:
    """Resolve path and ensure it stays inside base_dir."""
    base = base_dir.resolve()
    target = (base / relative_path).resolve()
    if not target.is_relative_to(base):
        raise ValueError(
            f"Путь выходит за пределы базовой директории: {relative_path!r}"
        )
    return target
