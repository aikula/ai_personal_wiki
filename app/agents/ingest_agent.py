"""
ingest_agent.py — Plan-and-Execute ingest pipeline.

Flow:
  1. PLAN:     Read source → AnalysisResult (entities, concepts, conflicts)
  2. EXECUTE:  For each planned page → write to wiki via WikiFS
  3. CONFLICTS: Append detected conflicts to conflicts.md
  4. SKILLS:   Apply relevant skills from skills.md during both steps
  5. LOG:      Write IngestLog to log.md

Rules (from CLAUDE.md):
  - Two-step is MANDATORY. Never skip analysis pass.
  - Conflicts do NOT block ingest. Process non-conflicting content, flag conflicts.
  - Every output from LLM is validated against typed schema before write.
  - max_pages_per_source enforced to prevent explosion.
  - After ingest, run WikiLinter on new/updated pages (incremental).
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
from app.agents.ingest_types import (
    AnalysisResult,
    IngestResult,
)
from app.config import Settings
from app.core.interpreter import CodeInterpreter
from app.core.linter import WikiLinter
from app.core.llm_client import LLMClient
from app.core.token_budget import ContextBudget
from app.core.utils import auto_link, now_iso
from app.core.wiki_fs import ConflictEntry, IngestLog, WikiFS, WikiPage

logger = logging.getLogger("wiki.ingest")


class IngestAgent:
    """
    Plan-and-Execute agent for ingesting raw .md files into wiki.

    Usage:
        agent = IngestAgent(fs, llm, interpreter, settings)
        result = agent.run("myapp/guide.md")

    The agent reads settings.ingest for configuration.
    All wiki writes go through self.fs (WikiFS).
    """

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

    # ── Public entrypoint ────────────────────────────────────────

    def run(self, raw_relative_path: str) -> IngestResult:
        source_content = self.fs.read_raw_file(raw_relative_path)
        if source_content is None:
            logger.warning("Source file not found: %s", raw_relative_path)
            return IngestResult(
                success=False,
                source_file=raw_relative_path,
                project="_general",
                pages_created=[], pages_updated=[],
                pages_superseded=[], conflict_ids=[],
                skills_triggered=[], lint_report=None,
                error=f"Source file not found: {raw_relative_path}",
            )

        project = self.fs.get_raw_project(
            self.fs.raw_dir / raw_relative_path
        )
        logger.info("Ingest started: file=%s project=%s", raw_relative_path, project)

        try:
            analysis = self._step1_analyze(
                source_file=raw_relative_path,
                project=project,
                source_content=source_content,
            )
            logger.info("Step1 complete: create=%d update=%d supersede=%d conflicts=%d",
                        len(analysis.pages_to_create), len(analysis.pages_to_update),
                        len(analysis.pages_to_supersede), len(analysis.conflicts))

            pages_created, pages_updated, pages_superseded = \
                self._step2_generate(analysis, source_content)

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
                lint_report = linter.lint(
                    slugs=pages_created + pages_updated + pages_superseded
                )

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
            logger.exception("Ingest failed: file=%s error=%s",
                             raw_relative_path, exc)
            return IngestResult(
                success=False,
                source_file=raw_relative_path,
                project=project,
                pages_created=[], pages_updated=[],
                pages_superseded=[], conflict_ids=[],
                skills_triggered=[], lint_report=None,
                error=f"{type(exc).__name__}: {exc}",
            )

    # ── Step 1: Analysis ─────────────────────────────────────────

    def _step1_analyze(
        self,
        source_file: str,
        project: str,
        source_content: str,
    ) -> AnalysisResult:
        related_pages = self._find_related_pages(source_content, project)
        wiki_context = self._build_wiki_context(related_pages)

        skills = self.fs.read_skills()
        agents_md = self._read_agents_md()

        prompt = STEP1_PROMPT.format(
            source_file=source_file,
            project=project,
            source_content=self.budget.trim(source_content, "wiki_context"),
            wiki_context=wiki_context,
            max_pages=self.settings.ingest.max_pages_per_source,
            schema=ANALYSIS_SCHEMA_HINT,
        )

        raw = self.llm.call(
            system=build_system_prompt(STEP1_SYSTEM, agents_md, skills),
            prompt=prompt,
            temperature=0.1,
        )

        data = parse_json_response(raw, context="Step1 analysis")
        return dict_to_analysis_result(data, source_file, project)

    def _find_related_pages(
        self,
        source_content: str,
        project: str,
    ) -> list[WikiPage]:
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

result = [slug for slug, score in sorted(candidates, key=lambda x: -x[1])[:10]
          if score > 0]
print(json.dumps(result))
"""
        output = self.interpreter.execute(code)
        slugs: list[str] = output.result_json or []
        logger.debug("Related pages found: %s", slugs)
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

    # ── Step 2: Generation ───────────────────────────────────────

    def _step2_generate(
        self,
        analysis: AnalysisResult,
        source_content: str,
    ) -> tuple[list[str], list[str], list[str]]:
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

            source_sections_text = "\n\n---\n\n".join(planned.source_sections)
            source_sections_text = source_sections_text[:3000]
            existing_trimmed = existing_content[:2000]

            candidates = self.fs.build_link_candidates(project=planned.project)
            link_lines = []
            for c in candidates[:15]:
                alias_str = "; ".join(c["aliases"][:3])
                link_lines.append(
                    f"- [[{c['slug']}]] — {c['title']}; aliases: {alias_str}"
                )
            link_candidates_text = "\n".join(link_lines) if link_lines else "(no candidates yet)"

            prompt = STEP2_PROMPT.format(
                planned_page_json=json.dumps(
                    planned_page_to_dict(planned), ensure_ascii=False, indent=2
                ),
                source_sections=source_sections_text,
                existing_content=existing_trimmed,
                link_candidates=link_candidates_text,
                today=today,
                confidence=planned.confidence,
                sources_count=planned.sources_count,
                char_limit=char_limit,
            )

            system = build_system_prompt(STEP2_SYSTEM, agents_md, skills)
            raw = self.llm.call(
                system=system,
                prompt=prompt,
                temperature=0.1,
                json_mode=True,
                max_tokens=2500,
            )

            try:
                page_data = parse_json_response(raw, context=f"Step2 {planned.slug}")
            except ValueError:
                logger.warning("Step2 JSON parse failed for %s, retrying once", planned.slug)
                retry_prompt = prompt + "\n\nIMPORTANT: Your previous response was not valid JSON. Output ONLY valid JSON."
                raw = self.llm.call(
                    system=system,
                    prompt=retry_prompt,
                    temperature=0.0,
                    json_mode=True,
                    max_tokens=2500,
                )
                page_data = parse_json_response(raw, context=f"Step2 retry {planned.slug}")

            meta = page_data.get("meta", {})
            content = page_data.get("content", "")

            if not meta.get("created"):
                meta["created"] = today

            all_candidates = self.fs.build_link_candidates()
            content = auto_link(content, all_candidates, current_slug=planned.slug)

            if action == "create":
                try:
                    self.fs.write_page(
                        slug=planned.slug, meta=meta, content=content,
                    )
                    pages_created.append(planned.slug)
                    logger.info("Step2 created: slug=%s", planned.slug)
                except Exception as exc:
                    logger.warning("Step2 create failed: slug=%s error=%s",
                                   planned.slug, exc)
                    self._log_failed_ingest(analysis)
                continue

            frontmatter_and_content = render_page_raw(meta, content)
            pending_updates.append({
                "slug": planned.slug,
                "content": frontmatter_and_content,
                "action": action,
            })

        if pending_updates:
            draft_id = f"ingest-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            plan = {
                "summary": f"{len(pending_updates)} page(s) to update from {analysis.source_file}",
                "source_file": analysis.source_file,
                "project": analysis.project,
                "actions": [
                    {"slug": u["slug"], "action": u["action"]}
                    for u in pending_updates
                ],
            }
            pages_to_write = {
                u["slug"]: u["content"] for u in pending_updates
            }
            self.fs.create_draft(
                draft_id=draft_id,
                plan=plan,
                pages=pages_to_write,
                conflicts=[],
            )
            for u in pending_updates:
                if u["action"] in ("update", "supersede"):
                    pages_updated.append(u["slug"])
                    logger.info("Draft queued: slug=%s action=%s draft=%s",
                                u["slug"], u["action"], draft_id)

        return pages_created, pages_updated, pages_superseded

    def _log_failed_ingest(self, analysis: AnalysisResult) -> None:
        self.fs.append_log(IngestLog(
            timestamp=now_iso(),
            source_file=analysis.source_file,
            project=analysis.project,
            pages_created=[], pages_updated=[],
            conflicts_detected=[], skills_triggered=[],
            char_delta=0,
        ))

    # ── Conflict recording ───────────────────────────────────────

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
                context_a=conflict.context_existing[:300],
                context_b=conflict.context_source[:300],
                suggested_options=conflict.suggested_options,
            )
            self.fs.append_conflict(entry)
            assigned_ids.append(cid)

        return assigned_ids

    # ── Skill extraction (called after human resolves conflict) ──

    def extract_skill_from_resolution(
        self,
        conflict_id: str,
        resolution: str,
        user_comment: str,
    ) -> str:
        conflicts_raw = self.fs.read_conflicts_raw()
        pattern = rf"## \[(?:OPEN|RESOLVED)\] {re.escape(conflict_id)}(.*?)(?=\n---|\Z)"
        match = re.search(pattern, conflicts_raw, re.DOTALL)
        conflict_summary = match.group(1).strip() if match else conflict_id

        raw = self.llm.call(
            system="You extract reusable rules from conflict resolutions.",
            prompt=SKILL_EXTRACTION_PROMPT.format(
                conflict_summary=conflict_summary[:800],
                resolution=resolution,
                user_comment=user_comment,
            ),
            temperature=0.1,
        )

        data = parse_json_response(raw, context="skill extraction")
        section = data.get("section", "Conflict Resolution Patterns")
        rule = data.get("rule", "")

        if rule:
            self.fs.append_skill(section=section, skill_text=rule)

        self.fs.resolve_conflict(
            conflict_id=conflict_id,
            resolution=resolution,
            user_comment=user_comment,
            skill_extracted=rule,
        )

        return rule

    # ── Rebuild ──────────────────────────────────────────────────

    def rebuild_from_scratch(self, progress_callback=None) -> dict:
        logger.info("Rebuild started")

        raw_files = self.fs.list_raw_files()

        removed = self.fs.cleanup_orphan_conflicts(raw_files)
        if removed:
            logger.info("Rebuild: removed %d orphan conflicts", removed)

        self.fs.full_reset_wiki()

        self.fs.defer_index()
        raw_files.sort(key=lambda p: (
            "0" if self.fs.get_raw_project(p) == "_general" else "1",
            str(p)
        ))
        logger.info("Rebuild: %d raw files to process", len(raw_files))

        results = {
            "total": len(raw_files),
            "success": 0,
            "failed": 0,
            "errors": [],
            "conflict_ids": [],
        }

        for i, raw_path in enumerate(raw_files):
            rel = str(raw_path.relative_to(self.fs.raw_dir))
            if progress_callback:
                progress_callback(i + 1, len(raw_files), rel)

            result = self.run(rel)
            if result.success:
                results["success"] += 1
                results["conflict_ids"] += result.conflict_ids
            else:
                results["failed"] += 1
                results["errors"].append({
                    "file": rel,
                    "error": result.error,
                })
                logger.warning("Rebuild failed: file=%s error=%s", rel, result.error)

        logger.info("Rebuild complete: success=%d failed=%d conflicts=%d",
                     results["success"], results["failed"], len(results["conflict_ids"]))

        self.fs.resume_index()
        self.fs.rebuild_index()

        return results

    # ── Helpers ──────────────────────────────────────────────────

    def _read_agents_md(self) -> str:
        path = self.fs.root / "AGENTS.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _char_limit_for_type(self, page_type: str) -> int:
        return {
            "entity":  self.settings.limits.entity_page_chars,
            "concept": self.settings.limits.concept_page_chars,
        }.get(page_type, self.settings.limits.entity_page_chars)
