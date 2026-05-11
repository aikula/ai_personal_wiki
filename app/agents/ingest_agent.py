"""
ingest_agent.py — Plan-and-Execute ingest pipeline.
"""

from __future__ import annotations

import json
import logging
import re
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
from app.core.linter import WikiLinter
from app.core.llm_client import LLMClient
from app.core.raw_sources import RawSourceError, list_raw_source_files, read_raw_source_file
from app.core.token_budget import ContextBudget
from app.core.utils import auto_link, validate_wikilinks, now_iso
from app.core.wiki_fs import ConflictEntry, IngestLog, WikiFS, WikiPage

logger = logging.getLogger("wiki.ingest")


class IngestAgent:
    def __init__(
        self,
        fs: WikiFS,
        llm: LLMClient,
        interpreter: CodeInterpreter,
        settings: Settings,
    ):
        self.fs = fs
        self.llm = llm
        self.interpreter = interpreter
        self.settings = settings
        self.budget = ContextBudget(settings)

    def run(self, raw_relative_path: str, allow_draft: bool = True) -> IngestResult:
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
            source_sections_text = "\n\n---\n\n".join(planned.source_sections)[:3000]
            existing_trimmed = existing_content[:2000]
            candidates = self.fs.build_link_candidates()
            link_lines = []
            for c in candidates[:15]:
                alias_str = "; ".join(c["aliases"][:3])
                link_lines.append(f"- [[{c['slug']}]] — {c['title']}; aliases: {alias_str}")
            link_candidates_text = "\n".join(link_lines) if link_lines else "(no candidates yet)"
            prompt = STEP2_PROMPT.format(
                planned_page_json=json.dumps(planned_page_to_dict(planned), ensure_ascii=False, indent=2),
                source_sections=source_sections_text,
                existing_content=existing_trimmed,
                link_candidates=link_candidates_text,
                today=today,
                confidence=planned.confidence,
                sources_count=planned.sources_count,
                char_limit=char_limit,
            )
            system = build_system_prompt(STEP2_SYSTEM, agents_md, skills)
            raw = self.llm.call(system=system, prompt=prompt, temperature=0.1, json_mode=True, max_tokens=2500)
            try:
                page_data = parse_json_response(raw, context=f"Step2 {planned.slug}")
            except ValueError:
                retry_prompt = prompt + "\n\nIMPORTANT: Your previous response was not valid JSON. Output ONLY valid JSON."
                raw = self.llm.call(system=system, prompt=retry_prompt, temperature=0.0, json_mode=True, max_tokens=2500)
                page_data = parse_json_response(raw, context=f"Step2 retry {planned.slug}")
            meta = page_data.get("meta", {})
            content = page_data.get("content", "")
            if not meta.get("created"):
                meta["created"] = today
            content = auto_link(content, self.fs.build_link_candidates(), current_slug=planned.slug)
            # Validate wikilinks against existing pages
            existing_slugs = {p.slug for p in self.fs.list_pages()}
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
                    logger.warning("Step2 write failed: slug=%s action=%s error=%s", planned.slug, action, exc)
                    self._log_failed_ingest(analysis)
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

    def _char_limit_for_type(self, page_type: str) -> int:
        return {
            "entity": self.settings.limits.entity_page_chars,
            "concept": self.settings.limits.concept_page_chars,
        }.get(page_type, self.settings.limits.entity_page_chars)
