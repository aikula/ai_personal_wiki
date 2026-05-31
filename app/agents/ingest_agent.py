"""
ingest_agent.py — Plan-and-Execute ingest pipeline.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date, datetime

from app.agents.ingest_helpers import (
    ANALYSIS_SCHEMA_HINT,
    build_system_prompt,
    dict_to_analysis_result,
    parse_json_response,
    planned_page_to_dict,
    render_page_raw,
)
from app.agents.ingest_prompts import (
    SKILL_EXTRACTION_PROMPT,
    STEP1_PROMPT,
    STEP1_SYSTEM,
    STEP2_PROMPT,
    STEP2_SYSTEM,
)
from app.agents.ingest_types import AnalysisResult, IngestResult
from app.config import Settings, language_instruction
from app.core.interpreter import CodeInterpreter
from app.core.large_source_ingest import (
    Chunk,
    ChunkAnalysisResult,
    MergeAnalysisResult,
    chunk_by_outline,
    merge_analysis,
    parse_outline,
)
from app.core.linter import WikiLinter
from app.core.llm_client import LLMGateway
from app.core.metered_llm_client import QuotaExceededError
from app.core.raw_sources import (
    RawSourceError,
    list_raw_source_files,
    read_raw_source_file,
)
from app.core.token_budget import ContextBudget
from app.core.utils import (
    auto_link,
    normalize_wikilinks,
    now_iso,
    slugify,
    validate_wikilinks,
)
from app.core.wiki_fs import (
    Claim,
    ConflictEntry,
    IngestLog,
    SourceCard,
    WikiFS,
    WikiPage,
)

logger = logging.getLogger("wiki.ingest")


class IngestAgent:
    def __init__(
        self,
        fs: WikiFS,
        llm: LLMGateway,
        interpreter: CodeInterpreter,
        settings: Settings,
    ):
        self.fs = fs
        self.llm = llm
        self.interpreter = interpreter
        self.settings = settings
        self.budget = ContextBudget(settings)

    def run(self, raw_relative_path: str, allow_draft: bool = True, cancel_event: threading.Event | None = None) -> IngestResult:
        try:
            source_content = read_raw_source_file(self.fs.raw_dir, raw_relative_path)
        except RawSourceError as exc:
            logger.warning("Source conversion failed: %s error=%s", raw_relative_path, exc)
            return IngestResult(
                success=False,
                source_file=raw_relative_path,
                project="_general",
                pages_created=[], pages_updated=[], pages_superseded=[],
                conflict_ids=[], skills_triggered=[], lint_report=None,
                error=str(exc),
            )
        if source_content is None:
            logger.warning("Source file not found: %s", raw_relative_path)
            return IngestResult(
                success=False,
                source_file=raw_relative_path,
                project="_general",
                pages_created=[], pages_updated=[], pages_superseded=[],
                conflict_ids=[], skills_triggered=[], lint_report=None,
                error=f"Source file not found: {raw_relative_path}",
            )

        project = self.fs.get_raw_project(self.fs.raw_dir / raw_relative_path)
        logger.info("Ingest started: file=%s project=%s allow_draft=%s",
                    raw_relative_path, project, allow_draft)

        # Check if this is a large source requiring chunked ingest
        is_large = len(source_content) > self.settings.ingest.large_source_threshold_chars
        if is_large:
            logger.info("Large source detected (%d chars), using chunked ingest", len(source_content))
            return self._run_large_ingest(
                raw_relative_path=raw_relative_path,
                project=project,
                source_content=source_content,
                allow_draft=allow_draft,
                cancel_event=cancel_event,
            )

        try:
            analysis = self._step1_analyze(
                source_file=raw_relative_path,
                project=project,
                source_content=source_content,
            )
            logger.info("Step1 complete: create=%d update=%d supersede=%d conflicts=%d",
                        len(analysis.pages_to_create), len(analysis.pages_to_update),
                        len(analysis.pages_to_supersede), len(analysis.conflicts))

            pages_created, pages_updated, pages_superseded = self._step2_generate(
                analysis, source_content, allow_draft=allow_draft
            )
            conflict_ids = self._record_conflicts(analysis)

            log_entry = IngestLog(
                timestamp=now_iso(),
                source_file=raw_relative_path,
                project=project,
                pages_created=pages_created,
                pages_updated=pages_updated,
                conflicts_detected=conflict_ids,
                skills_triggered=analysis.skills_triggered,
                char_delta=sum(
                    len(p.raw)
                    for s in pages_created + pages_updated
                    if (p := self.fs.read_page(s))
                ),
            )
            self.fs.append_log(log_entry)

            lint_report = None
            if self.settings.ingest.auto_lint_after_ingest:
                linter = WikiLinter(self.fs, self.settings)
                lint_report = linter.lint(slugs=pages_created + pages_updated + pages_superseded)

            return IngestResult(
                success=True,
                source_file=raw_relative_path,
                project=project,
                pages_created=pages_created,
                pages_updated=pages_updated,
                pages_superseded=pages_superseded,
                conflict_ids=conflict_ids,
                skills_triggered=analysis.skills_triggered,
                lint_report=lint_report,
                analysis_notes=analysis.analysis_notes,
            )
        except Exception as exc:
            logger.exception("Ingest failed: file=%s error=%s", raw_relative_path, exc)
            return IngestResult(
                success=False,
                source_file=raw_relative_path,
                project=project,
                pages_created=[], pages_updated=[], pages_superseded=[],
                conflict_ids=[], skills_triggered=[], lint_report=None,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _step1_analyze(self, source_file: str, project: str, source_content: str) -> AnalysisResult:
        related_pages = self._find_related_pages(source_content, project)
        wiki_context = self._build_wiki_context(related_pages)
        skills = self.fs.read_skills()
        agents_md = self._read_agents_md()
        lang_rule = language_instruction(self.settings)
        prompt = STEP1_PROMPT.format(
            source_file=source_file,
            project=project,
            source_content=self.budget.trim(source_content, "wiki_context"),
            wiki_context=wiki_context,
            max_pages=self.settings.ingest.max_pages_per_source,
            schema=ANALYSIS_SCHEMA_HINT,
            language_rule=lang_rule,
        )
        raw = self.llm.call(
            system=build_system_prompt(STEP1_SYSTEM, agents_md, skills),
            prompt=prompt,
            temperature=0.1,
        )
        data = parse_json_response(raw, context="Step1 analysis")
        return dict_to_analysis_result(data, source_file, project)

    def _find_related_pages(self, source_content: str, project: str) -> list[WikiPage]:
        code = f"""
import re
import json
from pathlib import Path
wiki_dir = Path({str(self.fs.wiki_dir)!r})
source = {source_content[:3000]!r}
stopwords = {{'this', 'that', 'with', 'from', 'have', 'will', 'been',
              'they', 'their', 'what', 'when', 'also', 'into', 'more'}}
words = set(
    w.lower() for w in re.findall(r'\\b[a-zA-Zа-яА-Я]{{5,}}\\b', source)
    if w.lower() not in stopwords
)
candidates = []
for md in wiki_dir.rglob("*.md"):
    try:
        text = md.read_text(encoding="utf-8").lower()
        overlap = sum(1 for w in words if w in text)
        rel = md.relative_to(wiki_dir).with_suffix("").as_posix()
        candidates.append((rel, overlap))
    except Exception:
        pass
result = [slug for slug, score in sorted(candidates, key=lambda x: -x[1])[:10] if score > 0]
print(json.dumps(result))
"""
        output = self.interpreter.execute(code)
        slugs: list[str] = output.result_json or []
        pages = []
        for slug in slugs:
            page = self.fs.read_page(slug)
            if page:
                pages.append(page)
        return pages

    def _build_wiki_context(self, pages: list[WikiPage]) -> str:
        parts = [p.raw for p in pages]
        fitted = self.budget.fit_wiki_pages(parts)
        return "\n\n---\n\n".join(fitted)

    def _step2_generate(self, analysis: AnalysisResult, source_content: str, allow_draft: bool = True) -> tuple[list[str], list[str], list[str]]:
        pages_created: list[str] = []
        pages_updated: list[str] = []
        pages_superseded: list[str] = []
        pending_updates: list[dict] = []
        skills = self.fs.read_skills()
        agents_md = self._read_agents_md()
        today = date.today().isoformat()
        all_planned = (
            [(p, "create") for p in analysis.pages_to_create]
            + [(p, "update") for p in analysis.pages_to_update]
            + [(p, "supersede") for p in analysis.pages_to_supersede]
        )
        for planned, action in all_planned:
            existing_content = ""
            if action in ("update", "supersede"):
                existing_page = self.fs.read_page(planned.slug)
                if existing_page:
                    existing_content = existing_page.raw
            char_limit = self._char_limit_for_type(planned.page_type)
            source_sections_text = self._format_source_sections(planned.source_sections)
            existing_trimmed = existing_content[:2000]
            candidates = self.fs.build_link_candidates()
            link_lines = []
            for c in candidates[:15]:
                alias_str = "; ".join(c["aliases"][:3])
                link_lines.append(f"- [[{c['slug']}]] — {c['title']}; aliases: {alias_str}")
            link_candidates_text = "\n".join(link_lines) if link_lines else "(no candidates yet)"
            prompt = STEP2_PROMPT.format(
                planned_page_json=json.dumps(planned_page_to_dict(planned), ensure_ascii=False, indent=2),
                source_file=analysis.source_file.replace("\\", "/"),
                source_sections=source_sections_text,
                existing_content=existing_trimmed,
                link_candidates=link_candidates_text,
                today=today,
                confidence=planned.confidence,
                sources_count=planned.sources_count,
                char_limit=char_limit,
            )
            system = build_system_prompt(STEP2_SYSTEM, agents_md, skills)
            raw = self.llm.call(system=system, prompt=prompt, temperature=0.1, json_mode=True, max_tokens=4000)
            try:
                page_data = parse_json_response(raw, context=f"Step2 {planned.slug}")
            except ValueError:
                retry_prompt = prompt + "\n\nIMPORTANT: Your previous response was not valid JSON. Output ONLY valid JSON."
                raw = self.llm.call(system=system, prompt=retry_prompt, temperature=0.0, json_mode=True, max_tokens=4000)
                page_data = parse_json_response(raw, context=f"Step2 retry {planned.slug}")
            meta = page_data.get("meta", {})
            content = page_data.get("content", "")
            if not meta.get("created"):
                meta["created"] = today
            # Enforce project from slug (LLM sometimes copies source-file project onto _general pages)
            meta["project"] = planned.slug.split("/")[0] if "/" in planned.slug else analysis.project
            # Auto-fill empty tags from slug and source
            if not meta.get("tags"):
                meta["tags"] = self._derive_tags(planned.slug, analysis.source_file)
            content = auto_link(content, self.fs.build_link_candidates(), current_slug=planned.slug)
            existing_slugs = {p.slug for p in self.fs.list_pages()}
            content = normalize_wikilinks(content, existing_slugs)
            broken = validate_wikilinks(content, existing_slugs)
            if broken:
                logger.warning("Step2 %s has %d broken wikilinks: %s", planned.slug, len(broken), broken)
            if action == "create" or not allow_draft:
                try:
                    self.fs.write_page(slug=planned.slug, meta=meta, content=content, allow_overwrite=(action != "create"))
                    if action == "create":
                        pages_created.append(planned.slug)
                    else:
                        pages_updated.append(planned.slug)
                except Exception as exc:
                    exc_name = type(exc).__name__
                    if "CharLimitExceeded" in exc_name:
                        logger.warning("Step2 %s exceeded char limit, retrying with compact prompt", planned.slug)
                        retry_ok = self._retry_compact_page(
                            planned, system, agents_md, skills, today, analysis, action,
                            pages_created, pages_updated,
                        )
                        if not retry_ok:
                            logger.error("Step2 retry also failed for %s", planned.slug)
                    else:
                        logger.warning("Step2 write failed: slug=%s action=%s error=%s", planned.slug, action, exc)
                continue
            pending_updates.append({"slug": planned.slug, "content": render_page_raw(meta, content), "action": action})
        if pending_updates:
            draft_id = f"ingest-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            plan = {
                "summary": f"{len(pending_updates)} page(s) to update from {analysis.source_file}",
                "source_file": analysis.source_file,
                "project": analysis.project,
                "actions": [{"slug": u["slug"], "action": u["action"]} for u in pending_updates],
            }
            self.fs.create_draft(
                draft_id=draft_id,
                plan=plan,
                pages={u["slug"]: u["content"] for u in pending_updates},
                conflicts=[],
            )
            for u in pending_updates:
                pages_updated.append(u["slug"])
        return pages_created, pages_updated, pages_superseded

    def _retry_compact_page(self, planned, system, agents_md, skills, today,
                            analysis, action, pages_created, pages_updated):
        """Retry page generation with a compact prompt asking for shorter output."""
        compact_prompt = STEP2_PROMPT.format(
            planned_page_json=f'{{"slug": "{planned.slug}", "action": "{action}"}}',
            source_file=analysis.source_file,
            source_sections="(compacted — produce concise output)",
            existing_content="",
            link_candidates="",
            today=today,
            confidence=planned.confidence,
            sources_count=1,
            char_limit=self.settings.limits.entity_page_chars,
        ) + "\n\nIMPORTANT: Keep the content VERY concise. Under 2500 chars of body text. No long explanations."
        try:
            raw = self.llm.call(system=system, prompt=compact_prompt, temperature=0.0, json_mode=True, max_tokens=3000)
            page_data = parse_json_response(raw, context=f"Step2 compact retry {planned.slug}")
            meta = page_data.get("meta", {})
            content = page_data.get("content", "")
            if not meta.get("created"):
                meta["created"] = today
            meta["project"] = planned.slug.split("/")[0] if "/" in planned.slug else analysis.project
            self.fs.write_page(slug=planned.slug, meta=meta, content=content, allow_overwrite=True)
            if action == "create":
                pages_created.append(planned.slug)
            else:
                pages_updated.append(planned.slug)
            return True
        except Exception:
            return False

    def _format_source_sections(self, sections: list[str], max_chars: int | None = None) -> str:
        """Fit relevant source fragments into Step 2 without falling back to full-source prefix."""
        if not sections:
            return "(no source sections assigned)"
        limit = max_chars or min(self.settings.query.context_budget_chars, 24_000)
        parts = []
        used = 0
        for section in sections:
            text = section.strip()
            if not text:
                continue
            separator = "\n\n---\n\n" if parts else ""
            remaining = limit - used - len(separator)
            if remaining <= 0:
                break
            if len(text) > remaining:
                text = text[:max(0, remaining - 18)].rstrip() + "\n[TRUNCATED]"
            parts.append(separator + text)
            used += len(separator) + len(text)
        return "".join(parts) if parts else "(no source sections assigned)"

    def _log_failed_ingest(self, analysis: AnalysisResult) -> None:
        self.fs.append_log(IngestLog(
            timestamp=now_iso(), source_file=analysis.source_file, project=analysis.project,
            pages_created=[], pages_updated=[], conflicts_detected=[], skills_triggered=[], char_delta=0,
        ))

    def _record_conflicts(self, analysis: AnalysisResult) -> list[str]:
        existing_raw = self.fs.read_conflicts_raw()
        ids = re.findall(r"CONFLICT-(\d+)", existing_raw)
        next_num = max((int(i) for i in ids), default=0) + 1
        assigned_ids = []
        for conflict in analysis.conflicts:
            cid = f"CONFLICT-{next_num:03d}"
            next_num += 1
            entry = ConflictEntry(
                id=cid,
                status="OPEN",
                date=date.today().isoformat(),
                project=analysis.project,
                source_file=analysis.source_file,
                conflict_type=conflict.conflict_type,
                page_a_slug=conflict.existing_slug,
                page_b_ref=conflict.source_ref,
                context_a=conflict.context_existing[:600],
                context_b=conflict.context_source[:600],
                suggested_options=conflict.suggested_options,
                description=conflict.description,
                is_cross_project=conflict.is_cross_project,
            )
            self.fs.append_conflict(entry)
            self._try_auto_resolve_conflict(cid, conflict.conflict_type, conflict.description)
            assigned_ids.append(cid)
        return assigned_ids

    def extract_skill_from_resolution(self, conflict_id: str, resolution: str, user_comment: str) -> str:
        conflicts_raw = self.fs.read_conflicts_raw()
        pattern = rf"## \[(?:OPEN|RESOLVED)\] {re.escape(conflict_id)}(.*?)(?=\n---|\Z)"
        match = re.search(pattern, conflicts_raw, re.DOTALL)
        conflict_summary = match.group(1).strip() if match else conflict_id
        lang_rule = language_instruction(self.settings)
        raw = self.llm.call(
            system="You extract reusable rules from conflict resolutions.",
            prompt=SKILL_EXTRACTION_PROMPT.format(
                conflict_summary=conflict_summary[:800],
                resolution=resolution,
                user_comment=user_comment,
                language_rule=lang_rule,
            ),
            temperature=0.1,
        )
        data = parse_json_response(raw, context="skill extraction")
        section = data.get("section", "Conflict Resolution Patterns")
        rule = data.get("rule", "")
        if rule:
            self.fs.append_skill(section=section, skill_text=rule)
        self.fs.resolve_conflict(conflict_id=conflict_id, resolution=resolution, user_comment=user_comment, skill_extracted=rule)
        return rule

    def _try_auto_resolve_conflict(self, conflict_id: str, conflict_type: str, description: str) -> bool:
        """Try to auto-resolve a conflict using skills.md rules. Returns True if resolved."""
        skills_raw = self.fs.read_skills()
        if not skills_raw:
            return False

        type_lower = conflict_type.lower()
        desc_lower = (description or "").lower()

        # Search each skill line for relevance to conflict type
        for line in skills_raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("<!--"):
                continue
            line_lower = line.lower()
            # Check if this skill line covers the conflict type or key terms
            conflict_terms = type_lower.replace("_", " ").split() + desc_lower.split()[:5]
            match_count = sum(1 for term in conflict_terms if len(term) > 3 and term in line_lower)
            if match_count >= 2:
                resolution = f"auto_skill: {line}"
                self.fs.resolve_conflict(
                    conflict_id=conflict_id,
                    resolution=resolution,
                    user_comment="Auto-resolved by skill matching",
                    skill_extracted="",
                )
                logger.info("Auto-resolved %s via skill: %s", conflict_id, line[:80])
                return True
        return False

    def rebuild_from_scratch(self, progress_callback=None) -> dict:
        logger.info("Rebuild started")
        raw_files = list_raw_source_files(self.fs.raw_dir)
        removed_conflicts = self.fs.clear_open_conflicts()
        if removed_conflicts:
            logger.info("Rebuild: cleared %d open conflicts", removed_conflicts)
        removed_drafts = self.fs.clear_all_drafts()
        if removed_drafts:
            logger.info("Rebuild: cleared %d stale drafts", removed_drafts)
        self.fs.full_reset_wiki()
        self.fs.defer_index()
        raw_files.sort(key=lambda p: ("0" if self.fs.get_raw_project(p) == "_general" else "1", str(p)))
        results = {"total": len(raw_files), "success": 0, "failed": 0, "errors": [], "conflict_ids": []}
        try:
            for i, raw_path in enumerate(raw_files):
                rel = str(raw_path.relative_to(self.fs.raw_dir))
                if progress_callback:
                    progress_callback(i + 1, len(raw_files), rel)
                result = self.run(rel, allow_draft=False)
                if result.success:
                    results["success"] += 1
                    results["conflict_ids"] += result.conflict_ids
                else:
                    results["failed"] += 1
                    results["errors"].append({"file": rel, "error": result.error})
            return results
        finally:
            self.fs.resume_index()
            self.fs.rebuild_index()

    def _read_agents_md(self) -> str:
        path = self.fs.root / "AGENTS.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _derive_tags(self, slug: str, source_file: str) -> list[str]:
        """Auto-fill tags from slug category and source file name."""
        tags = []
        parts = slug.split("/")
        # Category from slug path (e.g. "safety" from "_general/safety/fire-prevention")
        if len(parts) >= 2:
            tags.append(parts[-2])
        # Source file stem as tag (e.g. "MTU-L33-manual" from raw/_general/MTU-L33-manual.pdf)
        if source_file:
            stem = source_file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if stem and stem not in tags:
                tags.append(stem)
        return tags[:5]

    def _char_limit_for_type(self, page_type: str) -> int:
        return {
            "entity": self.settings.limits.entity_page_chars,
            "concept": self.settings.limits.concept_page_chars,
        }.get(page_type, self.settings.limits.entity_page_chars)

    # ── Large source ingest (Phase 2) ────────────────────────────

    def _run_large_ingest(
        self,
        raw_relative_path: str,
        project: str,
        source_content: str,
        allow_draft: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> IngestResult:
        """
        Process a large source document through chunked ingest pipeline.

        Flow:
        1. Create/update Source Card
        2. Parse outline
        3. Chunk by outline
        4. Analyze each chunk (Step 1 per chunk)
        5. Merge analysis
        6. Generate pages (Step 2 per planned page)
        7. Update Source Card with results
        """
        source_id = raw_relative_path.replace("/", "_", 1).rsplit(".", 1)[0]
        sha256 = self.fs.compute_source_sha256(source_content)
        today = date.today().isoformat()

        # Step 1: Create/update Source Card
        existing_card = self.fs.read_source_card(source_id)
        outline = parse_outline(source_content, raw_relative_path, target_chars=self.settings.ingest.chunk_target_chars)

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
        self.fs.write_source_card(card)

        # Step 2: Chunk the source
        chunks = chunk_by_outline(outline, source_content, self.settings)
        card.chunk_count = len(chunks)
        self.fs.write_source_card(card)

        # Step 3: Analyze each chunk
        chunk_results: list[ChunkAnalysisResult] = []
        quota_exhausted = False
        cancelled = False
        for chunk in chunks:
            if cancel_event and cancel_event.is_set():
                logger.warning("Ingest cancelled at chunk %s/%s. Saving partial results.", chunk.chunk_id, len(chunks))
                cancelled = True
                break
            try:
                result = self._analyze_chunk(
                    chunk=chunk,
                    source_id=source_id,
                    project=project,
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

        # Step 4: Merge analysis
        merged = merge_analysis(chunk_results, source_id, raw_relative_path)
        claims_files = self._persist_claims(
            merged.all_claims,
            source_id=source_id,
            project=project,
            raw_relative_path=raw_relative_path,
            source_sha256=sha256,
        )

        # Step 5: Generate pages (batch with limits)
        pages_created, pages_updated, pages_superseded, conflict_ids = (
            self._generate_from_merge(merged, project, source_content, allow_draft)
        )

        # Step 6: Update Source Card
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
        self.fs.write_source_card(card)

        # Step 7: Log and lint
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
                if (p := self.fs.read_page(s))
            ),
        )
        self.fs.append_log(log_entry)

        lint_report = None
        if self.settings.ingest.auto_lint_after_ingest:
            linter = WikiLinter(self.fs, self.settings)
            lint_report = linter.lint(
                slugs=pages_created + pages_updated + claims_files
            )

        analysis_notes = merged.triage_report
        if quota_exhausted:
            processed = len(chunk_results) - 1  # exclude the failed last chunk
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

    def _analyze_chunk(
        self,
        chunk: Chunk,
        source_id: str,
        project: str,
    ) -> ChunkAnalysisResult:
        """
        Analyze a single chunk using Step 1 pipeline (LLM-based).
        Returns typed ChunkAnalysisResult.
        """
        # Find related pages for this chunk
        related = self._find_related_pages(chunk.text, project)
        wiki_context = self._build_wiki_context(related)
        skills = self.fs.read_skills()
        agents_md = self._read_agents_md()
        lang_rule = language_instruction(self.settings)

        # Use existing Step 1 prompt but with chunk content
        section_path_str = " → ".join(chunk.section_path) if chunk.section_path else "(root)"
        prompt = STEP1_PROMPT.format(
            source_file=f"{source_id} [{chunk.chunk_id}: {section_path_str}]",
            project=project,
            source_content=self.budget.trim(chunk.text, "wiki_context"),
            wiki_context=wiki_context,
            max_pages=self.settings.ingest.max_pages_per_batch,
            schema=ANALYSIS_SCHEMA_HINT,
            language_rule=lang_rule,
        )

        raw = self.llm.call(
            system=build_system_prompt(STEP1_SYSTEM, agents_md, skills),
            prompt=prompt,
            temperature=0.1,
        )

        data = parse_json_response(raw, context=f"Chunk analysis {chunk.chunk_id}")

        # Convert to ChunkAnalysisResult
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

    def _persist_claims(
        self,
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
            if self.fs.find_duplicate_claim(normalized, source_id):
                # Mark existing duplicate as superseded by the new claim
                existing = self.fs.find_duplicate_claim(normalized, source_id)
                if existing:
                    self.fs.update_claim_status(
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
            path = self.fs.write_claim(claim)
            written.append(path.relative_to(self.fs.wiki_dir).as_posix())
        return written

    def _generate_from_merge(
        self,
        merged: MergeAnalysisResult,
        project: str,
        source_content: str,
        allow_draft: bool,
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        """
        Generate wiki pages from merge analysis result.
        Respects max_auto_write_pages and require_review_if_pages_gt limits.
        """
        pages_created = []
        pages_updated = []
        pages_superseded = []
        conflict_ids = []

        # Record conflicts first (non-blocking)
        for conflict_data in merged.all_conflicts:
            cid = self._record_single_conflict(conflict_data, project, merged.source_path)
            if cid:
                conflict_ids.append(cid)

        total_pages = len(merged.all_candidate_pages)
        require_review = allow_draft and total_pages > self.settings.ingest.require_review_if_pages_gt
        if require_review:
            logger.warning(
                "Large source: %d pages planned exceeds review threshold (%d). "
                "Writing draft for review instead of applying pages.",
                total_pages, self.settings.ingest.require_review_if_pages_gt,
            )
            pending_draft: dict[str, str] = {}

        # Generate pages in batches
        for i, page_info in enumerate(merged.all_candidate_pages):
            if not require_review and i >= self.settings.ingest.max_auto_write_pages:
                logger.warning(
                    "Reached max_auto_write_pages limit (%d). "
                    "Remaining %d pages need manual ingest.",
                    self.settings.ingest.max_auto_write_pages,
                    total_pages - i,
                )
                break

            slug = slugify(page_info["slug"])
            # Determine action based on existing page
            existing = self.fs.read_page(slug)
            action = "update" if existing else "create"

            try:
                page_meta, page_content = self._generate_single_page(
                    slug=slug,
                    project=project,
                    source_sections=page_info.get("source_sections", []),
                    source_file=merged.source_path,
                    existing_content=existing.raw if existing else "",
                    action=action,
                )
                if require_review:
                    pending_draft[slug] = render_page_raw(page_meta, page_content)
                    continue
                self.fs.write_page(
                    slug=slug,
                    meta=page_meta,
                    content=page_content,
                    allow_overwrite=(action != "create"),
                )
                if action == "create":
                    pages_created.append(slug)
                else:
                    pages_updated.append(slug)
            except Exception as exc:
                exc_name = type(exc).__name__
                if "CharLimitExceeded" in exc_name:
                    logger.warning("Page %s exceeded char limit, retrying compact", slug)
                    try:
                        compact_meta, compact_content = self._generate_single_page(
                            slug=slug,
                            project=project,
                            source_sections=page_info.get("source_sections", [])[:1],
                            source_file=merged.source_path,
                            existing_content="",
                            action=action,
                            force_char_limit=self.settings.limits.entity_page_chars,
                        )
                        if require_review:
                            pending_draft[slug] = render_page_raw(compact_meta, compact_content)
                        else:
                            self.fs.write_page(slug=slug, meta=compact_meta, content=compact_content, allow_overwrite=True)
                            if action == "create":
                                pages_created.append(slug)
                            else:
                                pages_updated.append(slug)
                    except Exception as retry_exc:
                        logger.error("Compact retry also failed for %s: %s", slug, retry_exc)
                else:
                    logger.error("Failed to generate page %s: %s", slug, exc)

        if require_review and pending_draft:
            draft_id = f"large-ingest-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            self.fs.create_draft(
                draft_id=draft_id,
                plan={
                    "summary": f"{len(pending_draft)} page(s) planned from large source {merged.source_path}",
                    "source_file": merged.source_path,
                    "project": project,
                    "review_reason": (
                        f"planned pages {total_pages} exceed threshold "
                        f"{self.settings.ingest.require_review_if_pages_gt}"
                    ),
                    "actions": [
                        {"slug": slug, "action": "update" if self.fs.read_page(slug) else "create"}
                        for slug in pending_draft
                    ],
                },
                pages=pending_draft,
                conflicts=merged.all_conflicts,
            )

        return pages_created, pages_updated, pages_superseded, conflict_ids

    def _generate_single_page(
        self,
        slug: str,
        project: str,
        source_sections: list[str],
        source_file: str,
        existing_content: str,
        action: str,
        force_char_limit: int | None = None,
    ) -> tuple[dict, str]:
        """Generate a single wiki page from source content."""
        from app.agents.ingest_helpers import (
            build_system_prompt,
            parse_json_response,
        )
        from app.agents.ingest_prompts import STEP2_PROMPT, STEP2_SYSTEM

        skills = self.fs.read_skills()
        agents_md = self._read_agents_md()
        today = date.today().isoformat()
        char_limit = force_char_limit or self._char_limit_for_type("entity")

        # Build a minimal planned page dict for the prompt
        planned_page = {
            "slug": slug,
            "title": slug.split("/")[-1].replace("-", " ").title(),
            "project": project,
            "page_type": "entity",
            "tags": [project],
            "action": action,
        }

        candidates = self.fs.build_link_candidates()
        link_lines = []
        for c in candidates[:15]:
            alias_str = "; ".join(c["aliases"][:3])
            link_lines.append(f"- [[{c['slug']}]] — {c['title']}; aliases: {alias_str}")
        link_candidates_text = "\n".join(link_lines) if link_lines else "(no candidates yet)"

        source_sections_text = self._format_source_sections(source_sections)

        prompt = STEP2_PROMPT.format(
            planned_page_json=json.dumps(planned_page, ensure_ascii=False, indent=2),
            source_file=source_file,
            source_sections=source_sections_text,
            existing_content=existing_content[:2000],
            link_candidates=link_candidates_text,
            today=today,
            confidence=0.8,
            sources_count=1,
            char_limit=char_limit,
        )

        system = build_system_prompt(STEP2_SYSTEM, agents_md, skills)
        raw = self.llm.call(system=system, prompt=prompt, temperature=0.1, json_mode=True, max_tokens=4000)

        try:
            page_data = parse_json_response(raw, context=f"Step2 {slug}")
        except ValueError:
            retry_prompt = prompt + "\n\nIMPORTANT: Your previous response was not valid JSON. Output ONLY valid JSON."
            raw = self.llm.call(system=system, prompt=retry_prompt, temperature=0.0, json_mode=True, max_tokens=4000)
            page_data = parse_json_response(raw, context=f"Step2 retry {slug}")

        meta = page_data.get("meta", {})
        content = page_data.get("content", "")
        if not meta.get("created"):
            meta["created"] = today
        meta["project"] = project
        if not meta.get("tags"):
            meta["tags"] = self._derive_tags(slug, source_file)

        existing_slugs = {p.slug for p in self.fs.list_pages()}
        content = normalize_wikilinks(content, existing_slugs)

        return meta, content

    def _record_single_conflict(
        self,
        conflict_data: dict,
        project: str,
        source_path: str,
    ) -> str | None:
        """Record a single conflict from chunk analysis."""
        try:
            existing_raw = self.fs.read_conflicts_raw()
            ids = re.findall(r"CONFLICT-(\d+)", existing_raw)
            next_num = max((int(i) for i in ids), default=0) + 1
            cid = f"CONFLICT-{next_num:03d}"

            entry = ConflictEntry(
                id=cid,
                status="OPEN",
                date=date.today().isoformat(),
                project=project,
                source_file=source_path,
                conflict_type=conflict_data.get("conflict_type", "factual_contradiction"),
                page_a_slug=conflict_data.get("existing_slug", "unknown"),
                page_b_ref=conflict_data.get("source_ref", source_path),
                context_a=conflict_data.get("context_existing", "")[:600],
                context_b=conflict_data.get("context_source", "")[:600],
                suggested_options=conflict_data.get("suggested_options", []),
                description=conflict_data.get("description", ""),
                is_cross_project=conflict_data.get("is_cross_project", False),
            )
            self.fs.append_conflict(entry)
            self._try_auto_resolve_conflict(
                cid,
                conflict_data.get("conflict_type", ""),
                conflict_data.get("description", ""),
            )
            return cid
        except Exception as exc:
            logger.error("Failed to record conflict: %s", exc)
            return None
