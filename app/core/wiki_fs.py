"""
wiki_fs.py — Filesystem abstraction layer for wiki-data/.

RULES:
- This is the ONLY module that writes to wiki-data/
- All methods are synchronous (async wrappers in api layer if needed)
- Every write validates character limits before writing
- Every write to a wiki page validates frontmatter completeness
- read_* methods never raise on missing file — return None instead
- write_* methods always raise WikiFSError on failure (never silent)
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.safe_page_updates import PageUpdateDiff

import frontmatter  # python-frontmatter

try:
    from markitdown import MarkItDown
    MARKITDOWN_AVAILABLE = True
except ImportError:
    MARKITDOWN_AVAILABLE = False

from app.config import Settings
from app.core.utils import (
    extract_wikilinks,
    heading_to_anchor,
    validate_project_name,
    validate_raw_filename,
    validate_slug,
)

logger = logging.getLogger("wiki.fs")


# ─────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────

class WikiFSError(Exception):
    """Base error for all filesystem operations."""

class FrontmatterError(WikiFSError):
    """Page missing required frontmatter fields."""

class CharLimitExceededError(WikiFSError):
    """Page content exceeds character limit for its type."""
    def __init__(self, path: Path, actual: int, limit: int):
        self.path = path
        self.actual = actual
        self.limit = limit
        super().__init__(
            f"{path.name}: {actual} chars exceeds limit {limit} "
            f"({actual - limit} chars over)"
        )

class SlugConflictError(WikiFSError):
    """Attempt to create page with already existing slug."""

class ReviewRequiredError(WikiFSError):
    """Update requires human review before applying."""
    def __init__(self, slug: str, diff, reason: str):
        self.slug = slug
        self.diff = diff
        self.reason = reason
        super().__init__(f"Review required for {slug}: {reason}")


# ─────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────

REQUIRED_FRONTMATTER = {
    "title", "project", "type", "tags",
    "confidence", "sources", "last_confirmed",
    "supersedes", "superseded_by", "created",
}

PAGE_TYPES = {"entity", "concept", "index", "log", "source"}
PROJECT_TYPES = frozenset()


def _validate_frontmatter(meta: dict) -> None:
    missing = REQUIRED_FRONTMATTER - set(meta.keys())
    if missing:
        raise FrontmatterError(f"Отсутствуют обязательные поля frontmatter: {missing}")


def _next_heading_at_or_above(content: str, start: int, level: int) -> re.Match | None:
    heading_re = re.compile(r"^(#{1,6})\s+.+$", re.MULTILINE)
    for match in heading_re.finditer(content, start):
        if len(match.group(1)) <= level:
            return match
    return None


@dataclass
class WikiPage:
    """
    Parsed wiki page. Returned by all read operations.
    slug: path relative to wiki/ without .md, e.g. "myapp/deploy"
    """
    slug: str
    path: Path
    meta: dict          # parsed frontmatter fields
    content: str        # markdown body (without frontmatter block)
    raw: str            # full file content including frontmatter
    char_count: int

    @property
    def title(self) -> str:
        return self.meta.get("title", self.slug)

    @property
    def project(self) -> str:
        return self.meta.get("project", "_general")

    @property
    def page_type(self) -> str:
        return self.meta.get("type", "entity")

    @property
    def tags(self) -> list[str]:
        return self.meta.get("tags", [])

    @property
    def confidence(self) -> float:
        return float(self.meta.get("confidence", 1.0))

    @property
    def wikilinks(self) -> list[str]:
        """Extract all [[slug]], [[slug|text]], [[slug#anchor]] from content."""
        return extract_wikilinks(self.content)

    @property
    def anchors(self) -> set[str]:
        """All heading-based anchors in this page."""
        headings = re.findall(r"^#{1,6}\s+(.+)$", self.content, re.MULTILINE)
        return {heading_to_anchor(h) for h in headings}


@dataclass
class SourceCard:
    """Source identity card tracking ingest state and drift."""
    source_id: str                # e.g. "myapp/deploy_guide"
    source_path: str              # relative path from raw/, e.g. "raw/myapp/deploy_guide.md"
    source_sha256: str
    title: str                    # "Source: deploy_guide.md"
    project: str
    ingest_status: str            # "active" | "changed" | "removed" | "error"
    created: str                  # ISO date
    last_confirmed: str           # ISO date
    last_ingested: str            # ISO datetime
    outline: list[dict]           # [{text, level, char_count}, ...]
    chunk_count: int
    chunks_processed: int
    chunks_failed: int
    pages_planned: list[str]      # slugs planned for creation/update
    pages_written: list[str]      # slugs actually written
    conflicts_opened: list[str]   # conflict IDs opened during ingest
    claims_files: list[str]       # paths to claim files: _claims/<project>/<source-slug>/chunk-XXX.md
    drift_status: str             # "unknown" | "unchanged" | "changed" | "missing_source"

    @property
    def slug(self) -> str:
        return f"_sources/{self.source_id}"


@dataclass
class PageOutline:
    """Structured outline of a wiki page for query retrieval."""
    slug: str
    title: str
    project: str
    page_type: str
    tags: list[str]
    synopsis: str            # from frontmatter or first paragraph
    headings: list[dict]     # [{text, anchor, level, char_count, preview}]
    wikilinks: list[str]
    char_count: int
    confidence: float


@dataclass
class SectionContent:
    """Content of a specific section within a wiki page."""
    slug: str
    heading: str
    anchor: str
    content: str
    char_count: int
    provenance_markers: list[str]   # ^[...] markers found in section
    source_refs: list[str]          # raw source references if available


@dataclass
class ConflictEntry:
    id: str                    # e.g. "CONFLICT-007"
    status: str                # "OPEN" | "RESOLVED"
    date: str
    project: str
    source_file: str
    conflict_type: str         # "factual_contradiction" | "version_mismatch" | ...
    page_a_slug: str
    page_b_ref: str
    context_a: str             # relevant excerpt from wiki page (up to 600 chars)
    context_b: str             # relevant excerpt from source document (up to 600 chars)
    suggested_options: list[str]
    description: str = ""      # 1-2 sentence LLM summary of what exactly contradicts
    is_cross_project: bool = False  # True if cross_project_difference
    user_comment: str = ""
    resolution: str = "pending"
    skill_extracted: str = ""
    resolved_at: str = ""


@dataclass
class IngestLog:
    """Written to log.md after each ingest operation."""
    timestamp: str
    source_file: str
    project: str
    pages_created: list[str]   # slugs
    pages_updated: list[str]   # slugs
    conflicts_detected: list[str]  # conflict IDs
    skills_triggered: list[str]
    char_delta: int            # total chars added to wiki


@dataclass
class Claim:
    """
    A factual claim extracted from a source during chunk analysis.

    Claims are stored as individual files in wiki/_claims/<project>/<source-slug>/
    to enable deduplication, conflict detection, and provenance tracking.
    """
    claim_id: str               # e.g. "myapp/deploy_guide#chunk-001-claim-001"
    source_id: str              # e.g. "myapp/deploy_guide"
    source_path: str            # e.g. "raw/myapp/deploy_guide.md"
    source_sha256: str
    source_section: str         # e.g. "## Redis"
    quote: str                  # verbatim quote from source
    normalized: str             # normalized form for deduplication
    related_slugs: list[str]    # wiki page slugs this claim supports
    confidence: float           # 0.0-1.0
    status: str                 # "active" | "superseded" | "contradicted" | "unresolved" | "ignored"
    chunk_id: str               # which chunk produced this claim
    project: str
    created: str                # ISO date

    @property
    def file_path(self) -> str:
        """Relative path within wiki/_claims/ for this claim."""
        safe_source = self.source_id.replace("/", "__")
        return f"_claims/{self.project}/{safe_source}/{self.chunk_id}/{self.claim_id.split('#')[-1]}.md"


# ─────────────────────────────────────────────
# WikiFS
# ─────────────────────────────────────────────

class WikiFS:
    """
    All filesystem operations for wiki-data/.
    Instantiate once per request or inject as singleton.

    Usage:
        fs = WikiFS(settings)
        fs = WikiFS.from_path(path, limits=settings.limits)
        page = fs.read_page("myapp/deploy")
        fs.write_page("myapp/deploy", meta=..., content=...)
    """

    def __init__(self, settings_or_root: Settings | Path | str, limits=None):
        if isinstance(settings_or_root, Settings):
            self.root = Path(settings_or_root.wiki_data_path)
            self.limits = settings_or_root.limits
        else:
            self.root = Path(settings_or_root)
            self.limits = limits

        self.wiki_dir = self.root / "wiki"
        self.raw_dir = self.root / "raw"
        self._defer_index = False
        self._ensure_structure()

    @classmethod
    def from_path(cls, root: Path, limits=None) -> WikiFS:
        """Create WikiFS from a direct path (used with WorkspaceContext)."""
        return cls(root, limits=limits)

    @property
    def state_dir(self) -> Path:
        return self.root / ".state"

    # ── Initialisation ──────────────────────────────────────────

    def _ensure_structure(self) -> None:
        """Create required directories if they don't exist."""
        for d in [
            self.wiki_dir,
            self.raw_dir / "_general",
            self.root,
            self.state_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

        # Bootstrap required files if missing
        if not (self.root / "conflicts.md").exists():
            (self.root / "conflicts.md").write_text(
                "# Conflicts\n\n_No conflicts recorded yet._\n",
                encoding="utf-8"
            )
        if not (self.root / "skills.md").exists():
            (self.root / "skills.md").write_text(
                _SKILLS_TEMPLATE, encoding="utf-8"
            )
        if not (self.wiki_dir / "index.md").exists():
            self._bootstrap_index()
        if not (self.wiki_dir / "log.md").exists():
            (self.wiki_dir / "log.md").write_text(
                "# Change Log\n\n", encoding="utf-8"
            )

    def _bootstrap_index(self) -> None:
        today = date.today().isoformat()
        content = f"""---
title: Wiki Index
project: _general
type: index
tags: []
confidence: 1.0
sources: 0
last_confirmed: {today}
supersedes: null
superseded_by: null
created: {today}
---

# Wiki Index

Last updated: {today}
Pages: 0 | Projects: 0 | Open conflicts: 0
"""
        (self.wiki_dir / "index.md").write_text(content, encoding="utf-8")

    # ── Page READ operations ─────────────────────────────────────

    def read_page(self, slug: str) -> WikiPage | None:
        """
        Read a wiki page by slug.
        slug: relative to wiki/, without .md (e.g. "myapp/deploy")
        Returns None if page does not exist. Never raises on missing.
        """
        path = self._slug_to_path(slug)
        if not path.exists():
            return None
        return self._parse_page(path, slug)

    def read_page_by_path(self, path: Path) -> WikiPage | None:
        """Read page by absolute path. Returns None if not found."""
        if not path.exists():
            return None
        slug = path.relative_to(self.wiki_dir).with_suffix("").as_posix()
        return self._parse_page(path, slug)

    def list_pages(
        self,
        project: str | None = None,
        projects: list[str] | None = None,
        page_type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[WikiPage]:
        """
        List wiki pages with optional filters.
        All filters are AND-combined.
        Returns list sorted by slug.
        """
        pages = []
        for path in sorted(self.wiki_dir.rglob("*.md")):
            slug = path.relative_to(self.wiki_dir).with_suffix("").as_posix()
            page = self._parse_page(path, slug)
            if page is None:
                continue
            if project and page.project != project:
                continue
            if projects and page.project not in projects:
                continue
            if page_type and page.page_type != page_type:
                continue
            if tags and not all(t in page.tags for t in tags):
                continue
            pages.append(page)
        return pages

    def list_projects(self) -> list[str]:
        """Return sorted list of all project names in wiki."""
        projects = set()
        for page in self.list_pages():
            projects.add(page.project)
        return sorted(projects)

    def list_all_projects(self) -> list[dict]:
        """
        Return list of all projects with counts from both wiki pages and raw files.
        Always includes _general even if empty.
        Returns: [{"name": str, "wiki_pages": int, "raw_files": int}, ...]
        """
        # Collect wiki page projects
        wiki_projects: dict[str, int] = {}
        for page in self.list_pages():
            wiki_projects[page.project] = wiki_projects.get(page.project, 0) + 1

        # Collect raw file projects
        raw_projects: dict[str, int] = {}
        if self.raw_dir.exists():
            for f in self.raw_dir.rglob("*"):
                if f.is_file():
                    proj = self.get_raw_project(f)
                    raw_projects[proj] = raw_projects.get(proj, 0) + 1

        # Merge
        all_names = set(wiki_projects.keys()) | set(raw_projects.keys())
        # Always include _general
        all_names.add("_general")

        result = []
        for name in sorted(all_names):
            result.append({
                "name": name,
                "wiki_pages": wiki_projects.get(name, 0),
                "raw_files": raw_projects.get(name, 0),
            })
        return result

    def get_wiki_tree(self) -> dict:
        """
        Return nested dict representing wiki directory structure.
        Used by UI right panel for navigation rendering.

        Returns:
            {
              "projects": {
                "myapp": [
                  {"slug": "myapp/deploy", "title": "Deploy Guide",
                   "type": "entity", "confidence": 0.9}
                ],
                "_general": [...]
              },
              "total_pages": 24,
              "open_conflicts": 2
            }
        """
        tree: dict[str, list] = {}
        for page in self.list_pages():
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
            "open_conflicts": self.count_open_conflicts(),
        }

    def build_link_candidates(self, project: str | None = None) -> list[dict]:
        """Build compact list of known wiki pages for linker prompt injection.

        Each candidate::

            {"slug": str, "title": str, "project": str, "type": str,
             "tags": list[str], "aliases": list[str],
             "synopsis": str}
        """
        candidates = []
        for page in self.list_pages(project=project):
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

    def get_graph_metrics(self) -> dict:
        """Compute graph-level metrics for the wiki."""
        pages = self.list_pages()
        incoming: dict[str, set[str]] = {}
        outgoing_count: dict[str, int] = {}
        for p in pages:
            incoming.setdefault(p.slug, set())
            outgoing_count[p.slug] = 0

        for p in pages:
            if p.page_type == "index":  # skip index pages (L0/L1)
                continue
            for linked in p.wikilinks:
                if linked in incoming:
                    incoming[linked].add(p.slug)
                outgoing_count[p.slug] += 1

        non_index = [p for p in pages if p.page_type not in ("index", "log")]
        orphans = [p for p in non_index if not incoming.get(p.slug)]
        no_outgoing = [p for p in non_index if outgoing_count.get(p.slug, 0) == 0]
        no_related = [p.slug for p in pages
                      if "связанные страницы" not in p.content.lower()
                      and p.page_type not in ("index", "log")]

        return {
            "total_pages": len(pages),
            "non_index_pages": len(non_index),
            "total_wikilinks": sum(outgoing_count.values()),
            "avg_outgoing_per_page": (
                round(sum(outgoing_count[p.slug] for p in non_index) / len(non_index), 2)
                if non_index else 0
            ),
            "orphan_count": len(orphans),
            "orphan_slugs": [p.slug for p in orphans],
            "pages_with_no_outgoing": len(no_outgoing),
            "pages_with_no_outgoing_slugs": [p.slug for p in no_outgoing],
            "pages_without_related_section": len(no_related),
            "pages_without_related_section_slugs": no_related,
        }

    def search_pages(self, query: str, project: str | None = None, projects: list[str] | None = None) -> list[dict]:
        """
        Full-text grep search across wiki pages.
        Returns list of {slug, title, excerpt, score} sorted by score desc.
        score = number of query word matches in content.
        """
        words = query.lower().split()
        results = []

        for page in self.list_pages(project=project, projects=projects):
            text = page.raw.lower()
            score = sum(text.count(w) for w in words)
            if score == 0:
                continue
            # Find first match location for excerpt
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
        self,
        query: str,
        project: str | None = None,
        projects: list[str] | None = None,
        top_k: int = 20,
    ) -> list[dict]:
        """
        Weighted field search across wiki pages.

        score =
          8 * matches_in_title +
          5 * matches_in_tags +
          4 * matches_in_summary +
          3 * matches_in_headings +
          1 * matches_in_body

        Returns list of {slug, title, project, excerpt, score, field_scores}
        sorted by score desc.
        """
        words = query.lower().split()
        if not words:
            return []

        results = []

        for page in self.list_pages(project=project, projects=projects):
            title_lower = page.title.lower()
            tags_lower = [t.lower() for t in page.tags]
            synopsis = page.meta.get("synopsis", "")
            synopsis_lower = synopsis.lower()

            # Extract headings
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

    def read_page_outline(self, slug: str) -> PageOutline | None:
        """
        Return structured outline of a wiki page.

        Returns title, synopsis, tags, headings with anchors and previews,
        wikilinks, and metadata. Used by QueryAgent for index-first retrieval.
        """
        page = self.read_page(slug)
        if page is None:
            return None

        # Synopsis: from frontmatter or first non-heading paragraph
        synopsis = page.meta.get("synopsis", "")
        if not synopsis:
            first_para = re.search(r"^(?!#)(.+)$", page.content, re.MULTILINE)
            if first_para:
                synopsis = first_para.group(1).strip()[:300]

        # Parse headings
        headings = []
        heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

        for match in heading_pattern.finditer(page.content):
            level = len(match.group(1))
            text = match.group(2).strip()
            anchor = heading_to_anchor(text)

            # Find content until next heading of same or higher level.
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
        self,
        slug: str,
        heading: str,
        char_limit: int | None = None,
    ) -> SectionContent | None:
        """
        Return full text of a specific section identified by heading text.

        heading: exact heading text or anchor slug.
        char_limit: optional max chars to return (None = unlimited).
        """
        page = self.read_page(slug)
        if page is None:
            return None

        heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
        matches = list(heading_pattern.finditer(page.content))

        # Find the matching heading
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

        # Apply char limit if specified
        if char_limit and len(section_text) > char_limit:
            section_text = section_text[:char_limit - 40] + "\n\n… [TRIMMED]"

        # Extract provenance markers
        provenance = re.findall(r"\^\[([^\]]+)\]", section_text)

        # Extract source refs (raw file paths mentioned)
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
        self,
        requests: list[dict],
    ) -> list[SectionContent | None]:
        """
        Batch-read multiple sections from multiple pages.

        requests: list of {slug, heading, char_limit?}
        Returns list of SectionContent or None (if not found).
        """
        return [
            self.read_page_section(r["slug"], r["heading"], r.get("char_limit"))
            for r in requests
        ]

    # ── Page WRITE operations ────────────────────────────────────

    def write_page(
        self,
        slug: str,
        meta: dict,
        content: str,
        allow_overwrite: bool = True,
    ) -> WikiPage:
        path = self._slug_to_path(slug)

        if not allow_overwrite and path.exists():
            raise SlugConflictError(f"Страница уже существует: {slug}")

        # Auto-fill nullable required fields that LLM may omit
        meta.setdefault("supersedes", None)
        meta.setdefault("superseded_by", None)

        _validate_frontmatter(meta)

        if not meta.get("last_confirmed"):
            meta["last_confirmed"] = date.today().isoformat()

        post = frontmatter.Post(content, **meta)
        raw = frontmatter.dumps(post)

        limit = self._char_limit_for_type(meta.get("type", "entity"))
        if len(raw) > limit:
            raise CharLimitExceededError(path, len(raw), limit)

        is_new = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")

        self._update_index_entry(slug, meta)

        action = "created" if is_new else "updated"
        logger.info("Page %s: slug=%s chars=%d", action, slug, len(raw))

        return self._parse_page(path, slug)

    def apply_safe_update(
        self,
        slug: str,
        plan,
        force: bool = False,
    ) -> tuple[WikiPage, PageUpdateDiff]:
        """
        Apply a typed page update plan with diff generation.

        If the update requires review and force=False, raises ReviewRequiredError.
        Returns (updated_page, diff) tuple.
        """
        from app.core.safe_page_updates import (
            generate_diff,
            validate_plan,
        )

        page = self.read_page(slug)
        if page is None:
            raise WikiFSError(f"Page not found: {slug}")

        # Validate plan
        errors = validate_plan(plan)
        if errors:
            raise WikiFSError(f"Invalid update plan for {slug}: {'; '.join(errors)}")

        # Generate diff
        diff = generate_diff(page, plan, self.limits.entity_page_chars // 2)

        # Check review requirement
        if diff.requires_review and not force:
            raise ReviewRequiredError(
                slug=slug,
                diff=diff,
                reason=diff.review_reason,
            )

        # Apply and write
        meta, content = _apply_ops(page, plan)
        return self.write_page(slug, meta=meta, content=content), diff

    def generate_update_diff(
        self,
        slug: str,
        plan,
    ) -> PageUpdateDiff | None:
        """Generate diff for a planned update without applying it."""
        from app.core.safe_page_updates import generate_diff

        page = self.read_page(slug)
        if page is None:
            return None
        return generate_diff(page, plan)

    def delete_page(self, slug: str) -> bool:
        path = self._slug_to_path(slug)
        if not path.exists():
            return False
        path.unlink()
        self._remove_index_entry(slug)
        logger.info("Page deleted: slug=%s", slug)
        return True

    def supersede_page(self, old_slug: str, new_slug: str) -> None:
        """
        Mark old_slug as superseded by new_slug.
        Updates frontmatter of old page, does not delete it.
        """
        old = self.read_page(old_slug)
        if old is None:
            raise WikiFSError(f"Невозможно заменить несуществующую страницу: {old_slug}")
        meta = dict(old.meta)
        meta["superseded_by"] = new_slug
        self.write_page(old_slug, meta=meta, content=old.content)

    # ── RAW file operations ──────────────────────────────────────

    def list_raw_files(self, project: str | None = None) -> list[Path]:
        """
        List all .md files in raw/.
        project=None returns all; project="_general" returns raw/_general/*.md
        """
        if project:
            validate_project_name(project)
            target = self.raw_dir / project
            if not target.exists():
                return []
            return sorted(target.rglob("*.md"))
        return sorted(self.raw_dir.rglob("*.md"))

    def read_raw_file(self, relative_path: str) -> str | None:
        """
        Read raw source file. relative_path from raw/ root.
        e.g. "myapp/deploy_guide.md"
        For .md, .txt, .py files: returns UTF-8 text content.
        For .pdf, .docx, .pptx files: uses mrkitdown to extract text content.
        Returns None if not found.
        """
        if not relative_path:
            raise ValueError("relative_path не может быть пустым")
        path = self._resolve_in_dir(self.raw_dir, relative_path)
        if not path.exists():
            return None
            
        # Handle text-based formats directly
        if path.suffix.lower() in {'.md', '.txt', '.py'}:
            return path.read_text(encoding="utf-8")
        
        # Handle document formats with markitdown
        if path.suffix.lower() in {'.pdf', '.docx', '.pptx'}:
            if not MARKITDOWN_AVAILABLE:
                logger.warning("markitdown not available, cannot read %s", path.suffix)
                return None
            try:
                converter = MarkItDown()
                result = converter.convert(str(path))
                text = getattr(result, "text_content", None) or str(result)
                return text if text.strip() else None
            except Exception as e:
                logger.error("Failed to convert %s with markitdown: %s", path, e)
                return None
        
        # For unsupported formats, return None (should not happen due to validation)
        logger.warning("Unsupported file format: %s", path.suffix)
        return None

    def save_raw_file(self, project: str, filename: str, content: str) -> Path:
        validate_project_name(project)
        validate_raw_filename(filename)
        target_dir = self.raw_dir / project
        target_dir.mkdir(parents=True, exist_ok=True)
        target = self._resolve_in_dir(target_dir, filename)
        target.write_text(content, encoding="utf-8")
        logger.info("Raw file saved: path=%s/%s chars=%d", project, filename, len(content))
        self.update_source_manifest(f"{project}/{filename}", content)
        return target

    def get_raw_project(self, raw_path: Path) -> str:
        """
        Determine project name from raw file path.
        raw/myapp/guide.md -> "myapp"
        raw/_general/guide.md -> "_general"
        raw/guide.md -> "_general"  (root = general)
        """
        rel = raw_path.relative_to(self.raw_dir)
        parts = rel.parts
        if len(parts) >= 2:
            return parts[0]
        return "_general"

    # ── Source manifest (state) ────────────────────────────────────

    def _manifest_path(self) -> Path:
        return self.state_dir / "source_manifest.json"

    def _read_manifest(self) -> dict:
        path = self._manifest_path()
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    def _write_manifest(self, manifest: dict) -> None:
        self._manifest_path().write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _sha256(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def check_source_state(self, relative_path: str, content: str) -> dict:
        """Check if a source file has changed since last ingest.

        Returns::

            {"status": "new"|"changed"|"unchanged"|"duplicate",
             "sha256": str, "duplicate_of": str | None}
        """
        manifest = self._read_manifest()
        sha = self._sha256(content)
        datetime.now().isoformat(timespec="seconds")

        if relative_path in manifest:
            entry = manifest[relative_path]
            if entry["sha256"] == sha:
                return {"status": "unchanged", "sha256": sha, "duplicate_of": None}
            return {"status": "changed", "sha256": sha, "duplicate_of": None}

        # Check for duplicate (same hash, different path)
        for path, entry in manifest.items():
            if entry["sha256"] == sha:
                return {"status": "duplicate", "sha256": sha, "duplicate_of": path}

        return {"status": "new", "sha256": sha, "duplicate_of": None}

    def update_source_manifest(self, relative_path: str, content: str) -> None:
        """Record or update a source file in the manifest after ingest."""
        manifest = self._read_manifest()
        sha = self._sha256(content)
        now = datetime.now().isoformat(timespec="seconds")

        if relative_path in manifest:
            manifest[relative_path]["sha256"] = sha
            manifest[relative_path]["last_seen"] = now
            manifest[relative_path]["last_ingested"] = now
            manifest[relative_path]["status"] = "active"
            manifest[relative_path]["size"] = len(content)
        else:
            manifest[relative_path] = {
                "sha256": sha,
                "size": len(content),
                "first_seen": now,
                "last_seen": now,
                "last_ingested": now,
                "status": "active",
            }

        self._write_manifest(manifest)

    def mark_source_removed(self, relative_path: str) -> None:
        """Mark a source file as removed (when raw file is deleted)."""
        manifest = self._read_manifest()
        if relative_path in manifest:
            manifest[relative_path]["status"] = "removed"
            self._write_manifest(manifest)

    # ── Source Card operations ────────────────────────────────────

    def compute_source_sha256(self, content: str) -> str:
        """Compute SHA256 hash of source content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _source_card_path(self, source_id: str) -> Path:
        """Return path for a Source Card: wiki/_sources/<project>/<source-slug>.md"""
        return self._resolve_in_dir(self.wiki_dir, f"_sources/{source_id}.md")

    def write_source_card(self, card: SourceCard) -> None:
        """Write or update a Source Card to wiki/_sources/."""
        path = self._source_card_path(card.source_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        meta = {
            "title": card.title,
            "project": card.project,
            "type": "source",
            "tags": ["source", "ingest"],
            "confidence": 1.0,
            "sources": 1,
            "last_confirmed": card.last_confirmed,
            "supersedes": None,
            "superseded_by": None,
            "created": card.created,
            # Source Card specific fields
            "source_id": card.source_id,
            "source_path": card.source_path,
            "source_sha256": card.source_sha256,
            "ingest_status": card.ingest_status,
            "outline": card.outline,
            "chunk_count": card.chunk_count,
            "chunks_processed": card.chunks_processed,
            "chunks_failed": card.chunks_failed,
            "pages_planned": card.pages_planned,
            "pages_written": card.pages_written,
            "conflicts_opened": card.conflicts_opened,
            "claims_files": card.claims_files,
            "drift_status": card.drift_status,
        }

        # Build content section
        outline_lines = []
        for item in card.outline:
            indent = "  " * (item.get("level", 1) - 1)
            outline_lines.append(f"{indent}- {item['text']} ({item.get('char_count', 0)} chars)")

        content = (
            f"# {card.title}\n\n"
            f"**Source:** `{card.source_path}`\n"
            f"**SHA256:** `{card.source_sha256[:16]}...`\n"
            f"**Status:** {card.ingest_status}\n"
            f"**Drift:** {card.drift_status}\n\n"
            f"## Outline\n\n"
            + "\n".join(outline_lines)
            + f"\n\n## Stats\n\n"
            f"- Chunks: {card.chunks_processed}/{card.chunk_count} processed"
            f" ({card.chunks_failed} failed)\n"
            f"- Pages written: {len(card.pages_written)}\n"
            f"- Conflicts opened: {len(card.conflicts_opened)}\n"
            f"- Claims files: {len(card.claims_files)}\n"
        )

        post = frontmatter.Post(content, **meta)
        raw = frontmatter.dumps(post)
        path.write_text(raw, encoding="utf-8")
        logger.info("Source Card written: id=%s status=%s", card.source_id, card.ingest_status)

    def read_source_card(self, source_id: str) -> SourceCard | None:
        """Read a Source Card by source_id. Returns None if not found."""
        path = self._source_card_path(source_id)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            post = frontmatter.loads(raw)
            meta = post.metadata

            def _date_or_today(field: str) -> str:
                val = meta.get(field, "")
                if isinstance(val, date):
                    return val.isoformat()
                return val or date.today().isoformat()

            return SourceCard(
                source_id=meta.get("source_id", source_id),
                source_path=meta.get("source_path", ""),
                source_sha256=meta.get("source_sha256", ""),
                title=meta.get("title", f"Source: {source_id}"),
                project=meta.get("project", "_general"),
                ingest_status=meta.get("ingest_status", "unknown"),
                created=_date_or_today("created"),
                last_confirmed=_date_or_today("last_confirmed"),
                last_ingested=meta.get("last_ingested", ""),
                outline=meta.get("outline", []),
                chunk_count=meta.get("chunk_count", 0),
                chunks_processed=meta.get("chunks_processed", 0),
                chunks_failed=meta.get("chunks_failed", 0),
                pages_planned=meta.get("pages_planned", []),
                pages_written=meta.get("pages_written", []),
                conflicts_opened=meta.get("conflicts_opened", []),
                claims_files=meta.get("claims_files", []),
                drift_status=meta.get("drift_status", "unknown"),
            )
        except Exception as exc:
            logger.error("Source Card parse error: id=%s error=%s", source_id, exc)
            return None

    def list_source_cards(self, project: str | None = None) -> list[SourceCard]:
        """List all Source Cards, optionally filtered by project."""
        cards = []
        sources_dir = self.wiki_dir / "_sources"
        if not sources_dir.exists():
            return cards

        for path in sorted(sources_dir.rglob("*.md")):
            slug = path.relative_to(self.wiki_dir).with_suffix("").as_posix()
            # slug = "_sources/<project>/<source-slug>"
            parts = slug.split("/", 2)
            if len(parts) < 3:
                continue
            source_id = f"{parts[1]}/{parts[2]}"
            card = self.read_source_card(source_id)
            if card is None:
                continue
            if project and card.project != project:
                continue
            cards.append(card)
        return cards

    def check_source_drift(self, relative_path: str) -> dict:
        """
        Check if a raw source file has drifted since last ingest.

        Returns:
            {"status": "unchanged"|"changed"|"missing_source"|"no_card",
             "old_sha256": str | None, "new_sha256": str | None}
        """
        # Try to find card by source_path in manifest
        manifest = self._read_manifest()

        if relative_path not in manifest:
            # Check if source file exists
            raw_path = self.raw_dir / relative_path
            if not raw_path.exists():
                return {
                    "status": "missing_source",
                    "old_sha256": None,
                    "new_sha256": None,
                }
            return {"status": "no_card", "old_sha256": None, "new_sha256": None}

        entry = manifest[relative_path]
        old_sha = entry.get("sha256")

        # Read current content
        content = self.read_raw_file(relative_path)
        if content is None:
            return {
                "status": "missing_source",
                "old_sha256": old_sha,
                "new_sha256": None,
            }

        new_sha = self.compute_source_sha256(content)

        if old_sha == new_sha:
            return {"status": "unchanged", "old_sha256": old_sha, "new_sha256": new_sha}

        return {"status": "changed", "old_sha256": old_sha, "new_sha256": new_sha}

    def update_source_card_drift(self, source_id: str, drift_status: str) -> None:
        """Update drift_status field on an existing Source Card."""
        card = self.read_source_card(source_id)
        if card is None:
            logger.warning("Cannot update drift: Source Card not found: %s", source_id)
            return
        card.drift_status = drift_status
        if drift_status == "changed":
            card.ingest_status = "changed"
        self.write_source_card(card)

    # ── Claim operations ─────────────────────────────────────────

    def _claim_path(self, claim: Claim) -> Path:
        """Get absolute path for a claim file."""
        return self._resolve_in_dir(self.wiki_dir, claim.file_path)

    def write_claim(self, claim: Claim) -> Path:
        """Write a claim to wiki/_claims/. Returns the written path."""
        path = self._claim_path(claim)
        path.parent.mkdir(parents=True, exist_ok=True)

        meta = {
            "title": f"Claim: {claim.claim_id}",
            "project": claim.project,
            "type": "concept",
            "tags": ["claim", claim.source_id, claim.status],
            "confidence": claim.confidence,
            "sources": 1,
            "last_confirmed": claim.created,
            "supersedes": None,
            "superseded_by": None,
            "created": claim.created,
            # Claim-specific fields
            "claim_id": claim.claim_id,
            "source_id": claim.source_id,
            "source_path": claim.source_path,
            "source_sha256": claim.source_sha256,
            "source_section": claim.source_section,
            "quote": claim.quote,
            "normalized": claim.normalized,
            "related_slugs": claim.related_slugs,
            "status": claim.status,
            "chunk_id": claim.chunk_id,
        }

        content = (
            f"# {claim.claim_id}\n\n"
            f"**Source:** `{claim.source_path}`\n"
            f"**Section:** {claim.source_section}\n"
            f"**Status:** {claim.status}\n\n"
            f"## Quote\n\n"
            f"> {claim.quote}\n\n"
            f"## Normalized\n\n"
            f"{claim.normalized}\n\n"
            f"## Related Pages\n\n"
            + "\n".join(f"- [[{s}]]" for s in claim.related_slugs)
            + "\n"
        )

        post = frontmatter.Post(content, **meta)
        raw = frontmatter.dumps(post)
        path.write_text(raw, encoding="utf-8")
        logger.debug("Claim written: id=%s status=%s", claim.claim_id, claim.status)
        return path

    def read_claim(self, claim_id: str, project: str, source_id: str, chunk_id: str) -> Claim | None:
        """Read a specific claim by its identifiers."""
        safe_source = source_id.replace("/", "__")
        rel_path = f"_claims/{project}/{safe_source}/{chunk_id}/{claim_id.split('#')[-1]}.md"
        path = self._resolve_in_dir(self.wiki_dir, rel_path)
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            post = frontmatter.loads(raw)
            meta = post.metadata

            def _date_or(field: str, default: str) -> str:
                val = meta.get(field, default)
                return val.isoformat() if isinstance(val, date) else (val or default)

            return Claim(
                claim_id=meta.get("claim_id", claim_id),
                source_id=meta.get("source_id", source_id),
                source_path=meta.get("source_path", ""),
                source_sha256=meta.get("source_sha256", ""),
                source_section=meta.get("source_section", ""),
                quote=meta.get("quote", ""),
                normalized=meta.get("normalized", ""),
                related_slugs=meta.get("related_slugs", []),
                confidence=float(meta.get("confidence", 1.0)),
                status=meta.get("status", "active"),
                chunk_id=meta.get("chunk_id", chunk_id),
                project=project,
                created=_date_or("created", date.today().isoformat()),
            )
        except Exception as exc:
            logger.error("Claim parse error: id=%s error=%s", claim_id, exc)
            return None

    def list_claims(
        self,
        project: str | None = None,
        source_id: str | None = None,
        status: str | None = None,
    ) -> list[Claim]:
        """List claims with optional filters."""
        claims = []
        claims_dir = self.wiki_dir / "_claims"
        if not claims_dir.exists():
            return claims

        for path in sorted(claims_dir.rglob("*.md")):
            try:
                raw = path.read_text(encoding="utf-8")
                post = frontmatter.loads(raw)
                meta = post.metadata

                claim_project = meta.get("project", "_general")
                claim_source = meta.get("source_id", "")
                claim_status = meta.get("status", "active")

                if project and claim_project != project:
                    continue
                if source_id and claim_source != source_id:
                    continue
                if status and claim_status != status:
                    continue

                created_val = meta.get("created", date.today().isoformat())
                created = created_val.isoformat() if isinstance(created_val, date) else (created_val or date.today().isoformat())

                claims.append(Claim(
                    claim_id=meta.get("claim_id", ""),
                    source_id=claim_source,
                    source_path=meta.get("source_path", ""),
                    source_sha256=meta.get("source_sha256", ""),
                    source_section=meta.get("source_section", ""),
                    quote=meta.get("quote", ""),
                    normalized=meta.get("normalized", ""),
                    related_slugs=meta.get("related_slugs", []),
                    confidence=float(meta.get("confidence", 1.0)),
                    status=claim_status,
                    chunk_id=meta.get("chunk_id", ""),
                    project=claim_project,
                    created=created,
                ))
            except Exception as exc:
                logger.debug("Skipping claim file %s: %s", path, exc)

        return claims

    def find_duplicate_claim(
        self,
        normalized: str,
        source_id: str,
    ) -> Claim | None:
        """
        Check if a claim with the same normalized text already exists
        for the same source. Returns the existing claim if found.
        """
        normalized_key = normalized[:100].lower().strip()
        existing = self.list_claims(source_id=source_id, status="active")
        for claim in existing:
            if claim.normalized[:100].lower().strip() == normalized_key:
                return claim
        return None

    def update_claim_status(self, claim_id: str, project: str, source_id: str, chunk_id: str, new_status: str) -> bool:
        """Update the status of an existing claim."""
        claim = self.read_claim(claim_id, project, source_id, chunk_id)
        if claim is None:
            return False
        claim.status = new_status
        self.write_claim(claim)
        return True

    def get_claims_for_page(self, slug: str) -> list[Claim]:
        """Get all active claims that reference a given wiki page."""
        all_claims = self.list_claims(status="active")
        return [c for c in all_claims if slug in c.related_slugs]

    def detect_claim_conflicts(self, source_id: str) -> list[tuple[Claim, Claim]]:
        """
        Detect conflicting claims within the same source.

        Returns list of (claim_a, claim_b) pairs where claims have
        contradictory statuses or normalized text suggests contradiction.
        """
        conflicts = []
        claims = self.list_claims(source_id=source_id, status="active")

        for i, claim_a in enumerate(claims):
            for claim_b in claims[i + 1:]:
                # Check if they reference the same page but have different info
                common_slugs = set(claim_a.related_slugs) & set(claim_b.related_slugs)
                if common_slugs:
                    # Same page, different claims — potential conflict
                    if claim_a.confidence > 0.7 and claim_b.confidence > 0.7:
                        conflicts.append((claim_a, claim_b))

        return conflicts

    # ── Conflicts operations ─────────────────────────────────────

    def read_conflicts_raw(self) -> str:
        path = self.root / "conflicts.md"
        return path.read_text(encoding="utf-8")

    def append_conflict(self, entry: ConflictEntry) -> None:
        """
        Append new conflict entry to conflicts.md.
        Auto-archives resolved conflicts if file exceeds limit.
        """
        path = self.root / "conflicts.md"
        current = path.read_text(encoding="utf-8")

        new_block = _render_conflict_block(entry)
        updated = current.rstrip() + "\n\n---\n\n" + new_block + "\n"

        if len(updated) > self.limits.conflicts_md_chars:
            self._archive_resolved_conflicts()
            current = path.read_text(encoding="utf-8")
            updated = current.rstrip() + "\n\n---\n\n" + new_block + "\n"

        path.write_text(updated, encoding="utf-8")

    def resolve_conflict(
        self,
        conflict_id: str,
        resolution: str,
        user_comment: str,
        skill_extracted: str = "",
    ) -> bool:
        """
        Mark conflict as resolved. Updates status in conflicts.md.
        Returns False if conflict_id not found.
        """
        path = self.root / "conflicts.md"
        content = path.read_text(encoding="utf-8")

        # Update status marker
        old = f"## [OPEN] {conflict_id}"
        new = f"## [RESOLVED] {conflict_id}"
        if old not in content:
            return False

        content = content.replace(old, new)

        # Inject resolution fields
        ts = datetime.now().isoformat(timespec="seconds")
        resolution_block = (
            f"- **User comment:** {user_comment}\n"
            f"- **Resolution:** {resolution}\n"
            f"- **Skill extracted:** {skill_extracted}\n"
            f"- **Resolved at:** {ts}\n"
        )
        # Insert before the closing --- of this conflict block
        content = _inject_after_conflict_id(content, conflict_id, resolution_block)

        path.write_text(content, encoding="utf-8")
        self.rebuild_index()
        return True

    def prepare_conflict_resolution_draft(
        self,
        conflict_id: str,
        resolution: str,
        user_comment: str = "",
    ) -> dict | None:
        """
        Create a draft update for the wiki page affected by a resolved conflict.
        Returns draft metadata or None if conflict/page not found.
        """
        # Find the conflict in conflicts.md
        conflicts_raw = self.read_conflicts_raw()
        pattern = rf"## \[(?:OPEN|RESOLVED)\] {re.escape(conflict_id)}(.*?)(?=\n---\n## |\Z)"
        match = re.search(pattern, conflicts_raw, re.DOTALL)
        if not match:
            return None

        block = match.group(1)
        # Extract page_a_slug
        slug_match = re.search(r"Page A.*?\[\[([^\]]+)\]\]", block)
        if not slug_match:
            return None
        page_a_slug = slug_match.group(1)

        # Extract source context
        context_source_match = re.search(r"Context B.*?>\s*(.+?)(?=\n\n|\n-)", block, re.DOTALL)
        context_source = context_source_match.group(1).strip() if context_source_match else ""

        # Get existing page
        existing_page = self.read_page(page_a_slug)
        if existing_page is None:
            return None

        # Create draft
        draft_id = f"conflict-{conflict_id}"
        draft_dir = self.drafts_dir / draft_id
        draft_dir.mkdir(parents=True, exist_ok=True)

        # Store conflict metadata
        meta = {
            "conflict_id": conflict_id,
            "resolution": resolution,
            "user_comment": user_comment,
            "affected_slug": page_a_slug,
            "source_context": context_source,
            "existing_content": existing_page.raw,
        }
        (draft_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Store existing page for diff
        (draft_dir / "existing.md").write_text(existing_page.raw, encoding="utf-8")

        logger.info("Conflict resolution draft created: %s for %s", draft_id, page_a_slug)
        return {
            "draft_id": draft_id,
            "affected_slug": page_a_slug,
            "resolution": resolution,
            "source_context": context_source,
        }

    def count_open_conflicts(self) -> int:
        content = self.read_conflicts_raw()
        return content.count("## [OPEN]")

    def clear_open_conflicts(self) -> int:
        """Remove all OPEN conflicts and keep RESOLVED history."""
        path = self.root / "conflicts.md"
        content = self.read_conflicts_raw()
        parts = re.split(r"\n---\n", content)

        removed = 0
        kept: list[str] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if "## [OPEN]" in part:
                removed += 1
                continue
            kept.append(part)

        if removed == 0:
            return 0

        if not kept:
            new_content = "# Conflicts\n\n_No conflicts recorded yet._\n"
        else:
            new_content = "\n\n---\n\n".join(kept) + "\n"
        path.write_text(new_content, encoding="utf-8")
        self.rebuild_index()
        logger.info("Cleared %d OPEN conflicts before rebuild", removed)
        return removed

    # ── Skills operations ────────────────────────────────────────

    def read_skills(self) -> str:
        path = self.root / "skills.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def append_skill(self, section: str, skill_text: str) -> None:
        """
        Append skill to named section in skills.md.
        section: one of "Source Trust Rules", "Conflict Resolution Patterns",
                 "Domain Conventions", "Query Formatting Rules", "Ingest Patterns"
        Creates section if not found.
        Raises CharLimitExceededError if skills.md would exceed limit.
        """
        path = self.root / "skills.md"
        content = path.read_text(encoding="utf-8")

        entry = f"- {skill_text.strip()}\n"
        section_header = f"## {section}"

        if section_header in content:
            # Insert after section header (newest skills first — reverse chronological)
            content = content.replace(
                section_header,
                f"{section_header}\n{entry}",
                1
            )
        else:
            # Append new section
            content += f"\n{section_header}\n{entry}"

        if len(content) > self.limits.skills_md_chars:
            raise CharLimitExceededError(path, len(content), self.limits.skills_md_chars)

        path.write_text(content, encoding="utf-8")

    # ── Log operations ───────────────────────────────────────────

    def append_log(self, entry: IngestLog) -> None:
        """
        Append ingest record to log.md.
        Rotates log to archive/ if char limit exceeded.
        """
        path = self.wiki_dir / "log.md"
        current = path.read_text(encoding="utf-8")

        new_entry = _render_log_entry(entry)
        updated = current.rstrip() + "\n\n" + new_entry + "\n"

        if len(updated) > self.limits.log_md_chars:
            self._rotate_log(current)
            updated = "# Change Log\n\n" + new_entry + "\n"

        path.write_text(updated, encoding="utf-8")

    def _rotate_log(self, content: str) -> None:
        archive_dir = self.root / "archive"
        archive_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y_%m")
        archive_path = archive_dir / f"log_{ts}.md"
        # If archive for this month exists, append
        if archive_path.exists():
            existing = archive_path.read_text(encoding="utf-8")
            archive_path.write_text(existing + "\n" + content, encoding="utf-8")
        else:
            archive_path.write_text(content, encoding="utf-8")

    # ── Draft operations ──────────────────────────────────────────

    @property
    def drafts_dir(self) -> Path:
        return self.root / "drafts"

    def create_draft(
        self,
        draft_id: str,
        plan: dict,
        pages: dict[str, str],
        conflicts: list[dict],
    ) -> None:
        """Create a draft artifact for human review.

        Args:
            draft_id:  e.g. ``ingest-20260507-120000``
            plan:      dict with analysis plan summary
            pages:     ``{slug: new_content_markdown}`` for each candidate
            conflicts: list of conflict dicts

        Writes to ``drafts/{draft_id}/``.
        """
        d = self.drafts_dir / draft_id
        d.mkdir(parents=True, exist_ok=True)

        (d / "plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (d / "conflicts.json").write_text(
            json.dumps(conflicts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        pages_dir = d / "pages"
        diffs_dir = d / "diffs"
        pages_dir.mkdir(exist_ok=True)
        diffs_dir.mkdir(exist_ok=True)

        for slug, content in pages.items():
            safe = slug.replace("/", "__")
            (pages_dir / f"{safe}.md").write_text(content, encoding="utf-8")

            old = self.read_page(slug)
            old_text = old.raw if old else ""
            diff = list(
                difflib.unified_diff(
                    old_text.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"wiki/{slug}",
                    tofile=f"draft/{slug}",
                )
            )
            if diff:
                (diffs_dir / f"{safe}.diff.md").write_text(
                    "".join(diff), encoding="utf-8"
                )

        logger.info("Draft created: id=%s pages=%d", draft_id, len(pages))

    def list_drafts(self) -> list[dict]:
        """List pending drafts with metadata."""
        if not self.drafts_dir.exists():
            return []
        drafts = []
        for d in sorted(self.drafts_dir.iterdir()):
            if not d.is_dir():
                continue
            plan_path = d / "plan.json"
            conflicts_path = d / "conflicts.json"
            pages = sorted(
                p.stem.replace("__", "/")
                for p in (d / "pages").glob("*.md")
            ) if (d / "pages").exists() else []
            diffs = sorted(
                p.name for p in (d / "diffs").glob("*.diff.md")
            ) if (d / "diffs").exists() else []
            plan = {}
            if plan_path.exists():
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
            conflicts = []
            if conflicts_path.exists():
                conflicts = json.loads(conflicts_path.read_text(encoding="utf-8"))
            drafts.append({
                "id": d.name,
                "created": d.stat().st_mtime,
                "pages": pages,
                "diff_count": len(diffs),
                "conflict_count": len(conflicts),
                "plan_summary": plan.get("summary", ""),
            })
        return drafts

    def read_draft(self, draft_id: str) -> dict | None:
        """Read full draft details, returns None if not found."""
        d = self.drafts_dir / draft_id
        if not d.exists():
            return None

        pages: list[dict] = []
        diffs: list[dict] = []
        pages_dir = d / "pages"
        diffs_dir = d / "diffs"

        if pages_dir.exists():
            for p in sorted(pages_dir.glob("*.md")):
                slug = p.stem.replace("__", "/")
                pages.append({
                    "slug": slug,
                    "content": p.read_text(encoding="utf-8"),
                })
        if diffs_dir.exists():
            for p in sorted(diffs_dir.glob("*.diff.md")):
                diffs.append({
                    "filename": p.name,
                    "content": p.read_text(encoding="utf-8"),
                })

        plan = {}
        plan_path = d / "plan.json"
        if plan_path.exists():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))

        conflicts = []
        conflicts_path = d / "conflicts.json"
        if conflicts_path.exists():
            conflicts = json.loads(conflicts_path.read_text(encoding="utf-8"))

        return {
            "id": draft_id,
            "plan": plan,
            "pages": pages,
            "diffs": diffs,
            "conflicts": conflicts,
        }

    def apply_draft(self, draft_id: str) -> list[str]:
        """Apply a draft: write all candidate pages to the wiki.
        Returns list of applied slugs. Removes draft on success."""
        draft = self.read_draft(draft_id)
        if draft is None:
            raise WikiFSError(f"Черновик не найден: {draft_id}")

        applied = []
        errors: list[str] = []
        for page in draft["pages"]:
            # Parse frontmatter + content from the candidate markdown
            try:
                post = frontmatter.loads(page["content"])
                meta = dict(post.metadata)
                content = post.content
            except Exception as exc:
                msg = f"{page['slug']}: parse error ({exc})"
                errors.append(msg)
                logger.warning("Draft apply parse error: %s", msg)
                continue

            try:
                self.write_page(
                    slug=page["slug"],
                    meta=meta,
                    content=content,
                    allow_overwrite=True,
                )
                applied.append(page["slug"])
            except Exception as exc:
                msg = f"{page['slug']}: write error ({exc})"
                errors.append(msg)
                logger.warning("Draft apply write error: %s", msg)

        if errors:
            raise WikiFSError("Применение черновика завершено с ошибками: " + "; ".join(errors))
        if not applied:
            raise WikiFSError("Применение черновика: нет валидных страниц для применения")

        # Remove draft directory only after successful apply
        import shutil
        shutil.rmtree(self.drafts_dir / draft_id)
        logger.info("Draft applied: id=%s pages=%s", draft_id, applied)
        return applied

    def reject_draft(self, draft_id: str) -> bool:
        """Reject a draft: remove the draft directory. Returns True if removed."""
        d = self.drafts_dir / draft_id
        if not d.exists():
            return False
        import shutil
        shutil.rmtree(d)
        logger.info("Draft rejected: id=%s", draft_id)
        return True

    # ── Rebuild operation ────────────────────────────────────────

    def full_reset_wiki(self) -> None:
        if self.wiki_dir.exists():
            shutil.rmtree(self.wiki_dir)
            logger.info("Wiki directory removed: %s", self.wiki_dir)
        self.wiki_dir.mkdir()
        self._bootstrap_index()
        today = date.today().isoformat()
        log_content = f"""---
title: Change Log
project: _general
type: log
tags: []
confidence: 1.0
sources: 0
last_confirmed: {today}
supersedes: null
superseded_by: null
created: {today}
---

# Change Log

"""
        (self.wiki_dir / "log.md").write_text(log_content, encoding="utf-8")
        logger.info("Wiki directory re-bootstrapped: %s", self.wiki_dir)

    def clear_all_drafts(self) -> int:
        """Remove all pending drafts. Called before rebuild to avoid stale drafts."""
        if not self.drafts_dir.exists():
            return 0
        removed = 0
        for d in self.drafts_dir.iterdir():
            if d.is_dir():
                shutil.rmtree(d)
                removed += 1
        logger.info("Cleared %d stale drafts before rebuild", removed)
        return removed

    def defer_index(self) -> None:
        """Suppress index updates for batch operations (avoids O(N²))."""
        self._defer_index = True

    def resume_index(self) -> None:
        """Re-enable index updates after batch operation."""
        self._defer_index = False

    def rebuild_index(self) -> None:
        """Rebuild index.md from scratch from current wiki state."""
        pages = self.list_pages()
        projects = {p.project for p in pages}
        open_conf = self.count_open_conflicts()
        today = date.today().isoformat()

        # Group pages by project
        by_project: dict[str, list] = {}
        for p in pages:
            by_project.setdefault(p.project, []).append(p)

        # Write per-project L1 indices
        for proj, proj_pages in by_project.items():
            self._write_project_index(proj, proj_pages, today)

        # Update main L0 index
        index_path = self.wiki_dir / "index.md"
        index_page = self.read_page("index")
        if index_page is None:
            return

        stats_block = (
            f"Last updated: {today}\n"
            f"Pages: {len(pages)} | "
            f"Projects: {len(projects)} | "
            f"Open conflicts: {open_conf}"
        )
        new_content = re.sub(
            r"Last updated:.*?Open conflicts: \d+",
            stats_block,
            index_page.content,
            flags=re.DOTALL,
        )

        for proj in projects:
            if f"## {proj}" not in new_content:
                new_content += f"\n## {proj}\n[[{proj}/index]] — project {proj}\n"

        index_path.write_text(
            frontmatter.dumps(frontmatter.Post(new_content, **index_page.meta)),
            encoding="utf-8"
        )
        logger.info("Index rebuilt: pages=%d projects=%d", len(pages), len(projects))

    def _write_project_index(self, project: str, pages: list, today: str) -> None:
        """Write per-project L1 index (wiki/<project>/index.md)."""
        # Group pages by type
        by_type: dict[str, list] = {}
        for p in pages:
            by_type.setdefault(p.page_type, []).append(p)

        # Content
        lines = [
            f"# {project} Wiki",
            "",
            f"Last updated: {today}",
            f"Pages: {len(pages)}",
            "",
        ]

        for page_type in ["entity", "concept", "index", "log"]:
            if page_type in by_type:
                lines.append(f"## {page_type.title()}s")
                for p in by_type[page_type]:
                    lines.append(f"[[{p.slug}]] — {p.title}")
                lines.append("")

        content = "\n".join(lines).rstrip() + "\n"

        # Check char limit
        if len(content) > self.limits.index_l1_chars:
            logger.warning("Project index %s exceeds char limit", project)

        # Frontmatter
        meta = {
            "title": f"{project} Wiki",
            "project": project,
            "type": "index",
            "tags": [],
            "confidence": 1.0,
            "sources": 0,
            "last_confirmed": today,
            "supersedes": None,
            "superseded_by": None,
            "created": today,
        }

        # Write using frontmatter (same as write_page)
        index_path = self.wiki_dir / project / "index.md"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post(content, **meta)
        index_path.write_text(frontmatter.dumps(post), encoding="utf-8")
        logger.debug("Wrote project index: %s", project)

    def cleanup_orphan_conflicts(self, existing_raw_files: list[Path]) -> int:
        """Remove OPEN conflicts whose source_file no longer exists in raw/.
        RESOLVED conflicts are kept (their skills are already in skills.md).
        Returns number of removed conflicts.
        """
        existing: set[str] = set()
        for p in existing_raw_files:
            rel = p.relative_to(self.raw_dir)
            existing.add(str(rel).replace("\\", "/"))
            existing.add(str(rel))  # backslash variant for Windows

        content = self.read_conflicts_raw()
        parts = re.split(r"\n---\n", content)

        removed = 0
        kept = []
        for part in parts:
            part = part.strip()
            if not part:
                continue

            is_open = "## [OPEN]" in part
            if not is_open:
                kept.append(part)
                continue

            match = re.search(r"- \*\*Source file:\*\* (.+)", part)
            if match:
                source_file = match.group(1).strip().replace("\\", "/")
                if source_file in existing:
                    kept.append(part)
                else:
                    removed += 1
                    logger.info(
                        "Removing orphan conflict for missing raw file: %s",
                        source_file,
                    )
            else:
                kept.append(part)

        if removed > 0:
            new_content = "\n\n---\n\n".join(kept) + "\n"
            path = self.root / "conflicts.md"
            path.write_text(new_content, encoding="utf-8")
            logger.info("Removed %d orphan conflicts", removed)

        return removed

    # ── Internal helpers ─────────────────────────────────────────

    def _slug_to_path(self, slug: str) -> Path:
        validate_slug(slug)
        return self._resolve_in_dir(self.wiki_dir, f"{slug}.md")

    @staticmethod
    def _resolve_in_dir(base_dir: Path, relative_path: str) -> Path:
        """Resolve path and ensure it stays inside base_dir."""
        base = base_dir.resolve()
        target = (base / relative_path).resolve()
        if not target.is_relative_to(base):
            raise ValueError(f"Путь выходит за пределы базовой директории: {relative_path!r}")
        return target

    def _parse_page(self, path: Path, slug: str) -> WikiPage | None:
        try:
            raw = path.read_text(encoding="utf-8")
            post = frontmatter.loads(raw)
            # Convert date objects back to strings to satisfy linter
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

    def _char_limit_for_type(self, page_type: str) -> int:
        mapping = {
            "entity":  self.limits.entity_page_chars,
            "concept": self.limits.concept_page_chars,
            "index":   self.limits.index_l0_chars,
            "log":     self.limits.log_md_chars,
        }
        return mapping.get(page_type, self.limits.entity_page_chars)

    def _update_index_entry(self, slug: str, meta: dict) -> None:
        """Rebuild index.md statistics line. Skips if deferring (batch/rebuild).

        TODO: During bulk rebuild, this still calls list_pages() on every write
        when _defer_index is False. Consider deferring all index updates during
        ingest and rebuilding once at the end.
        """
        if self._defer_index:
            return
        pages = self.list_pages()
        projects = {p.project for p in pages}
        open_conf = self.count_open_conflicts()
        today = date.today().isoformat()

        # Update main L0 index
        index_path = self.wiki_dir / "index.md"
        index_page = self.read_page("index")
        if index_page is None:
            return

        # Update stats block (replaces entire block to avoid accumulation)
        stats_block = (
            f"Last updated: {today}\n"
            f"Pages: {len(pages)} | "
            f"Projects: {len(projects)} | "
            f"Open conflicts: {open_conf}"
        )
        new_content = re.sub(
            r"Last updated:.*?Open conflicts: \d+",
            stats_block,
            index_page.content,
            flags=re.DOTALL,
        )

        # Rebuild project section if project not yet listed
        project = meta.get("project", "_general")
        if f"## {project}" not in new_content:
            new_content += (
                f"\n## {project}\n"
                f"[[{project}/index]] — project {project}\n"
            )

        index_path.write_text(
            frontmatter.dumps(frontmatter.Post(new_content, **index_page.meta)),
            encoding="utf-8"
        )

        # Update per-project L1 index
        proj_pages = [p for p in pages if p.project == project]
        self._write_project_index(project, proj_pages, today)

    def _remove_index_entry(self, slug: str) -> None:
        """Remove specific slug mention from index.md."""
        index_path = self.wiki_dir / "index.md"
        if not index_path.exists():
            return
        content = index_path.read_text(encoding="utf-8")
        content = re.sub(rf"\[\[{re.escape(slug)}[^\]]*\]\][^\n]*\n?", "", content)
        index_path.write_text(content, encoding="utf-8")

    def _archive_resolved_conflicts(self) -> None:
        archive_dir = self.root / "archive"
        archive_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y_%m")
        archive_path = archive_dir / f"conflicts_{ts}.md"
        content = self.read_conflicts_raw()
        resolved = re.findall(r"## \[RESOLVED\].*?(?=## \[|$)", content, re.DOTALL)
        if resolved:
            archive_text = "# Archived Conflicts\n\n" + "\n---\n".join(resolved)
            if archive_path.exists():
                archive_path.write_text(
                    archive_path.read_text(encoding="utf-8") + "\n" + archive_text,
                    encoding="utf-8"
                )
            else:
                archive_path.write_text(archive_text, encoding="utf-8")
            # Remove resolved from active file
            open_only = re.findall(r"## \[OPEN\].*?(?=## \[|$)", content, re.DOTALL)
            new_content = "# Conflicts\n\n" + "\n---\n\n".join(open_only)
            (self.root / "conflicts.md").write_text(new_content, encoding="utf-8")


# ─────────────────────────────────────────────
# Module-level rendering helpers
# ─────────────────────────────────────────────

def _render_conflict_block(entry: ConflictEntry) -> str:
    options_text = "\n".join(
        f"  {i+1}. {opt}"
        for i, opt in enumerate(entry.suggested_options)
    )
    description_line = (
        f"- **Description:** {entry.description}\n"
        if entry.description else ""
    )
    cross_project_line = (
        "- **Cross-project:** true\n"
        if entry.is_cross_project else ""
    )
    return (
        f"## [{entry.status}] {entry.id}\n\n"
        f"- **Date:** {entry.date}\n"
        f"- **Project:** {entry.project}\n"
        f"- **Source file:** {entry.source_file}\n"
        f"- **Conflict type:** {entry.conflict_type}\n"
        f"{cross_project_line}"
        f"- **Page A (wiki):** [[{entry.page_a_slug}]]\n"
        f"- **Page B (source):** {entry.page_b_ref}\n"
        f"{description_line}"
        f"- **Context A (wiki excerpt):**\n\n"
        f"  > {entry.context_a.replace(chr(10), chr(10) + '  > ')}\n\n"
        f"- **Context B (source excerpt):**\n\n"
        f"  > {entry.context_b.replace(chr(10), chr(10) + '  > ')}\n\n"
        f"- **Suggested options:**\n{options_text}\n"
        f"- **User comment:** {entry.user_comment or '_none_'}\n"
        f"- **Resolution:** {entry.resolution}\n"
        f"- **Skill extracted:** {entry.skill_extracted}\n"
        f"- **Resolved at:** {entry.resolved_at}\n"
    )


def _render_log_entry(entry: IngestLog) -> str:
    pages_c = ", ".join(entry.pages_created) or "—"
    pages_u = ", ".join(entry.pages_updated) or "—"
    conflicts = ", ".join(entry.conflicts_detected) or "—"
    return (
        f"- **[{entry.timestamp}]** `ingest` "
        f"| project: `{entry.project}` "
        f"| file: `{entry.source_file}` "
        f"| created: {pages_c} "
        f"| updated: {pages_u} "
        f"| conflicts: {conflicts}\n"
    )


def _inject_after_conflict_id(
    raw: str,
    conflict_id: str,
    text_to_inject: str,
) -> str:
    pattern = rf"(## \[(?:OPEN|RESOLVED)\] {re.escape(conflict_id)}[^\n]*\n)"
    replacement = rf"\1{text_to_inject}\n"
    updated = re.sub(pattern, replacement, raw, count=1)
    if updated == raw:
        updated = raw.rstrip() + f"\n\n{text_to_inject}\n"
    return updated


def _extract_excerpt(content: str, word: str, window: int = 120) -> str:
    idx = content.lower().find(word.lower())
    if idx == -1:
        return content[:window]
    start = max(0, idx - 40)
    end = min(len(content), idx + window - 40)
    excerpt = content[start:end].replace("\n", " ").strip()
    return f"…{excerpt}…" if start > 0 else f"{excerpt}…"


_SKILLS_TEMPLATE = """# Skills

## Source Trust Rules

## Conflict Resolution Patterns

## Domain Conventions

## Query Formatting Rules

## Ingest Patterns
"""


def _apply_ops(page: WikiPage, plan) -> tuple[dict, str]:
    """Apply a PageWritePlan to a WikiPage, returning (meta, content)."""
    from app.core.safe_page_updates import (
        AddProvenanceMarker,
        AppendSection,
        ReplaceSection,
        UpdateFrontmatterField,
    )

    meta = dict(page.meta)
    content = page.content

    for op in plan.operations:
        if isinstance(op, ReplaceSection):
            content = _replace_section(content, op)
        elif isinstance(op, AppendSection):
            content = _append_section(content, op)
        elif isinstance(op, UpdateFrontmatterField):
            meta = _update_fm_field(meta, op)
        elif isinstance(op, AddProvenanceMarker):
            content = _add_prov_marker(content, op)

    meta.setdefault("last_confirmed", date.today().isoformat())
    meta.setdefault("created", page.meta.get("created", date.today().isoformat()))
    return meta, content


def _replace_section(content: str, op) -> str:
    heading_re = re.compile(
        rf"^(#{{1,6}})\s+{re.escape(op.heading)}\s*$",
        re.MULTILINE,
    )
    match = heading_re.search(content)
    if not match:
        raise ValueError(f"Heading not found: '{op.heading}'")

    heading_level = len(match.group(1))
    start = match.end()

    next_heading_re = re.compile(
        rf"^(#{{1,{heading_level}}})\s+",
        re.MULTILINE,
    )
    next_match = next_heading_re.search(content, start)

    if next_match:
        old_section = content[start:next_match.start()]
        new_content = content[:start] + "\n" + op.new_content + "\n\n" + content[next_match.start():]
    else:
        old_section = content[start:]
        new_content = content[:start] + "\n" + op.new_content + "\n"

    if op.preserve_provenance:
        existing_markers = re.findall(r"\^\[([^\]]+)\]", old_section)
        for marker in existing_markers:
            if f"^[{marker}]" not in new_content:
                new_content = new_content.rstrip() + f"\n\n^[{marker}]\n"

    return new_content


def _append_section(content: str, op) -> str:
    heading_re = re.compile(
        rf"^(#{{1,6}})\s+{re.escape(op.heading)}\s*$",
        re.MULTILINE,
    )
    match = heading_re.search(content)

    if not match:
        if op.as_subsection:
            headings = list(re.finditer(r"^(#{1,6})\s+(.+)$", content, re.MULTILINE))
            if headings:
                last = headings[-1]
                insert_pos = last.end()
                next_h = re.search(r"^(#{1,6})\s+", content[insert_pos:], re.MULTILINE)
                if next_h:
                    insert_pos = insert_pos + next_h.start()
                new_heading = f"\n## {op.heading}\n\n{op.content}\n"
                return content[:insert_pos] + new_heading + content[insert_pos:]
            return content + f"\n## {op.heading}\n\n{op.content}\n"
        return content.rstrip() + f"\n\n## {op.heading}\n\n{op.content}\n"

    start = match.end()
    next_heading_re = re.compile(r"^(#{1,6})\s+", re.MULTILINE)
    next_match = next_heading_re.search(content, start)

    if next_match:
        return content[:next_match.start()] + "\n" + op.content + "\n\n" + content[next_match.start():]
    return content.rstrip() + "\n\n" + op.content + "\n"


def _update_fm_field(meta: dict, op) -> dict:
    if op.field_value is None:
        meta.pop(op.field_name, None)
    else:
        meta[op.field_name] = op.field_value
    return meta


def _add_prov_marker(content: str, op) -> str:
    idx = content.find(op.after_text)
    if idx == -1:
        return content.rstrip() + f"\n\n^[{op.source_ref}]\n"
    insert_pos = idx + len(op.after_text)
    marker = f" ^[{op.source_ref}]"
    return content[:insert_pos] + marker + content[insert_pos:]
