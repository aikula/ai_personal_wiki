"""
ingest_agent.py — Plan-and-Execute ingest pipeline.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime

from app.agents.ingest_helpers import (
    ANALYSIS_SCHEMA_HINT,
    build_system_prompt,
    char_limit_for_type,
    derive_tags,
    dict_to_analysis_result,
    format_source_sections,
    parse_json_response,
    planned_page_to_dict,
    read_agents_md,
    render_page_raw,
)
from app.agents.ingest_large import run_large_ingest, analyze_chunk, persist_claims
from app.agents.ingest_retrieval import build_wiki_context, find_related_pages
from app.agents.ingest_generate import retry_compact_page
from app.agents.ingest_conflicts import (
    extract_skill_from_resolution,
    record_conflicts,
    try_auto_resolve_conflict,
)
from app.agents.ingest_prompts import (
    STEP1_PROMPT,
    STEP1_SYSTEM,
    STEP2_PROMPT,
    STEP2_SYSTEM,
)
from app.agents.ingest_types import AnalysisResult, IngestResult
from app.config import Settings, language_instruction
from app.core.interpreter import CodeInterpreter
from app.core.linter import WikiLinter
from app.core.llm_client import LLMGateway
from app.core.raw_sources import (
    RawSourceError,
    infer_project_from_raw_relative_path,
    list_raw_source_files,
    read_raw_source_file,
)
from app.core.token_budget import ContextBudget
from app.core.utils import (
    auto_link,
    normalize_wikilinks,
    now_iso,
    validate_wikilinks,
)
from app.core.wiki_fs import (
    IngestLog,
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
        project = infer_project_from_raw_relative_path(raw_relative_path)
        try:
            source_content = read_raw_source_file(self.fs.raw_dir, raw_relative_path)
        except RawSourceError as exc:
            logger.warning("Source conversion failed: %s error=%s", raw_relative_path, exc)
            return IngestResult(
                success=False,
                source_file=raw_relative_path,
                project=project,
                pages_created=[], pages_updated=[], pages_superseded=[],
                conflict_ids=[], skills_triggered=[], lint_report=None,
                error=str(exc),
            )
        if source_content is None:
            logger.warning("Source file not found: %s", raw_relative_path)
            return IngestResult(
                success=False,
                source_file=raw_relative_path,
                project=project,
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
                # Auto-fix broken wikilinks created during ingest
                fixed = self.fs.fix_broken_wikilinks(project=project)
                if fixed:
                    logger.info("Auto-fixed %d broken wikilinks after ingest", fixed)

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
        return find_related_pages(self.interpreter, self.fs, source_content, project)

    def _build_wiki_context(self, pages: list[WikiPage]) -> str:
        return build_wiki_context(pages, self.budget)

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
            try:
                existing_content = ""
                if action in ("update", "supersede"):
                    existing_page = self.fs.read_page(planned.slug)
                    if existing_page:
                        existing_content = existing_page.raw
                char_limit = self._char_limit_for_type(planned.page_type)
                source_sections_text = self._format_source_sections(planned.source_sections)
                existing_trimmed = existing_content[:self.settings.ingest.existing_content_limit]
                candidates = self.fs.build_link_candidates()
                link_lines = []
                for c in candidates[:self.settings.ingest.link_candidates_limit]:
                    alias_str = "; ".join(c["aliases"][:self.settings.ingest.link_aliases_per_candidate])
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
                raw = self.llm.call(system=system, prompt=prompt, temperature=0.1, json_mode=True, max_tokens=self.settings.ingest.max_completion_tokens)
                try:
                    page_data = parse_json_response(raw, context=f"Step2 {planned.slug}")
                except ValueError:
                    retry_prompt = prompt + "\n\nIMPORTANT: Your previous response was not valid JSON. Output ONLY valid JSON."
                    retry_max_tokens = self.settings.ingest.max_completion_tokens * 2
                    raw = self.llm.call(system=system, prompt=retry_prompt, temperature=self.settings.ingest.retry_temperature, json_mode=True, max_tokens=retry_max_tokens)
                    try:
                        page_data = parse_json_response(raw, context=f"Step2 retry {planned.slug}")
                    except ValueError:
                        raw = self.llm.call(system=system, prompt=retry_prompt, temperature=self.settings.ingest.retry_temperature, json_mode=True, max_tokens=self.settings.llm.max_completion_tokens)
                        page_data = parse_json_response(raw, context=f"Step2 fallback {planned.slug}")
                meta = page_data.get("meta", {})
                content = page_data.get("content", "")
                if not meta.get("created"):
                    meta["created"] = today
                meta["project"] = planned.slug.split("/")[0] if "/" in planned.slug else analysis.project
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
            except Exception as exc:
                logger.error("Step2 failed for %s: %s", planned.slug, exc)
                continue
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
        return retry_compact_page(self.fs, self.llm, self.settings, planned, system, agents_md, skills, today, analysis, action, pages_created, pages_updated)

    def _format_source_sections(self, sections: list[str], max_chars: int | None = None) -> str:
        return format_source_sections(sections, self.settings, max_chars)

    def _log_failed_ingest(self, analysis: AnalysisResult) -> None:
        self.fs.append_log(IngestLog(
            timestamp=now_iso(), source_file=analysis.source_file, project=analysis.project,
            pages_created=[], pages_updated=[], conflicts_detected=[], skills_triggered=[], char_delta=0,
        ))

    def _record_conflicts(self, analysis: AnalysisResult) -> list[str]:
        return record_conflicts(self.fs, self.settings, analysis)

    def extract_skill_from_resolution(self, conflict_id: str, resolution: str, user_comment: str) -> str:
        return extract_skill_from_resolution(self.fs, self.llm, self.settings, conflict_id, resolution, user_comment)

    def _try_auto_resolve_conflict(self, conflict_id: str, conflict_type: str, description: str) -> bool:
        return try_auto_resolve_conflict(self.fs, conflict_id, conflict_type, description)

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
        return read_agents_md(self.fs.root)

    def _derive_tags(self, slug: str, source_file: str) -> list[str]:
        return derive_tags(slug, source_file)

    def _char_limit_for_type(self, page_type: str) -> int:
        return char_limit_for_type(page_type, self.settings)

    # ── Large source ingest (Phase 2) ────────────────────────────

    def _run_large_ingest(self, raw_relative_path, project, source_content, allow_draft=True, cancel_event=None):
        return run_large_ingest(self.fs, self.llm, self.settings, self.budget, self.interpreter,
                               raw_relative_path, project, source_content, allow_draft, cancel_event)

    def _analyze_chunk(self, chunk, source_id, project):
        return analyze_chunk(self.fs, self.llm, self.settings, self.budget, self.interpreter,
                            chunk, source_id, project)

    def _persist_claims(self, claims, source_id, project, raw_relative_path, source_sha256):
        return persist_claims(self.fs, claims, source_id, project, raw_relative_path, source_sha256)


