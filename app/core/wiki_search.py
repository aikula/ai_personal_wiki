"""wiki_search.py — Search, tree, outline, and section operations extracted from WikiFS."""

from __future__ import annotations

import logging
import re

from app.core.utils import heading_to_anchor
from app.core.wiki_types import (
    PageOutline,
    SectionContent,
    _next_heading_at_or_above,
)

logger = logging.getLogger("wiki.search")


def _extract_excerpt(content: str, word: str, window: int = 120) -> str:
    idx = content.lower().find(word.lower())
    if idx == -1:
        return content[:window]
    start = max(0, idx - 40)
    end = min(len(content), idx + window - 40)
    excerpt = content[start:end].replace("\n", " ").strip()
    return f"…{excerpt}…" if start > 0 else f"{excerpt}…"


def search_pages(
    fs,
    query: str,
    project: str | None = None,
    projects: list[str] | None = None,
) -> list[dict]:
    words = query.lower().split()
    results = []

    for page in fs.list_pages(project=project, projects=projects):
        text = page.raw.lower()
        score = sum(text.count(w) for w in words)
        if score == 0:
            continue
        excerpt = _extract_excerpt(page.content, words[0])
        results.append({
            "slug": page.slug,
            "title": page.title,
            "project": page.project,
            "excerpt": excerpt,
            "score": score,
        })

    return sorted(results, key=lambda x: -x["score"])


def search_pages_weighted(
    fs,
    query: str,
    project: str | None = None,
    projects: list[str] | None = None,
    top_k: int = 10,
) -> list[dict]:
    words = query.lower().split()
    if not words:
        return []

    results = []

    for page in fs.list_pages(project=project, projects=projects):
        title_lower = page.title.lower()
        tags_lower = [t.lower() for t in page.tags]
        synopsis = page.meta.get("synopsis", "")
        synopsis_lower = synopsis.lower()

        headings_text = " ".join(
            h.strip("# ").lower()
            for h in re.findall(r"^#{1,6}\s+(.+)$", page.content, re.MULTILINE)
        )

        body_lower = page.content.lower()

        title_score = sum(1 for w in words if w in title_lower)
        tag_score = sum(1 for w in words for t in tags_lower if w in t)
        synopsis_score = sum(1 for w in words if w in synopsis_lower)
        heading_score = sum(1 for w in words if w in headings_text)
        body_score = sum(body_lower.count(w) for w in words)

        total = (
            8 * title_score
            + 5 * tag_score
            + 4 * synopsis_score
            + 3 * heading_score
            + 1 * body_score
        )

        if total == 0:
            continue

        excerpt = _extract_excerpt(page.content, words[0])
        results.append({
            "slug": page.slug,
            "title": page.title,
            "project": page.project,
            "excerpt": excerpt,
            "score": total,
            "field_scores": {
                "title": title_score,
                "tags": tag_score,
                "summary": synopsis_score,
                "headings": heading_score,
                "body": body_score,
            },
        })

    return sorted(results, key=lambda x: -x["score"])[:top_k]


def get_wiki_tree(fs) -> dict:
    tree: dict[str, list] = {}
    for page in fs.list_pages():
        project = page.project
        if project not in tree:
            tree[project] = []
        tree[project].append({
            "slug": page.slug,
            "title": page.title,
            "type": page.page_type,
            "confidence": page.confidence,
            "tags": page.tags,
        })
    return {
        "projects": tree,
        "total_pages": sum(len(v) for v in tree.values()),
        "open_conflicts": fs.count_open_conflicts(),
    }


def build_link_candidates(fs, project: str | None = None) -> list[dict]:
    candidates = []
    for page in fs.list_pages(project=project):
        if page.page_type in ("index", "log") or page.slug in ("index", "log"):
            continue
        aliases = [page.title]
        last = page.slug.rstrip("/").split("/")[-1]
        aliases.append(last.replace("-", " "))
        aliases.append(last)
        aliases.extend(t for t in page.tags if len(t) > 3)
        candidates.append({
            "slug": page.slug,
            "title": page.title,
            "project": page.project,
            "type": page.page_type,
            "tags": page.tags,
            "synopsis": page.meta.get("synopsis", ""),
            "aliases": list(dict.fromkeys(a for a in aliases if a)),
        })
    return candidates


def get_graph_metrics(fs) -> dict:
    pages = fs.list_pages()
    incoming: dict[str, set[str]] = {}
    outgoing_count: dict[str, int] = {}
    for p in pages:
        incoming.setdefault(p.slug, set())
        outgoing_count[p.slug] = 0

    for p in pages:
        if p.page_type == "index":
            continue
        for linked in p.wikilinks:
            if linked in incoming:
                incoming[linked].add(p.slug)
            outgoing_count[p.slug] += 1

    non_index = [p for p in pages if p.page_type not in ("index", "log")]
    orphans = [p for p in non_index if not incoming.get(p.slug)]
    no_outgoing = [p for p in non_index if outgoing_count.get(p.slug, 0) == 0]
    no_related = [
        p.slug
        for p in pages
        if "связанные страницы" not in p.content.lower()
        and p.page_type not in ("index", "log")
    ]

    return {
        "total_pages": len(pages),
        "non_index_pages": len(non_index),
        "total_wikilinks": sum(outgoing_count.values()),
        "avg_outgoing_per_page": (
            round(sum(outgoing_count[p.slug] for p in non_index) / len(non_index), 2)
            if non_index
            else 0
        ),
        "orphan_count": len(orphans),
        "orphan_slugs": [p.slug for p in orphans],
        "pages_with_no_outgoing": len(no_outgoing),
        "pages_with_no_outgoing_slugs": [p.slug for p in no_outgoing],
        "pages_without_related_section": len(no_related),
        "pages_without_related_section_slugs": no_related,
    }


def read_page_outline(fs, slug: str) -> PageOutline | None:
    page = fs.read_page(slug)
    if page is None:
        return None

    synopsis = page.meta.get("synopsis", "")
    if not synopsis:
        first_para = re.search(r"^(?!#)(.+)$", page.content, re.MULTILINE)
        if first_para:
            synopsis = first_para.group(1).strip()[:300]

    headings = []
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    for match in heading_pattern.finditer(page.content):
        level = len(match.group(1))
        text = match.group(2).strip()
        anchor = heading_to_anchor(text)

        start = match.end()
        next_heading = _next_heading_at_or_above(page.content, start, level)
        if next_heading:
            section_text = page.content[start:next_heading.start()].strip()
        else:
            section_text = page.content[start:].strip()

        preview = section_text[:200].replace("\n", " ") if section_text else ""

        headings.append({
            "text": text,
            "anchor": anchor,
            "level": level,
            "char_count": len(section_text),
            "preview": preview,
        })

    return PageOutline(
        slug=page.slug,
        title=page.title,
        project=page.project,
        page_type=page.page_type,
        tags=page.tags,
        synopsis=synopsis,
        headings=headings,
        wikilinks=page.wikilinks,
        char_count=page.char_count,
        confidence=page.confidence,
    )


def read_page_section(
    fs,
    slug: str,
    heading: str,
    char_limit: int | None = None,
) -> SectionContent | None:
    page = fs.read_page(slug)
    if page is None:
        return None

    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    matches = list(heading_pattern.finditer(page.content))

    target_idx = None
    heading_lower = heading.lower().strip()

    for i, match in enumerate(matches):
        text = match.group(2).strip()
        anchor = heading_to_anchor(text)
        if heading_lower == text.lower() or heading_lower == anchor.lower():
            target_idx = i
            break

    if target_idx is None:
        return None

    match = matches[target_idx]
    level = len(match.group(1))
    start = match.end()
    next_match = None
    for candidate in matches[target_idx + 1:]:
        if len(candidate.group(1)) <= level:
            next_match = candidate
            break

    if next_match:
        section_text = page.content[start:next_match.start()].strip()
    else:
        section_text = page.content[start:].strip()

    if char_limit and len(section_text) > char_limit:
        section_text = section_text[:char_limit - 40] + "\n\n… [TRIMMED]"

    provenance = re.findall(r"\^\[([^\]]+)\]", section_text)
    source_refs = re.findall(r"raw/([^\s\]]+)", section_text)

    return SectionContent(
        slug=page.slug,
        heading=match.group(2).strip(),
        anchor=heading_to_anchor(match.group(2).strip()),
        content=section_text,
        char_count=len(section_text),
        provenance_markers=provenance,
        source_refs=source_refs,
    )


def multi_read_sections(
    fs,
    requests: list[dict],
) -> list[SectionContent | None]:
    return [
        read_page_section(fs, r["slug"], r["heading"], r.get("char_limit"))
        for r in requests
    ]
