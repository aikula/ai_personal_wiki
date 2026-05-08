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

import frontmatter  # python-frontmatter

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
        raise FrontmatterError(f"Отсутствуют обязательные поля frontmatter: {missing}")


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
        self._defer_index = False
        self._ensure_structure()

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
        Returns None if not found.
        """
        if not relative_path:
            raise ValueError("relative_path не может быть пустым")
        path = self._resolve_in_dir(self.raw_dir, relative_path)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

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
        (self.wiki_dir / "log.md").write_text("# Change Log\n\n", encoding="utf-8")
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
            return WikiPage(
                slug=slug,
                path=path,
                meta=dict(post.metadata),
                content=post.content,
                raw=raw,
                char_count=len(raw),
            )
        except Exception as exc:
            logger.warning("Parse error: slug=%s path=%s error=%s", slug, path, exc)
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
        """Rebuild index.md statistics line. Skips if deferring (batch/rebuild)."""
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
    return (
        f"## [{entry.status}] {entry.id}\n\n"
        f"- **Date:** {entry.date}\n"
        f"- **Project:** {entry.project}\n"
        f"- **Source file:** {entry.source_file}\n"
        f"- **Conflict type:** {entry.conflict_type}\n"
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