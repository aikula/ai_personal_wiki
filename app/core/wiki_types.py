"""
wiki_types.py — Data models and types for WikiFS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.core.utils import (
    extract_wikilinks,
    heading_to_anchor,
)

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
# Constants
# ─────────────────────────────────────────────

REQUIRED_FRONTMATTER = {
    "title", "project", "type", "tags",
    "confidence", "sources", "last_confirmed",
    "supersedes", "superseded_by", "created",
}

PAGE_TYPES = {"entity", "concept", "index", "log", "source"}
PROJECT_TYPES = frozenset()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


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


# ─────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────


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
