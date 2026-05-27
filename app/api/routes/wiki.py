"""
wiki.py — Wiki navigation and page rendering routes.

GET  /api/wiki/tree          — full wiki tree for right panel
GET  /api/wiki/page/{slug}   — render single page (HTML + raw)
GET  /api/wiki/search        — full-text search
GET  /api/wiki/raw/{slug}    — raw markdown (for edit view)
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import markdown  # pip install markdown
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.dependencies import WikiFS, get_wiki_fs
from app.api.models import (
    WikiPageResponse,
    WikiSearchResponse,
    WikiTreeResponse,
)
from app.core.utils import validate_slug

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
    # Strip anchor if present in slug from frontend
    clean_slug = slug.split("#")[0]
    try:
        validate_slug(clean_slug)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    page = fs.read_page(clean_slug)
    if page is None:
        raise HTTPException(404, f"Страница не найдена: {clean_slug}")

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
        synopsis=page.meta.get("synopsis", ""),
        needs_review=page.meta.get("needs_review", False),
        source_coverage=page.meta.get("source_coverage", ""),
    )


@router.get("/metrics")
async def get_wiki_metrics(
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    """Return graph metrics for the wiki knowledge base."""
    return fs.get_graph_metrics()


@router.get("/projects")
async def list_projects(
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    """Return list of all projects with wiki page and raw file counts."""
    return {"projects": fs.list_all_projects()}


@router.get("/search", response_model=WikiSearchResponse)
async def search_wiki(
    q: str = Query(min_length=1, max_length=500),
    project: str | None = Query(default=None),
    projects: list[str] | None = Query(default=None),  # noqa: B008
    fs: Annotated[WikiFS, Depends(get_wiki_fs)] = None,
):
    """Full-text keyword search across wiki pages with optional project filter."""
    results = fs.search_pages(query=q, project=project, projects=projects)
    return WikiSearchResponse(results=results[:20])


@router.get("/raw/{slug:path}")
async def get_raw_markdown(
    slug: str,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    """Return a raw source file download or wiki page markdown.

    Provenance links use raw source paths under ``raw/``. For backwards
    compatibility, wiki page slugs still return JSON with the page markdown.
    """
    clean_path = slug.split("#")[0]

    raw_relative = clean_path[4:] if clean_path.startswith("raw/") else clean_path
    if raw_relative:
        raw_path = _resolve_raw_path(fs.raw_dir, raw_relative)
        if raw_path is not None:
            return FileResponse(
                path=str(raw_path),
                filename=raw_path.name,
                media_type="application/octet-stream",
            )
        if clean_path.startswith("raw/"):
            raise HTTPException(404, f"Raw file not found: {clean_path}")

    try:
        validate_slug(clean_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    page = fs.read_page(clean_path)
    if page is None:
        raise HTTPException(404, f"Страница не найдена: {clean_path}")
    return {"slug": clean_path, "raw": page.raw}


# ── HTML rendering helper ────────────────────────────────────────

def _render_page_html(content: str, current_slug: str) -> str:
    """
    Convert markdown content to HTML.
    Transforms [[slug]] → <a href="#wiki/slug" class="wikilink">title</a>
    Transforms [[slug|text]] → <a href="#wiki/slug" class="wikilink">text</a>
    Transforms [[slug#anchor]] → <a href="#wiki/slug" class="wikilink">slug</a>
    Transforms ^[raw/path.md] → <sup class="provenance"><a href="#raw/path.md">path.md</a></sup>

    Skips replacements inside code blocks.
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

    def replace_provenance(match):
        raw_path = match.group(1)
        display = raw_path.split("/")[-1]
        return (
            f'<sup class="provenance">'
            f'<a href="/api/wiki/raw/{raw_path}" title="Source: {raw_path}">'
            f'^[{display}]</a></sup>'
        )

    # Replace wikilinks and provenance markers before markdown processing,
    # skipping code blocks
    parts = re.split(r"(```.*?```|`[^`]+`)", content, flags=re.DOTALL)
    for i in range(len(parts)):
        # Only replace in parts that are NOT code blocks
        if not (parts[i].startswith("```") or parts[i].startswith("`")):
            parts[i] = re.sub(r"\[\[([^\]]+)\]\]", replace_wikilink, parts[i])
            parts[i] = re.sub(r"\^\[raw/([^\]]+)\]", replace_provenance, parts[i])

    processed = "".join(parts)

    # Reset and convert
    _MD.reset()
    return _MD.convert(processed)


def _resolve_raw_path(raw_dir: Path, relative_path: str) -> Path | None:
    candidate = (raw_dir / relative_path).resolve()
    raw_root = raw_dir.resolve()
    if raw_root != candidate and raw_root not in candidate.parents:
        raise HTTPException(400, f"Path escapes raw directory: {relative_path}")
    if candidate.exists() and candidate.is_file():
        return candidate
    return None
