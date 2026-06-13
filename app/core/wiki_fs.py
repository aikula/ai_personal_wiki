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
from datetime import date
from pathlib import Path

import frontmatter  # python-frontmatter

from app.config import Settings
from app.core.wiki_claims import (
    claim_path as _claim_path_fn,
)
from app.core.wiki_claims import (
    detect_claim_conflicts as _detect_claim_conflicts,
)
from app.core.wiki_claims import (
    find_duplicate_claim as _find_duplicate_claim,
)
from app.core.wiki_claims import (
    get_claims_for_page as _get_claims_for_page,
)
from app.core.wiki_claims import (
    list_claims as _list_claims,
)
from app.core.wiki_claims import (
    read_claim as _read_claim,
)
from app.core.wiki_claims import (
    search_claims as _search_claims,
)
from app.core.wiki_claims import (
    update_claim_status as _update_claim_status,
)
from app.core.wiki_claims import (
    write_claim as _write_claim,
)
from app.core.wiki_cleanup import (
    archive_resolved_conflicts as _archive_resolved_conflicts,
)
from app.core.wiki_cleanup import (
    cleanup_orphan_conflicts as _cleanup_orphan_conflicts,
)
from app.core.wiki_conflicts import (
    append_conflict as _append_conflict,
)
from app.core.wiki_conflicts import (
    clear_open_conflicts as _clear_open_conflicts,
)
from app.core.wiki_conflicts import (
    count_open_conflicts as _count_open_conflicts,
)
from app.core.wiki_conflicts import (
    prepare_conflict_resolution_draft as _prepare_conflict_resolution_draft,
)
from app.core.wiki_conflicts import (
    read_conflicts_raw as _read_conflicts_raw,
)
from app.core.wiki_conflicts import (
    resolve_conflict as _resolve_conflict,
)
from app.core.wiki_drafts import (
    apply_draft as _apply_draft,
)
from app.core.wiki_drafts import (
    clear_all_drafts as _clear_all_drafts,
)
from app.core.wiki_drafts import (
    create_draft as _create_draft,
)
from app.core.wiki_drafts import (
    drafts_dir as _drafts_dir,
)
from app.core.wiki_drafts import (
    list_drafts as _list_drafts,
)
from app.core.wiki_drafts import (
    read_draft as _read_draft,
)
from app.core.wiki_drafts import (
    reject_draft as _reject_draft,
)
from app.core.wiki_fix import fix_broken_wikilinks as _fix_broken_wikilinks
from app.core.wiki_index import (
    bootstrap_index as _bootstrap_index,
)
from app.core.wiki_index import (
    defer_index as _defer_index,
)
from app.core.wiki_index import (
    full_reset_wiki as _full_reset_wiki,
)
from app.core.wiki_index import (
    rebuild_index as _rebuild_index,
)
from app.core.wiki_index import (
    remove_index_entry as _remove_index_entry,
)
from app.core.wiki_index import (
    resume_index as _resume_index,
)
from app.core.wiki_index import (
    update_index_entry as _update_index_entry,
)
from app.core.wiki_index import (
    write_project_index as _write_project_index,
)
from app.core.wiki_log import (
    _LOG_FRONTMATTER,
)
from app.core.wiki_log import (
    append_log as _append_log,
)
from app.core.wiki_log import (
    append_skill as _append_skill,
)
from app.core.wiki_log import (
    read_skills as _read_skills,
)
from app.core.wiki_log import (
    rotate_log as _rotate_log,
)
from app.core.wiki_raw import (
    get_raw_project as _get_raw_project,
)
from app.core.wiki_raw import (
    list_raw_files as _list_raw_files,
)
from app.core.wiki_raw import (
    read_raw_file as _read_raw_file,
)
from app.core.wiki_raw import (
    save_raw_file as _save_raw_file,
)
from app.core.wiki_search import (
    build_link_candidates as _build_link_candidates,
)
from app.core.wiki_search import (
    get_graph_metrics as _get_graph_metrics,
)
from app.core.wiki_search import (
    get_wiki_tree as _get_wiki_tree,
)
from app.core.wiki_search import (
    multi_read_sections as _multi_read_sections,
)
from app.core.wiki_search import (
    read_page_outline as _read_page_outline,
)
from app.core.wiki_search import (
    read_page_section as _read_page_section,
)
from app.core.wiki_search import (
    search_pages as _search_pages,
)
from app.core.wiki_search import (
    search_pages_weighted as _search_pages_weighted,
)
from app.core.wiki_source import (
    check_source_drift as _check_source_drift,
)
from app.core.wiki_source import (
    check_source_state as _check_source_state,
)
from app.core.wiki_source import (
    compute_source_sha256 as _compute_source_sha256,
)
from app.core.wiki_source import (
    list_source_cards as _list_source_cards,
)
from app.core.wiki_source import (
    manifest_path as _manifest_path,
)
from app.core.wiki_source import (
    mark_source_removed as _mark_source_removed,
)
from app.core.wiki_source import (
    read_manifest as _read_manifest,
)
from app.core.wiki_source import (
    read_source_card as _read_source_card,
)
from app.core.wiki_source import (
    sha256 as _sha256,
)
from app.core.wiki_source import (
    source_card_path as _source_card_path,
)
from app.core.wiki_source import (
    update_source_card_drift as _update_source_card_drift,
)
from app.core.wiki_source import (
    update_source_manifest as _update_source_manifest,
)
from app.core.wiki_source import (
    write_manifest as _write_manifest,
)
from app.core.wiki_source import (
    write_source_card as _write_source_card,
)
from app.core.wiki_types import (
    CharLimitExceededError,
    FrontmatterError,  # noqa: F401 — re-exported for tests
    IngestLog,
    PageOutline,
    SectionContent,
    SlugConflictError,
    SourceCard,
    WikiFSError,
    WikiPage,
    _validate_frontmatter,
)
from app.core.wiki_updates import (
    apply_safe_update as _apply_safe_update,
)
from app.core.wiki_updates import (
    generate_update_diff as _generate_update_diff,
)
from app.core.wiki_utils import (
    parse_page as _parse_page_fn,
)
from app.core.wiki_utils import (
    resolve_in_dir as _resolve_in_dir_fn,
)
from app.core.wiki_utils import (
    slug_to_path as _slug_to_path_fn,
)

logger = logging.getLogger("wiki.fs")


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
            self._bootstrap_log()

    def _bootstrap_log(self) -> None:
        if not (self.wiki_dir / "log.md").exists():
            today = date.today().isoformat()
            (self.wiki_dir / "log.md").write_text(
                _LOG_FRONTMATTER.format(today=today),
                encoding="utf-8",
            )

    def _bootstrap_index(self) -> None:
        _bootstrap_index(self)

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
            if slug.startswith(("_claims/", "_sources/")) or slug.startswith("."):
                continue
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
        """Return nested dict representing wiki directory structure."""
        return _get_wiki_tree(self)

    def build_link_candidates(self, project: str | None = None) -> list[dict]:
        """Build compact list of known wiki pages for linker prompt injection."""
        return _build_link_candidates(self, project)

    def get_graph_metrics(self) -> dict:
        """Compute graph-level metrics for the wiki."""
        return _get_graph_metrics(self)

    def search_pages(self, query: str, project: str | None = None, projects: list[str] | None = None) -> list[dict]:
        """Full-text grep search across wiki pages."""
        return _search_pages(self, query, project, projects)

    def search_pages_weighted(
        self,
        query: str,
        project: str | None = None,
        projects: list[str] | None = None,
        top_k: int = 20,
    ) -> list[dict]:
        """Weighted field search across wiki pages."""
        return _search_pages_weighted(self, query, project, projects, top_k)

    def read_page_outline(self, slug: str) -> PageOutline | None:
        """Return structured outline of a wiki page."""
        return _read_page_outline(self, slug)

    def read_page_section(
        self,
        slug: str,
        heading: str,
        char_limit: int | None = None,
    ) -> SectionContent | None:
        """Return full text of a specific section identified by heading text."""
        return _read_page_section(self, slug, heading, char_limit)

    def multi_read_sections(
        self,
        requests: list[dict],
    ) -> list[SectionContent | None]:
        """Batch-read multiple sections from multiple pages."""
        return _multi_read_sections(self, requests)

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
        new_raw: str,
        reason: str,
    ) -> tuple[bool, str | None]:
        """Apply raw content to a wiki page and return (success, diff_text)."""
        return _apply_safe_update(self, slug, new_raw, reason)

    def generate_update_diff(self, slug: str, new_raw: str) -> str | None:
        """Generate a unified diff between current and proposed raw content."""
        return _generate_update_diff(self, slug, new_raw)

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
        return _list_raw_files(self, project)

    def read_raw_file(self, relative_path: str) -> str | None:
        return _read_raw_file(self, relative_path)

    def save_raw_file(self, project: str, filename: str, content: str) -> Path:
        return _save_raw_file(self, project, filename, content)

    def get_raw_project(self, raw_path: Path) -> str:
        return _get_raw_project(self, raw_path)

    # ── Source manifest (state) ────────────────────────────────────

    def _manifest_path(self) -> Path:
        return _manifest_path(self)

    def _read_manifest(self) -> dict:
        return _read_manifest(self)

    def _write_manifest(self, manifest: dict) -> None:
        _write_manifest(self, manifest)

    def _sha256(self, content: str) -> str:
        return _sha256(self, content)

    def check_source_state(self, relative_path: str, content: str) -> dict:
        return _check_source_state(self, relative_path, content)

    def update_source_manifest(self, relative_path: str, content: str) -> None:
        _update_source_manifest(self, relative_path, content)

    def mark_source_removed(self, relative_path: str) -> None:
        _mark_source_removed(self, relative_path)

    # ── Source Card operations ────────────────────────────────────

    def compute_source_sha256(self, content: str) -> str:
        return _compute_source_sha256(self, content)

    def _source_card_path(self, source_id: str) -> Path:
        return _source_card_path(self, source_id)

    def write_source_card(self, card: SourceCard) -> None:
        _write_source_card(self, card)

    def read_source_card(self, source_id: str) -> SourceCard | None:
        return _read_source_card(self, source_id)

    def list_source_cards(self, project: str | None = None) -> list[SourceCard]:
        return _list_source_cards(self, project)

    def check_source_drift(self, relative_path: str) -> dict:
        return _check_source_drift(self, relative_path)

    def update_source_card_drift(self, source_id: str, drift_status: str) -> None:
        _update_source_card_drift(self, source_id, drift_status)

    # ── Claim operations ─────────────────────────────────────────

    def claim_path(self, claim) -> Path:
        return _claim_path_fn(self, claim)

    def write_claim(self, claim) -> Path:
        return _write_claim(self, claim)

    def read_claim(self, claim_id: str, project: str, source_id: str, chunk_id: str):
        return _read_claim(self, claim_id, project, source_id, chunk_id)

    def list_claims(self, project=None, status=None, source_id=None):
        return _list_claims(self, project=project, status=status, source_id=source_id)

    def search_claims(self, query: str, project: str | None = None, top_k: int = 10):
        return _search_claims(self, query, project=project, top_k=top_k)

    def find_duplicate_claim(self, normalized: str, source_id: str):
        return _find_duplicate_claim(self, normalized, source_id)

    def update_claim_status(self, claim_id: str, project: str, source_id: str, chunk_id: str, new_status: str) -> bool:
        return _update_claim_status(self, claim_id, project, source_id, chunk_id, new_status)

    def get_claims_for_page(self, slug: str):
        return _get_claims_for_page(self, slug)

    def detect_claim_conflicts(self, source_id: str):
        return _detect_claim_conflicts(self, source_id)

    # ── Conflicts operations ─────────────────────────────────────

    def read_conflicts_raw(self) -> str:
        return _read_conflicts_raw(self)

    def append_conflict(self, entry) -> None:
        return _append_conflict(self, entry)

    def resolve_conflict(
        self,
        conflict_id: str,
        resolution: str,
        user_comment: str,
        skill_extracted: str = "",
    ) -> bool:
        return _resolve_conflict(self, conflict_id, resolution, user_comment, skill_extracted)

    def prepare_conflict_resolution_draft(
        self,
        conflict_id: str,
        resolution: str,
        user_comment: str = "",
    ) -> dict | None:
        return _prepare_conflict_resolution_draft(self, conflict_id, resolution, user_comment)

    def count_open_conflicts(self) -> int:
        return _count_open_conflicts(self)

    def clear_open_conflicts(self) -> int:
        return _clear_open_conflicts(self)

    # ── Skills operations ────────────────────────────────────────

    def read_skills(self) -> str:
        return _read_skills(self)

    def append_skill(self, section: str, skill_text: str) -> None:
        _append_skill(self, section, skill_text)

    # ── Log operations ───────────────────────────────────────────

    def append_log(self, entry: IngestLog) -> None:
        _append_log(self, entry)

    def _rotate_log(self, content: str) -> None:
        _rotate_log(self, content)

    # ── Draft operations ──────────────────────────────────────────

    @property
    def drafts_dir(self) -> Path:
        return _drafts_dir(self)

    def create_draft(
        self,
        draft_id: str,
        plan: dict,
        pages: dict[str, str],
        conflicts: list[dict],
    ) -> None:
        _create_draft(self, draft_id, plan, pages, conflicts)

    def list_drafts(self) -> list[dict]:
        return _list_drafts(self)

    def read_draft(self, draft_id: str) -> dict | None:
        return _read_draft(self, draft_id)

    def apply_draft(self, draft_id: str) -> list[str]:
        return _apply_draft(self, draft_id)

    def reject_draft(self, draft_id: str) -> bool:
        return _reject_draft(self, draft_id)

    # ── Rebuild operation ────────────────────────────────────────

    def full_reset_wiki(self) -> None:
        """Remove and re-bootstrap the entire wiki directory."""
        _full_reset_wiki(self)

    def clear_all_drafts(self) -> int:
        return _clear_all_drafts(self)

    def defer_index(self) -> None:
        _defer_index(self)

    def resume_index(self) -> None:
        _resume_index(self)

    def rebuild_index(self) -> None:
        _rebuild_index(self)

    def _write_project_index(self, project: str, pages: list, today: str) -> None:
        _write_project_index(self, project, pages, today)

    def cleanup_orphan_conflicts(self, existing_raw_files: list[Path]) -> int:
        """Remove OPEN conflicts whose source_file no longer exists in raw/."""
        return _cleanup_orphan_conflicts(self, existing_raw_files)

    def fix_broken_wikilinks(self, project: str | None = None) -> int:
        """Remove broken [[wikilinks]] from all pages in the given project (or all projects)."""
        return _fix_broken_wikilinks(self, project)

    # ── Internal helpers ─────────────────────────────────────────

    def _slug_to_path(self, slug: str) -> Path:
        return _slug_to_path_fn(self, slug)

    @staticmethod
    def _resolve_in_dir(base_dir: Path, relative_path: str) -> Path:
        return _resolve_in_dir_fn(base_dir, relative_path)

    def _parse_page(self, path: Path, slug: str) -> WikiPage | None:
        return _parse_page_fn(self, path, slug)

    def _char_limit_for_type(self, page_type: str) -> int:
        mapping = {
            "entity":  self.limits.entity_page_chars,
            "concept": self.limits.concept_page_chars,
            "index":   self.limits.index_l0_chars,
            "log":     self.limits.log_md_chars,
        }
        return mapping.get(page_type, self.limits.entity_page_chars)

    def _update_index_entry(self, slug: str, meta: dict) -> None:
        _update_index_entry(self, slug, meta)

    def _remove_index_entry(self, slug: str) -> None:
        _remove_index_entry(self, slug)

    def _archive_resolved_conflicts(self) -> None:
        _archive_resolved_conflicts(self)


# ─────────────────────────────────────────────
# Module-level rendering helpers
# ─────────────────────────────────────────────


_SKILLS_TEMPLATE = """# Skills

## Source Trust Rules

## Conflict Resolution Patterns

## Domain Conventions

## Query Formatting Rules

## Ingest Patterns
"""


