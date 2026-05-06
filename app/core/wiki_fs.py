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

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import frontmatter  # python-frontmatter

from app.config import Settings

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


# ─────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────

REQUIRED_FRONTMATTER = {
    "title", "project", "type", "tags",
    "confidence", "sources", "last_confirmed",
    "supersedes", "superseded_by", "created",
}

PAGE_TYPES = {"entity", "concept", "index", "log"}
PROJECT_TYPES = frozenset()


def _validate_frontmatter(meta: dict) -> None:
    missing = REQUIRED_FRONTMATTER - set(meta.keys())
    if missing:
        raise FrontmatterError(f"Missing required frontmatter fields: {missing}")


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
        """Extract all [[slug]] and [[slug|text]] from content."""
        return re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]", self.content)

    @property
    def anchors(self) -> set[str]:
        """All heading-based anchors in this page."""
        headings = re.findall(r"^#{1,6}\s+(.+)$", self.content, re.MULTILINE)
        return {self._heading_to_anchor(h) for h in headings}

    @staticmethod
    def _heading_to_anchor(heading: str) -> str:
        anchor = heading.lower().strip()
        anchor = re.sub(r"[`*_\[\]()]", "", anchor)
        anchor = re.sub(r"[^\w\s-]", "", anchor)
        anchor = re.sub(r"\s+", "-", anchor)
        return anchor.strip("-")


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
    context_a: str             # first 300 chars of wiki page
    context_b: str             # first 300 chars of source
    suggested_options: list[str]
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


# ─────────────────────────────────────────────
# WikiFS
# ─────────────────────────────────────────────

class WikiFS:
    """
    All filesystem operations for wiki-data/.
    Instantiate once per request or inject as singleton.

    Usage:
        fs = WikiFS(settings)
        page = fs.read_page("myapp/deploy")
        fs.write_page("myapp/deploy", meta=..., content=...)
    """

    def __init__(self, settings: Settings):
        self.root = Path(settings.wiki_data_path)
        self.wiki_dir = self.root / "wiki"
        self.raw_dir = self.root / "raw"
        self.limits = settings.limits
        self._ensure_structure()

    # ── Initialisation ──────────────────────────────────────────

    def _ensure_structure(self) -> None:
        """Create required directories if they don't exist."""
        for d in [
            self.wiki_dir,
            self.raw_dir / "_general",
            self.root,
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

    def search_pages(self, query: str, project: str | None = None) -> list[dict]:
        """
        Full-text grep search across wiki pages.
        Returns list of {slug, title, excerpt, score} sorted by score desc.
        score = number of query word matches in content.
        """
        words = query.lower().split()
        results = []

        for page in self.list_pages(project=project):
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
            raise SlugConflictError(f"Page already exists: {slug}")

        _validate_frontmatter(meta)

        if not meta.get("last_confirmed"):
            meta["last_confirmed"] = date.today().isoformat()

        post = frontmatter.Post(content, **meta)
        raw = frontmatter.dumps(post)

        limit = self._char_limit_for_type(meta.get("type", "entity"))
        if len(raw) > limit:
            raise CharLimitExceededError(path, len(raw), limit)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")

        self._update_index_entry(slug, meta)

        action = "updated" if allow_overwrite and path.exists() else "created"
        logger.info("Page %s: slug=%s chars=%d", action, slug, len(raw))

        return self._parse_page(path, slug)

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
            raise WikiFSError(f"Cannot supersede non-existent page: {old_slug}")
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
            target = self.raw_dir / project
            if not target.exists():
                return []
            return sorted(target.rglob("*.md"))
        return sorted(self.raw_dir.rglob("*.md"))

    def read_raw_file(self, relative_path: str) -> str | None:
        """
        Read raw source file. relative_path from raw/ root.
        e.g. "myapp/deploy_guide.md"
        Returns None if not found.
        """
        path = self.raw_dir / relative_path
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def save_raw_file(self, project: str, filename: str, content: str) -> Path:
        target_dir = self.raw_dir / project
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        target.write_text(content, encoding="utf-8")
        logger.info("Raw file saved: path=%s/%s chars=%d", project, filename, len(content))
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
        return True

    def count_open_conflicts(self) -> int:
        content = self.read_conflicts_raw()
        return content.count("## [OPEN]")

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
            # Insert after section header
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

    # ── Rebuild operation ────────────────────────────────────────

    def full_reset_wiki(self) -> None:
        if self.wiki_dir.exists():
            shutil.rmtree(self.wiki_dir)
            logger.info("Wiki directory removed: %s", self.wiki_dir)
        self.wiki_dir.mkdir()
        self._bootstrap_index()
        (self.wiki_dir / "log.md").write_text("# Change Log\n\n", encoding="utf-8")
        logger.info("Wiki directory re-bootstrapped: %s", self.wiki_dir)

    # ── Internal helpers ─────────────────────────────────────────

    def _slug_to_path(self, slug: str) -> Path:
        return self.wiki_dir / f"{slug}.md"

    def _parse_page(self, path: Path, slug: str) -> WikiPage | None:
        try:
            raw = path.read_text(encoding="utf-8")
            post = frontmatter.loads(raw)
            return WikiPage(
                slug=slug,
                path=path,
                meta=dict(post.metadata),
                content=post.content,
                raw=raw,
                char_count=len(raw),
            )
        except Exception:
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
        """Rebuild index.md statistics line. Full rebuild is cheap at Phase 1."""
        pages = self.list_pages()
        projects = {p.project for p in pages}
        open_conf = self.count_open_conflicts()
        today = date.today().isoformat()

        index_path = self.wiki_dir / "index.md"
        index_page = self.read_page("index")
        if index_page is None:
            return

        # Update stats line
        new_content = re.sub(
            r"Last updated:.*",
            f"Last updated: {today}\n"
            f"Pages: {len(pages)} | "
            f"Projects: {len(projects)} | "
            f"Open conflicts: {open_conf}",
            index_page.content,
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
    return (
        f"## [{entry.status}] {entry.id}\n\n"
        f"- **Date:** {entry.date}\n"
        f"- **Project:** {entry.project}\n"
        f"- **Source file:** {entry.source_file}\n"
        f"- **Conflict type:** {entry.conflict_type}\n"
        f"- **Page A:** [[{entry.page_a_slug}]]\n"
        f"- **Page B:** {entry.page_b_ref}\n"
        f"- **Context A:** {entry.context_a}\n"
        f"- **Context B:** {entry.context_b}\n"
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