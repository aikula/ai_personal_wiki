"""
linter.py — Structural wiki linter.

Checks (all in-process, no LLM, no writes):
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
 12. source_drift       — Source Card raw file changed since ingest
 13. missing_source     — Source Card raw file no longer exists
 14. orphan_source_card — Source Card has no written pages
 15. orphan_claim       — active claim has no related wiki pages
 16. claim_without_source_card — claim source card is missing
 17. contradicted_claim_still_active — contradicted claim is still active
 18. planned_page_not_created — Source Card lists planned page that was never written

LLM checks (audit_agent, not here):
  - factual contradictions
  - duplicate content
  - semantic inconsistencies
"""

from __future__ import annotations

import re
from datetime import date, datetime

from app.config import Settings
from app.core.linter_models import LintIssue, LintReport
from app.core.wiki_fs import WikiFS, WikiPage

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
        issues += self._check_source_drift()
        issues += self._check_claims()
        issues += self._check_planned_pages()

        return LintReport(
            ran_at=datetime.now().isoformat(timespec="seconds"),
            total_pages=len(target_pages),
            issues=sorted(issues, key=lambda i: (i.severity, i.slug)),
        )

    # ── Per-page checks ──────────────────────────────────────────

    def _check_frontmatter(self, page: WikiPage) -> list[LintIssue]:
        issues = []
        required = {
            "title": str,
            "project": str,
            "type": str,
            "tags": list,
            "confidence": (int, float),
            "sources": int,
            "last_confirmed": str,
            "supersedes": (str, type(None)),
            "superseded_by": (str, type(None)),
            "created": str,
        }
        for field_name, expected_type in required.items():
            if field_name not in page.meta:
                issues.append(LintIssue(
                    slug=page.slug, line=0,
                    kind="missing_frontmatter",
                    detail=f"Отсутствует обязательное поле: '{field_name}'",
                    severity="error",
                    fix_hint=f"Добавьте '{field_name}' в frontmatter",
                ))
            elif not isinstance(page.meta[field_name], expected_type):
                issues.append(LintIssue(
                    slug=page.slug, line=0,
                    kind="missing_frontmatter",
                    detail=f"Поле '{field_name}' имеет неверный тип: "
                           f"ожидался {_type_name(expected_type)}, "
                           f"получен {type(page.meta[field_name]).__name__}",
                    severity="error",
                    fix_hint=f"Исправьте тип '{field_name}' в frontmatter",
                ))
        if page.meta.get("type") not in {"entity", "concept", "index", "log", "source", None}:
            issues.append(LintIssue(
                slug=page.slug, line=0,
                kind="missing_frontmatter",
                detail=f"Неизвестный тип страницы: '{page.meta.get('type')}'",
                severity="warning",
                fix_hint="Используйте один из: entity, concept, index, log",
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
                detail=f"{page.char_count} символов, лимит {limit} (+{over} сверх лимита)",
                severity="warning",
                fix_hint="Разделите на две страницы по смысловой границе",
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
                        detail=f"[[{target_slug}]] — целевая страница не найдена",
                        severity="error",
                        fix_hint=f"Создайте страницу '{target_slug}' или исправьте ссылку",
                    ))
                elif anchor:
                    target_page = self._pages[target_slug]
                    if anchor not in target_page.anchors:
                        issues.append(LintIssue(
                            slug=page.slug, line=lineno,
                            kind="missing_anchor",
                            detail=f"[[{target_slug}#{anchor}]] — якорь не найден. "
                                   f"Доступные: {sorted(target_page.anchors)}",
                            severity="warning",
                            fix_hint="Исправьте имя якоря или добавьте заголовок в целевую страницу",
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
                        detail=f"[{match.group(1)}]({href}) — файл не найден",
                        severity="error",
                        fix_hint="Исправьте относительный путь или преобразуйте в [[wikilink]]",
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
                detail=f"Страница заменена на [[{page.meta['superseded_by']}]] "
                       f"но всё ещё связана с: {sorted(incoming)}",
                severity="warning",
                fix_hint=f"Обновите ссылки на [[{page.meta['superseded_by']}]]",
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
                detail=f"уверенность={conf}, last_confirmed {days_old} дн. назад",
                severity="info",
                fix_hint="Перепроверьте факты или обновите источники",
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
                    detail=f"'{alias}' встречается, но [[{c['slug']}]] не связана",
                    severity="info",
                    fix_hint=f"Добавьте [[{c['slug']}|{alias}]] при первом упоминании",
                ))
                break  # one match per candidate

        return issues

    def _get_link_candidates(self) -> list[dict]:
        if not hasattr(self, "_candidates_cache"):
            self._candidates_cache = self.fs.build_link_candidates()
        return self._candidates_cache

    def _check_provenance(self, page: WikiPage) -> list[LintIssue]:
        """Проверка provenance-маркеров ^[raw/...] на существование raw-файлов."""
        issues = []
        markers = re.findall(r"\^\[raw/([^\]]+)\]", page.content)
        for ref in markers:
            raw_path = self.fs.raw_dir / ref
            if not raw_path.exists():
                issues.append(LintIssue(
                    slug=page.slug, line=0,
                    kind="invalid_provenance",
                    detail=f"Provenance-маркер ссылается на несуществующий raw-файл: raw/{ref}",
                    severity="warning",
                    fix_hint=f"Удалите или исправьте provenance-маркер ^[raw/{ref}]",
                ))
        # Проверка provenance-маркеров без префикса raw/
        bad_markers = re.findall(r"\^\[(?!raw/)([^\]]+)\]", page.content)
        for ref in bad_markers:
            issues.append(LintIssue(
                slug=page.slug, line=0,
                kind="invalid_provenance",
                detail=f"Provenance-маркер без префикса raw/: ^[{ref}] (нужно ^[raw/{ref}])",
                severity="warning",
                fix_hint=f"Замените ^[{ref}] на ^[raw/{ref}]",
            ))
        return issues

    # ── Global checks ────────────────────────────────────────────

    def _check_orphans(self, pages: list[WikiPage]) -> list[LintIssue]:
        """Страницы без входящих wikilinks (index и log исключены)."""
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
                    detail="Ни одна страница не ссылается на эту",
                    severity="info",
                    fix_hint="Добавьте [[ссылку]] из связанной страницы или индекса",
                ))
        return issues

    def _check_duplicate_titles(self, pages: list[WikiPage]) -> list[LintIssue]:
        """Two pages with same title in one project."""
        seen: dict[tuple[str, str], str] = {}  # (project, title) → slug
        issues = []
        for page in pages:
            key = (page.project, page.title.lower().strip())
            if key in seen:
                issues.append(LintIssue(
                    slug=page.slug, line=0,
                    kind="duplicate_title",
                    detail=f"Совпадает название с [[{seen[key]}]] в проекте '{page.project}'",
                    severity="warning",
                    fix_hint="Различите названия или объедините страницы",
                ))
            else:
                seen[key] = page.slug
        return issues

    # ── Helpers ──────────────────────────────────────────────────

    def _check_source_drift(self) -> list[LintIssue]:
        """Check all Source Cards for drift against raw sources."""
        issues = []
        cards = self.fs.list_source_cards()

        for card in cards:
            if card.drift_status == "unchanged":
                continue

            if card.drift_status == "missing_source":
                issues.append(LintIssue(
                    slug=card.slug, line=0,
                    kind="missing_source",
                    detail=f"Source Card для '{card.source_id}': исходный файл "
                           f"'{card.source_path}' больше не существует",
                    severity="warning",
                    fix_hint="Удалите Source Card или восстановите raw-файл",
                ))
                continue

            if card.drift_status == "changed":
                issues.append(LintIssue(
                    slug=card.slug, line=0,
                    kind="source_drift",
                    detail=f"Source Card для '{card.source_id}': исходный файл "
                           f"изменился с момента последнего ingest (SHA256 mismatch)",
                    severity="warning",
                    fix_hint=f"Запустите re-ingest для '{card.source_path}'",
                ))

            if card.drift_status == "unknown":
                # Try to check drift
                if card.source_path:
                    rel = card.source_path.replace("raw/", "", 1) if card.source_path.startswith("raw/") else card.source_path
                    drift = self.fs.check_source_drift(rel)
                    if drift["status"] == "changed":
                        issues.append(LintIssue(
                            slug=card.slug, line=0,
                            kind="source_drift",
                            detail=f"Source Card для '{card.source_id}': "
                                   f"исходный файл изменился (drift detected)",
                            severity="warning",
                            fix_hint=f"Запустите re-ingest для '{card.source_path}'",
                        ))
                    elif drift["status"] == "missing_source":
                        issues.append(LintIssue(
                            slug=card.slug, line=0,
                            kind="missing_source",
                            detail=f"Source Card для '{card.source_id}': "
                                   f"исходный файл '{card.source_path}' не найден",
                            severity="warning",
                            fix_hint="Удалите Source Card или восстановите raw-файл",
                        ))

        # Check for Source Cards without corresponding wiki pages
        for card in cards:
            if card.ingest_status == "active" and not card.pages_written:
                issues.append(LintIssue(
                    slug=card.slug, line=0,
                    kind="orphan_source_card",
                    detail=f"Source Card '{card.source_id}': активен, но нет записанных страниц",
                    severity="info",
                    fix_hint=f"Запустите ingest для '{card.source_path}'",
                ))

        return issues

    def _check_claims(self) -> list[LintIssue]:
        """Check claims for orphaned claims, missing source cards, and status issues."""
        issues = []
        claims = self.fs.list_claims()

        # Get all known source IDs from Source Cards
        source_cards = self.fs.list_source_cards()
        known_source_ids = {card.source_id for card in source_cards}

        for claim in claims:
            # Check: claim without source card
            if claim.source_id not in known_source_ids:
                issues.append(LintIssue(
                    slug=claim.claim_id.replace("/", "_"), line=0,
                    kind="claim_without_source_card",
                    detail=f"Claim '{claim.claim_id}': нет соответствующего Source Card "
                           f"для источника '{claim.source_id}'",
                    severity="warning",
                    fix_hint=f"Создайте Source Card для '{claim.source_id}' или удалите claim",
                ))

            # Check: orphaned claim (no related wiki pages)
            if claim.status == "active" and not claim.related_slugs:
                issues.append(LintIssue(
                    slug=claim.claim_id.replace("/", "_"), line=0,
                    kind="orphan_claim",
                    detail=f"Claim '{claim.claim_id}': активен, но не связан ни с одной wiki-страницей",
                    severity="info",
                    fix_hint="Свяжите claim с wiki-страницей или измените статус на 'ignored'",
                ))

            # Check: contradicted claim still marked active
            if "contradict" in claim.normalized.lower() and claim.status == "active":
                issues.append(LintIssue(
                    slug=claim.claim_id.replace("/", "_"), line=0,
                    kind="contradicted_claim_still_active",
                    detail=f"Claim '{claim.claim_id}': текст содержит противоречие, "
                           f"но статус всё ещё 'active'",
                    severity="warning",
                    fix_hint="Измените статус claim на 'contradicted' или 'unresolved'",
                ))

        return issues

    def _check_planned_pages(self) -> list[LintIssue]:
        """Flag pages that Source Cards planned but never created."""
        issues = []
        cards = self.fs.list_source_cards()
        for card in cards:
            if not card.pages_planned:
                continue
            planned_set = set(card.pages_planned)
            written_set = set(card.pages_written or [])
            missing = planned_set - written_set
            if not missing:
                continue
            issues.append(LintIssue(
                slug=card.slug, line=0,
                kind="planned_page_not_created",
                detail=f"Source Card '{card.source_id}': "
                       f"{len(missing)} of {len(planned_set)} planned pages not created",
                severity="warning",
                fix_hint=f"Re-ingest '{card.source_path}' or increase max_auto_write_pages",
            ))
        return issues

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


def _type_name(expected_type) -> str:
    if isinstance(expected_type, tuple):
        return " | ".join(t.__name__ for t in expected_type)
    return expected_type.__name__
