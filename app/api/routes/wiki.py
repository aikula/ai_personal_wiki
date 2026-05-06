"""
wiki.py — Wiki navigation and page rendering routes.

GET  /api/wiki/tree          — full wiki tree for right panel
GET  /api/wiki/page/{slug}   — render single page (HTML + raw)
GET  /api/wiki/search        — full-text search
GET  /api/wiki/raw/{slug}    — raw markdown (for edit view)
"""

from __future__ import annotations

from typing import Annotated

import markdown  # pip install markdown
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import WikiFS, get_wiki_fs
from app.api.models import (
    WikiPageResponse,
    WikiSearchResponse,
    WikiTreeResponse,
)

router = APIRouter(prefix="/api/wiki", tags=["wiki"])

# Markdown renderer with wikilink extension (custom)
_MD = markdown.Markdown(extensions=["tables", "fenced_code", "toc"])


@router.get("/tree", response_model=WikiTreeResponse)
async def get_wiki_tree(
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    """
    Return full wiki structure for right panel navigation.
    Response used to render collapsible project tree with clickable links.
    """
    tree = fs.get_wiki_tree()
    return WikiTreeResponse(**tree)


@router.get("/page/{slug:path}", response_model=WikiPageResponse)
async def get_wiki_page(
    slug: str,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    """
    Get a wiki page by slug for display in right panel.
    [[wikilinks]] in content are converted to clickable HTML links.
    slug: path segments, e.g. "myapp/storage/redis"
    """
    page = fs.read_page(slug)
    if page is None:
        raise HTTPException(404, f"Page not found: {slug}")

    # Convert [[slug]] to HTML links before markdown rendering
    html_content = _render_page_html(page.content, slug)

    return WikiPageResponse(
        slug=page.slug,
        title=page.title,
        project=page.project,
        page_type=page.page_type,
        tags=page.tags,
        confidence=page.confidence,
        sources=page.meta.get("sources", 0),
        last_confirmed=str(page.meta.get("last_confirmed", "")),
        content_html=html_content,
        content_raw=page.content,
        char_count=page.char_count,
        wikilinks=page.wikilinks,
        superseded_by=page.meta.get("superseded_by"),
        supersedes=page.meta.get("supersedes"),
    )


@router.get("/search", response_model=WikiSearchResponse)
async def search_wiki(
    q: str = Query(min_length=1, max_length=500),
    project: str | None = Query(default=None),
    fs: Annotated[WikiFS, Depends(get_wiki_fs)] = None,
):
    """Full-text keyword search across wiki pages."""
    results = fs.search_pages(query=q, project=project)
    return WikiSearchResponse(results=results[:20])


@router.get("/raw/{slug:path}")
async def get_raw_markdown(
    slug: str,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    """Return raw markdown including frontmatter. Used by edit view."""
    page = fs.read_page(slug)
    if page is None:
        raise HTTPException(404, f"Page not found: {slug}")
    return {"slug": slug, "raw": page.raw}


# ── HTML rendering helper ────────────────────────────────────────

def _render_page_html(content: str, current_slug: str) -> str:
    """
    Convert markdown content to HTML.
    Transforms [[slug]] → <a href="#wiki/slug" class="wikilink">title</a>
    Transforms [[slug|text]] → <a href="#wiki/slug" class="wikilink">text</a>
    Transforms [[slug#anchor]] → <a href="#wiki/slug" class="wikilink">slug</a>
    """
    import re

    def replace_wikilink(match):
        inner = match.group(1)
        if "|" in inner:
            slug_part, display = inner.split("|", 1)
        else:
            slug_part = inner
            display = inner.split("/")[-1].replace("-", " ").title()

        # Handle anchors
        href_slug = slug_part
        if "#" in slug_part:
            slug_base, anchor = slug_part.split("#", 1)
            href_slug = f"{slug_base}#{anchor}"
            if "|" not in inner:
                display = slug_base.split("/")[-1].replace("-", " ").title()

        return (
            f'<a href="#wiki/{href_slug}" '
            f'class="wikilink">'
            f'{display}</a>'
        )

    # Replace wikilinks before markdown processing
    processed = re.sub(r"\[\[([^\]]+)\]\]", replace_wikilink, content)

    # Reset and convert
    _MD.reset()
    return _MD.convert(processed)