"""
linter.py — Structural wiki linter.

Checks (all in-process, no LLM, no I/O beyond reading):
  1. broken_wikilink    — [[slug]] references non-existent page
  2. broken_path_link   — [text](path.md) file not found
  3. missing_anchor     — [[slug#anchor]] anchor not in target page
  4. orphan_page        — page has no incoming wikilinks
  5. missing_frontmatter — required field absent or wrong type
  6. char_limit         — page exceeds limit for its type
  7. superseded_active  — superseded page still linked from others
  8. stale_page         — confidence < threshold AND last_confirmed > N days
  9. duplicate_title    — two pages with same title in same project
 10. missing_wikilink   — known alias appears without [[link]]
 11. invalid_provenance — ^[raw/...] marker references non-existent raw file

LLM checks (audit_agent, not here):
  - factual contradictions
  - duplicate content
  - semantic inconsistencies
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from app.config import Settings
from app.core.wiki_fs import WikiFS, WikiPage

# ─────────────────────────────────────────────
# Result models
# ─────────────────────────────────────────────

ISSUE_KINDS = {
    "broken_wikilink",
    "broken_path_link",
    "missing_anchor",
    "orphan_page",
    "missing_frontmatter",
    "char_limit",
    "superseded_active",
    "stale_page",
    "duplicate_title",
    "missing_wikilink",
    "invalid_provenance",
}


@dataclass
class LintIssue:
    slug: str            # affected page slug
    line: int            # 0 if not line-specific
    kind: str            # one of ISSUE_KINDS
    detail: str          # human-readable description
    severity: str        # "error" | "warning" | "info"
    fix_hint: str = ""   # what agent should do to fix

    def __str__(self) -> str:
        loc = f"{self.slug}:{self.line}" if self.line else self.slug
        return f"[{self.severity.upper()}] {loc} — {self.detail}"


@dataclass
class LintReport:
    ran_at: str
    total_pages: int
    issues: list[LintIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def by_kind(self) -> dict[str, list[LintIssue]]:
        result: dict[str, list[LintIssue]] = {}
        for issue in self.issues:
            result.setdefault(issue.kind, []).append(issue)
        return result

    @property
    def is_clean(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        if self.is_clean:
            return f"✓ Wiki clean. {self.total_pages} pages checked."
        return (
            f"✗ {len(self.errors)} errors, {len(self.warnings)} warnings "
            f"in {self.total_pages} pages."
        )


# ─────────────────────────────────────────────
# WikiLinter
# ─────────────────────────────────────────────

class WikiLinter:
    """
    Instantiate with WikiFS, call .lint() to get LintReport.
    All checks are pure reads — no writes, no LLM calls.

    Example:
        linter = WikiLinter(wiki_fs, settings)
        report = linter.lint()
        if not report.is_clean:
            for issue in report.errors:
                print(issue)
    """

    def __init__(self, fs: WikiFS, settings: Settings):
        self.fs = fs
        self.settings = settings
        self._pages: dict[str, WikiPage] = {}      # slug → page
        self._incoming: dict[str, set[str]] = {}   # slug → set of slugs linking to it

    def lint(self, slugs: list[str] | None = None) -> LintReport:
        """
        Run all structural checks.
        slugs: if provided, check only these pages (for incremental lint).
               if None, check entire wiki.
        """
        all_pages = self.fs.list_pages()
        self._pages = {p.slug: p for p in all_pages}
        self._build_incoming_links()

        target_pages = (
            [self._pages[s] for s in slugs if s in self._pages]
            if slugs else all_pages
        )

        issues: list[LintIssue] = []

        for page in target_pages:
            issues += self._check_frontmatter(page)
            issues += self._check_char_limit(page)
            issues += self._check_wikilinks(page)
            issues += self._check_path_links(page)
            issues += self._check_superseded_active(page)
            issues += self._check_stale(page)
            issues += self._check_missing_wikilinks(page)
            issues += self._check_provenance(page)

        # Global checks (always run for full picture)
        issues += self._check_orphans(target_pages)
        issues += self._check_duplicate_titles(target_pages)

        return LintReport(
            ran_at=datetime.now().isoformat(timespec="seconds"),
            total_pages=len(target_pages),
            issues=sorted(issues, key=lambda i: (i.severity, i.slug)),
        )

    # ── Per-page checks ──────────────────────────────────────────

    def _check_frontmatter(self, page: WikiPage) -> list[LintIssue]:
        issues = []
        # supersedes/superseded_by are nullable — validated separately
        # in _check_superseded_active, not here
        required = {
            "title": str,
            "project": str,
            "type": str,
            "confidence": (int, float),
            "sources": int,
            "last_confirmed": str,
            "created": str,
        }
        for field_name, expected_type in required.items():
            if field_name not in page.meta:
                issues.append(LintIssue(
                    slug=page.slug, line=0,
                    kind="missing_frontmatter",
                    detail=f"Missing required field: '{field_name}'",
                    severity="error",
                    fix_hint=f"Add '{field_name}' to frontmatter",
                ))
            elif not isinstance(page.meta[field_name], expected_type):
                issues.append(LintIssue(
                    slug=page.slug, line=0,
                    kind="missing_frontmatter",
                    detail=f"Field '{field_name}' has wrong type: "
                           f"expected {expected_type}, "
                           f"got {type(page.meta[field_name]).__name__}",
                    severity="error",
                    fix_hint=f"Fix type of '{field_name}' in frontmatter",
                ))
        if page.meta.get("type") not in {"entity", "concept", "index", "log", None}:
            issues.append(LintIssue(
                slug=page.slug, line=0,
                kind="missing_frontmatter",
                detail=f"Unknown page type: '{page.meta.get('type')}'",
                severity="warning",
                fix_hint="Use one of: entity, concept, index, log",
            ))
        return issues

    def _check_char_limit(self, page: WikiPage) -> list[LintIssue]:
        limit_map = {
            "entity":  self.settings.limits.entity_page_chars,
            "concept": self.settings.limits.concept_page_chars,
            "index":   self.settings.limits.index_l0_chars,
            "log":     self.settings.limits.log_md_chars,
        }
        page_type = page.page_type
        limit = limit_map.get(page_type, self.settings.limits.entity_page_chars)
        if page.char_count > limit:
            over = page.char_count - limit
            return [LintIssue(
                slug=page.slug, line=0,
                kind="char_limit",
                detail=f"{page.char_count} chars, limit {limit} (+{over} over)",
                severity="warning",
                fix_hint="Split into two pages at a semantic boundary",
            )]
        return []

    def _check_wikilinks(self, page: WikiPage) -> list[LintIssue]:
        issues = []
        lines = page.content.splitlines()
        wikilink_re = re.compile(r"\[\[([^\]|#]+)(?:(#)([^\]|]+))?(?:\|[^\]]+)?\]\]")

        for lineno, line in enumerate(lines, 1):
            for match in wikilink_re.finditer(line):
                target_slug = match.group(1).strip()
                anchor = match.group(3)

                if target_slug not in self._pages:
                    issues.append(LintIssue(
                        slug=page.slug, line=lineno,
                        kind="broken_wikilink",
                        detail=f"[[{target_slug}]] — target page not found",
                        severity="error",
                        fix_hint=f"Create page '{target_slug}' or fix the slug",
                    ))
                elif anchor:
                    target_page = self._pages[target_slug]
                    if anchor not in target_page.anchors:
                        issues.append(LintIssue(
                            slug=page.slug, line=lineno,
                            kind="missing_anchor",
                            detail=f"[[{target_slug}#{anchor}]] — anchor not found. "
                                   f"Available: {sorted(target_page.anchors)}",
                            severity="warning",
                            fix_hint="Fix anchor name or add heading to target page",
                        ))
        return issues

    def _check_path_links(self, page: WikiPage) -> list[LintIssue]:
        """Check [text](relative/path.md) style links."""
        issues = []
        lines = page.content.splitlines()
        path_link_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

        for lineno, line in enumerate(lines, 1):
            for match in path_link_re.finditer(line):
                href = match.group(2)
                # Skip external links, anchors-only, and mailto
                if href.startswith(("http", "mailto", "#")):
                    continue
                target = (page.path.parent / href).resolve()
                if not target.exists():
                    issues.append(LintIssue(
                        slug=page.slug, line=lineno,
                        kind="broken_path_link",
                        detail=f"[{match.group(1)}]({href}) — file not found",
                        severity="error",
                        fix_hint="Fix relative path or convert to [[wikilink]] style",
                    ))
        return issues

    def _check_superseded_active(self, page: WikiPage) -> list[LintIssue]:
        """Warn if a superseded page is still being linked."""
        if not page.meta.get("superseded_by"):
            return []
        incoming = self._incoming.get(page.slug, set())
        if incoming:
            return [LintIssue(
                slug=page.slug, line=0,
                kind="superseded_active",
                detail=f"Page is superseded by [[{page.meta['superseded_by']}]] "
                       f"but still linked from: {sorted(incoming)}",
                severity="warning",
                fix_hint=f"Update links to point to [[{page.meta['superseded_by']}]]",
            )]
        return []

    def _check_stale(self, page: WikiPage) -> list[LintIssue]:
        """Flag low-confidence old pages for review."""
        conf = page.confidence
        threshold_conf = self.settings.audit.confidence_warn_threshold
        threshold_days = self.settings.audit.stale_days_threshold

        try:
            last = date.fromisoformat(str(page.meta.get("last_confirmed", "")))
            days_old = (date.today() - last).days
        except (ValueError, TypeError):
            days_old = 0

        if conf < threshold_conf and days_old > threshold_days:
            return [LintIssue(
                slug=page.slug, line=0,
                kind="stale_page",
                detail=f"confidence={conf}, last_confirmed {days_old} days ago",
                severity="info",
                fix_hint="Re-confirm facts or update sources",
            )]
        return []

    def _check_missing_wikilinks(self, page: WikiPage) -> list[LintIssue]:
        """Flag known page titles/aliases that appear as plain text (not linked)."""
        candidates = self._get_link_candidates()
        issues = []
        page_text_lower = page.content.lower()

        for c in candidates:
            if c["slug"] == page.slug:
                continue
            for alias in c.get("aliases", []):
                if len(alias) < 4:
                    continue
                alias_lower = alias.lower()
                if alias_lower not in page_text_lower:
                    continue
                # Check not already linked
                if f"[[{c['slug']}" in page.content:
                    continue
                # Skip index/log pages
                if page.page_type in ("index", "log"):
                    continue
                # Avoid flagging on overly generic aliases
                if alias_lower in ("page", "service", "api", "app", "config"):
                    continue
                issues.append(LintIssue(
                    slug=page.slug, line=0,
                    kind="missing_wikilink",
                    detail=f"'{alias}' appears but [[{c['slug']}]] is not linked",
                    severity="info",
                    fix_hint=f"Add [[{c['slug']}|{alias}]] on first mention",
                ))
                break  # one match per candidate

        return issues

    def _get_link_candidates(self) -> list[dict]:
        if not hasattr(self, "_candidates_cache"):
            self._candidates_cache = self.fs.build_link_candidates()
        return self._candidates_cache

    def _check_provenance(self, page: WikiPage) -> list[LintIssue]:
        """Validate ^[raw/...] provenance markers reference existing raw files."""
        issues = []
        markers = re.findall(r"\^\[raw/([^\]]+)\]", page.content)
        for ref in markers:
            raw_path = self.fs.raw_dir / ref
            if not raw_path.exists():
                issues.append(LintIssue(
                    slug=page.slug, line=0,
                    kind="invalid_provenance",
                    detail=f"Provenance marker references non-existent raw file: raw/{ref}",
                    severity="warning",
                    fix_hint=f"Remove or correct the provenance marker ^[raw/{ref}]",
                ))
        return issues

    # ── Global checks ────────────────────────────────────────────

    def _check_orphans(self, pages: list[WikiPage]) -> list[LintIssue]:
        """Pages with no incoming wikilinks (index and log excluded)."""
        issues = []
        excluded_types = {"index", "log"}
        excluded_slugs = {"index"}

        for page in pages:
            if page.page_type in excluded_types:
                continue
            if page.slug in excluded_slugs:
                continue
            if not self._incoming.get(page.slug):
                issues.append(LintIssue(
                    slug=page.slug, line=0,
                    kind="orphan_page",
                    detail="No pages link to this page",
                    severity="info",
                    fix_hint="Add [[link]] from a related page or index",
                ))
        return issues

    def _check_duplicate_titles(self, pages: list[WikiPage]) -> list[LintIssue]:
        """Two pages with same title within same project."""
        seen: dict[tuple[str, str], str] = {}  # (project, title) → slug
        issues = []
        for page in pages:
            key = (page.project, page.title.lower().strip())
            if key in seen:
                issues.append(LintIssue(
                    slug=page.slug, line=0,
                    kind="duplicate_title",
                    detail=f"Same title as [[{seen[key]}]] in project '{page.project}'",
                    severity="warning",
                    fix_hint="Differentiate titles or merge the pages",
                ))
            else:
                seen[key] = page.slug
        return issues

    # ── Helpers ──────────────────────────────────────────────────

    def _build_incoming_links(self) -> None:
        """Build reverse link index: slug → set of slugs that link to it."""
        self._incoming = {slug: set() for slug in self._pages}
        for page in self._pages.values():
            if page.page_type == "index":  # skip index pages (L0/L1)
                continue
            for linked_slug in page.wikilinks:
                if linked_slug in self._incoming:
                    self._incoming[linked_slug].add(page.slug)


# ─────────────────────────────────────────────
# Helpers (module-level)
# ─────────────────────────────────────────────


def _extract_excerpt(content: str, word: str, window: int = 120) -> str:
    idx = content.lower().find(word.lower())
    if idx == -1:
        return content[:window]
    start = max(0, idx - 40)
    end = min(len(content), idx + window - 40)
    excerpt = content[start:end].replace("\n", " ").strip()
    return f"…{excerpt}…" if start > 0 else f"{excerpt}…"