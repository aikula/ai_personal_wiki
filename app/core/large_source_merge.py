"""Merge analysis for large source ingest — combines chunk results."""

from __future__ import annotations

import logging

from app.core.large_source_types import ChunkAnalysisResult, MergeAnalysisResult

logger = logging.getLogger("wiki.ingest.large")


def merge_analysis(
    chunk_results: list[ChunkAnalysisResult],
    source_id: str,
    source_path: str,
) -> MergeAnalysisResult:
    """
    Combine chunk analysis results into a unified plan.

    - Deduplicate candidate pages by slug (merge claims)
    - Deduplicate claims by normalized text
    - Lift conflicts to source level
    - Build triage report
    - Build page write plan
    """
    pages_by_slug: dict[str, dict] = {}
    for cr in chunk_results:
        for page in cr.candidate_pages:
            if page not in pages_by_slug:
                pages_by_slug[page] = {
                    "slug": page,
                    "source_chunks": [],
                    "claims": [],
                    "source_sections": [],
                }
            pages_by_slug[page]["source_chunks"].append(cr.chunk_id)
            if len(pages_by_slug[page]["source_chunks"]) > 1:
                logger.warning(
                    "Merge collision: slug '%s' planned by multiple chunks %s",
                    page, pages_by_slug[page]["source_chunks"],
                )
            for section in cr.page_sections.get(page, []):
                if section and section not in pages_by_slug[page]["source_sections"]:
                    pages_by_slug[page]["source_sections"].append(section)

    from rapidfuzz import fuzz as _fuzz

    seen_claims: list[str] = []
    all_claims = []
    for cr in chunk_results:
        for claim in cr.claims:
            normalized = claim.get("normalized", claim.get("quote", "")).lower().strip()
            is_dup = False
            for seen in seen_claims:
                if abs(len(seen) - len(normalized)) < max(len(normalized), 1) * 0.4:
                    if _fuzz.ratio(normalized, seen) > 85:
                        is_dup = True
                        break
            if not is_dup:
                seen_claims.append(normalized)
                all_claims.append(claim)
            for slug in claim.get("related_slugs", []):
                if slug in pages_by_slug and claim not in pages_by_slug[slug]["claims"]:
                    pages_by_slug[slug]["claims"].append(claim)

    all_conflicts = []
    for cr in chunk_results:
        all_conflicts.extend(cr.conflicts)

    processed = sum(1 for cr in chunk_results if cr.outcome != "failed")
    failed = sum(1 for cr in chunk_results if cr.outcome == "failed")

    triage_lines = [
        f"Source: {source_path}",
        f"Chunks: {len(chunk_results)} total, {processed} processed, {failed} failed",
        f"Pages planned: {len(pages_by_slug)}",
        f"Claims extracted: {len(all_claims)}",
        f"Conflicts detected: {len(all_conflicts)}",
    ]

    outcomes = {}
    for cr in chunk_results:
        outcomes[cr.outcome] = outcomes.get(cr.outcome, 0) + 1
    triage_lines.append("")
    triage_lines.append("Chunk outcomes:")
    for outcome, count in sorted(outcomes.items()):
        triage_lines.append(f"  {outcome}: {count}")

    page_write_plan = []
    for slug, info in pages_by_slug.items():
        page_write_plan.append({
            "slug": slug,
            "action": "create",
            "source_chunks": info["source_chunks"],
            "claim_count": len(info["claims"]),
        })

    return MergeAnalysisResult(
        source_id=source_id,
        source_path=source_path,
        total_chunks=len(chunk_results),
        chunks_processed=processed,
        chunks_failed=failed,
        all_candidate_pages=list(pages_by_slug.values()),
        all_claims=all_claims,
        all_conflicts=all_conflicts,
        triage_report="\n".join(triage_lines),
        page_write_plan=page_write_plan,
    )
