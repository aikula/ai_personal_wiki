"""
ingest_large.py — Standalone functions for large source chunked ingest pipeline.
"""

from __future__ import annotations

import logging
import threading
from datetime import date

from app.agents.ingest_generate import generate_from_merge
from app.agents.ingest_helpers import (
    ANALYSIS_SCHEMA_HINT,
    build_system_prompt,
    parse_json_response,
    read_agents_md,
)
from app.agents.ingest_prompts import STEP1_PROMPT, STEP1_SYSTEM
from app.agents.ingest_retrieval import build_wiki_context, find_related_pages
from app.agents.ingest_types import IngestResult
from app.config import language_instruction
from app.core.large_source_ingest import (
    chunk_by_outline,
    merge_analysis,
    parse_outline,
)
from app.core.large_source_types import Chunk, ChunkAnalysisResult
from app.core.linter import WikiLinter
from app.core.metered_llm_client import QuotaExceededError
from app.core.utils import now_iso, slugify
from app.core.wiki_fs import WikiFS
from app.core.wiki_types import Claim, IngestLog, SourceCard

logger = logging.getLogger("wiki.ingest")


def analyze_chunk(
    fs: WikiFS,
    llm,
    settings,
    budget,
    interpreter,
    chunk: Chunk,
    source_id: str,
    project: str,
) -> ChunkAnalysisResult:
    related = find_related_pages(interpreter, fs, chunk.text, project)
    wiki_context = build_wiki_context(related, budget)
    skills = fs.read_skills()
    agents_md = read_agents_md(fs.root)
    lang_rule = language_instruction(settings)

    section_path_str = " → ".join(chunk.section_path) if chunk.section_path else "(root)"
    prompt = STEP1_PROMPT.format(
        source_file=f"{source_id} [{chunk.chunk_id}: {section_path_str}]",
        project=project,
        source_content=budget.trim(chunk.text, "wiki_context"),
        wiki_context=wiki_context,
        max_pages=settings.ingest.max_pages_per_batch,
        schema=ANALYSIS_SCHEMA_HINT,
        language_rule=lang_rule,
    )

    raw = llm.call(
        system=build_system_prompt(STEP1_SYSTEM, agents_md, skills),
        prompt=prompt,
        temperature=0.1,
    )

    data = parse_json_response(raw, context=f"Chunk analysis {chunk.chunk_id}")

    candidate_slugs = []
    page_sections: dict[str, list[str]] = {}
    for page_type in ("pages_to_create", "pages_to_update", "pages_to_supersede"):
        for page in data.get(page_type, []):
            slug = page.get("slug", "")
            if slug and slug not in candidate_slugs:
                candidate_slugs.append(slug)
            if slug:
                sections = page.get("source_sections") or [chunk.text]
                page_sections.setdefault(slug, [])
                for section in sections:
                    if section and section not in page_sections[slug]:
                        page_sections[slug].append(section)

    claims = []
    for idx, claim in enumerate(data.get("claims", []), start=1):
        if not isinstance(claim, dict):
            continue
        c = dict(claim)
        c.setdefault("claim_id", f"{source_id}#{chunk.chunk_id}-claim-{idx:03d}")
        c.setdefault("source_id", source_id)
        c.setdefault(
            "source_path",
            f"raw/{chunk.source_path}" if not chunk.source_path.startswith("raw/") else chunk.source_path,
        )
        c.setdefault("source_section", " > ".join(chunk.section_path))
        c.setdefault("chunk_id", chunk.chunk_id)
        if not c.get("related_slugs") and len(candidate_slugs) == 1:
            c["related_slugs"] = [candidate_slugs[0]]
        claims.append(c)

    return ChunkAnalysisResult(
        chunk_id=chunk.chunk_id,
        source_id=source_id,
        section_path=chunk.section_path,
        candidate_pages=candidate_slugs,
        page_sections=page_sections,
        claims=claims,
        conflicts=data.get("conflicts", []),
        outcome="page" if candidate_slugs else "ignored",
    )


def persist_claims(
    fs: WikiFS,
    claims: list[dict],
    source_id: str,
    project: str,
    raw_relative_path: str,
    source_sha256: str,
) -> list[str]:
    written: list[str] = []
    today = date.today().isoformat()
    source_path = f"raw/{raw_relative_path}"
    valid_statuses = {"active", "superseded", "contradicted", "unresolved", "ignored"}

    for idx, data in enumerate(claims, start=1):
        quote = str(data.get("quote") or "").strip()
        normalized = str(data.get("normalized") or quote).strip()
        if not normalized:
            continue
        existing = fs.find_duplicate_claim(normalized, source_id)
        if existing:
            fs.update_claim_status(
                existing.claim_id, existing.project,
                existing.source_id, existing.chunk_id,
                "superseded",
            )
            logger.info("Claim superseded (fuzzy dedup): %s -> %s", existing.claim_id, source_id)
            continue
        chunk_id = str(data.get("chunk_id") or "chunk-000")
        claim_id = str(data.get("claim_id") or f"{source_id}#{chunk_id}-claim-{idx:03d}")
        status = str(data.get("status") or "active")
        if status not in valid_statuses:
            status = "active"
        related_slugs = data.get("related_slugs") or []
        if isinstance(related_slugs, str):
            related_slugs = [related_slugs]
        claim = Claim(
            claim_id=claim_id,
            source_id=source_id,
            source_path=str(data.get("source_path") or source_path),
            source_sha256=str(data.get("source_sha256") or source_sha256),
            source_section=str(data.get("source_section") or ""),
            quote=quote or normalized,
            normalized=normalized,
            related_slugs=list(related_slugs),
            confidence=float(data.get("confidence", 1.0)),
            status=status,
            chunk_id=chunk_id,
            project=project,
            created=today,
        )
        path = fs.write_claim(claim)
        written.append(path.relative_to(fs.wiki_dir).as_posix())
    return written


def run_large_ingest(
    fs: WikiFS,
    llm,
    settings,
    budget,
    interpreter,
    raw_relative_path: str,
    project: str,
    source_content: str,
    allow_draft: bool = True,
    cancel_event: threading.Event | None = None,
) -> IngestResult:
    source_id = slugify(raw_relative_path.replace("/", "_", 1).rsplit(".", 1)[0])
    if not source_id:
        return IngestResult(success=False, project=project, error=f"Invalid filename for source_id: {raw_relative_path}")
    sha256 = fs.compute_source_sha256(source_content)
    today = date.today().isoformat()

    existing_card = fs.read_source_card(source_id)
    outline = parse_outline(source_content, raw_relative_path, target_chars=settings.ingest.chunk_target_chars)

    card = SourceCard(
        source_id=source_id,
        source_path=f"raw/{raw_relative_path}",
        source_sha256=sha256,
        title=f"Source: {raw_relative_path.split('/')[-1]}",
        project=project,
        ingest_status="active",
        created=existing_card.created if existing_card else today,
        last_confirmed=today,
        last_ingested=now_iso(),
        outline=[{"text": item.text, "level": item.level, "char_count": item.char_count}
                 for item in outline.items],
        chunk_count=0,
        chunks_processed=0,
        chunks_failed=0,
        pages_planned=[],
        pages_written=[],
        conflicts_opened=[],
        claims_files=[],
        drift_status="unchanged",
    )
    fs.write_source_card(card)

    chunks = chunk_by_outline(outline, source_content, settings)
    card.chunk_count = len(chunks)
    fs.write_source_card(card)

    chunk_results: list[ChunkAnalysisResult] = []
    quota_exhausted = False
    cancelled = False
    for chunk in chunks:
        if cancel_event and cancel_event.is_set():
            logger.warning("Ingest cancelled at chunk %s/%s. Saving partial results.", chunk.chunk_id, len(chunks))
            cancelled = True
            break
        try:
            result = analyze_chunk(
                fs=fs, llm=llm, settings=settings, budget=budget,
                interpreter=interpreter, chunk=chunk,
                source_id=source_id, project=project,
            )
            chunk_results.append(result)
        except QuotaExceededError:
            logger.warning(
                "Quota exceeded at chunk %s/%s. Saving partial results from %d processed chunks.",
                chunk.chunk_id, len(chunks), len(chunk_results),
            )
            chunk_results.append(ChunkAnalysisResult(
                chunk_id=chunk.chunk_id,
                source_id=source_id,
                section_path=chunk.section_path,
                outcome="failed",
            ))
            quota_exhausted = True
            break
        except Exception as exc:
            logger.error("Chunk analysis failed: chunk_id=%s error=%s", chunk.chunk_id, exc)
            chunk_results.append(ChunkAnalysisResult(
                chunk_id=chunk.chunk_id,
                source_id=source_id,
                section_path=chunk.section_path,
                outcome="failed",
            ))

    merged = merge_analysis(chunk_results, source_id, raw_relative_path)
    claims_files = persist_claims(
        fs=fs,
        claims=merged.all_claims,
        source_id=source_id,
        project=project,
        raw_relative_path=raw_relative_path,
        source_sha256=sha256,
    )

    pages_created, pages_updated, pages_superseded, conflict_ids = (
        generate_from_merge(fs, llm, settings, merged, project, source_content, allow_draft)
    )

    card.chunks_processed = merged.chunks_processed
    card.chunks_failed = merged.chunks_failed
    card.pages_planned = [p["slug"] for p in merged.page_write_plan]
    card.pages_written = pages_created + pages_updated
    card.conflicts_opened = conflict_ids
    card.claims_files = claims_files
    if quota_exhausted:
        card.ingest_status = "partial"
    elif cancelled:
        card.ingest_status = "cancelled"
    fs.write_source_card(card)

    log_entry = IngestLog(
        timestamp=now_iso(),
        source_file=raw_relative_path,
        project=project,
        pages_created=pages_created,
        pages_updated=pages_updated,
        conflicts_detected=conflict_ids,
        skills_triggered=[],
        char_delta=sum(
            len(p.raw)
            for s in pages_created + pages_updated
            if (p := fs.read_page(s))
        ),
    )
    fs.append_log(log_entry)

    lint_report = None
    if settings.ingest.auto_lint_after_ingest:
        linter = WikiLinter(fs, settings)
        lint_report = linter.lint(
            slugs=pages_created + pages_updated + claims_files
        )
        fixed = fs.fix_broken_wikilinks(project=project)
        if fixed:
            logger.info("Auto-fixed %d broken wikilinks after large ingest", fixed)

    analysis_notes = merged.triage_report
    if quota_exhausted:
        processed = len(chunk_results) - 1
        analysis_notes = (
            f"PARTIAL INGEST: quota exhausted after {processed}/{len(chunks)} chunks. "
            f"Results saved for processed chunks. " + (analysis_notes or "")
        )
    elif cancelled:
        processed = len(chunk_results)
        analysis_notes = (
            f"CANCELLED: stopped after {processed}/{len(chunks)} chunks. "
            f"Partial results saved. " + (analysis_notes or "")
        )

    return IngestResult(
        success=True,
        source_file=raw_relative_path,
        project=project,
        pages_created=pages_created,
        pages_updated=pages_updated,
        pages_superseded=pages_superseded,
        conflict_ids=conflict_ids,
        skills_triggered=[],
        lint_report=lint_report,
        analysis_notes=analysis_notes,
    )
