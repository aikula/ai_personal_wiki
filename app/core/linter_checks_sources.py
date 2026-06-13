"""
linter_checks_sources.py — Source/cross-reference lint checks.

Checks (all in-process, no LLM, no writes):
  12. source_drift       — Source Card raw file changed since ingest
  13. missing_source     — Source Card raw file no longer exists
  14. orphan_source_card — Source Card has no written pages
  15. orphan_claim       — active claim has no related wiki pages
  16. claim_without_source_card — claim source card is missing
  17. contradicted_claim_still_active — contradicted claim is still active
  18. planned_page_not_created — Source Card lists planned page that was never written
"""

from __future__ import annotations

import logging

from app.core.linter_models import LintIssue

logger = logging.getLogger(__name__)


def check_source_drift(linter) -> list[LintIssue]:
    """Check all Source Cards for drift against raw sources."""
    issues: list[LintIssue] = []
    cards = linter.fs.list_source_cards()

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
            if card.source_path:
                rel = card.source_path.replace("raw/", "", 1) if card.source_path.startswith("raw/") else card.source_path
                drift = linter.fs.check_source_drift(rel)
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


def check_claims(linter) -> list[LintIssue]:
    """Check claims for orphaned claims, missing source cards, and status issues."""
    issues: list[LintIssue] = []
    claims = linter.fs.list_claims()
    source_cards = linter.fs.list_source_cards()
    known_source_ids = {card.source_id for card in source_cards}

    for claim in claims:
        if claim.source_id not in known_source_ids:
            issues.append(LintIssue(
                slug=claim.claim_id.replace("/", "_"), line=0,
                kind="claim_without_source_card",
                detail=f"Claim '{claim.claim_id}': нет соответствующего Source Card "
                       f"для источника '{claim.source_id}'",
                severity="warning",
                fix_hint=f"Создайте Source Card для '{claim.source_id}' или удалите claim",
            ))

        if claim.status == "active" and not claim.related_slugs:
            issues.append(LintIssue(
                slug=claim.claim_id.replace("/", "_"), line=0,
                kind="orphan_claim",
                detail=f"Claim '{claim.claim_id}': активен, но не связан ни с одной wiki-страницей",
                severity="info",
                fix_hint="Свяжите claim с wiki-страницей или измените статус на 'ignored'",
            ))

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


def check_planned_pages(linter) -> list[LintIssue]:
    """Flag pages that Source Cards planned but never created."""
    issues: list[LintIssue] = []
    cards = linter.fs.list_source_cards()
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
