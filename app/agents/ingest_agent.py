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
import yaml
from dataclasses import dataclass, field
from datetime import date, datetime

from app.config import Settings
from app.core.interpreter import CodeInterpreter
from app.core.linter import LintReport, WikiLinter
from app.core.llm_client import LLMClient
from app.core.token_budget import ContextBudget
from app.core.utils import auto_link, now_iso
from app.core.wiki_fs import ConflictEntry, IngestLog, WikiFS, WikiPage

logger = logging.getLogger("wiki.ingest")


# ─────────────────────────────────────────────
# Typed schema for Step 1: Analysis
# ─────────────────────────────────────────────

@dataclass
class PlannedPage:
    """
    A single page the agent plans to create or update.
    Produced in Step 1, consumed in Step 2.
    """
    slug: str               # e.g. "myapp/cache/redis"
    title: str
    project: str
    page_type: str          # "entity" | "concept"
    tags: list[str]
    action: str             # "create" | "update" | "supersede"
    supersedes: str | None = None   # slug of old page if action=supersede
    source_sections: list[str] = field(default_factory=list)
    # Raw text fragments from source that feed this page.
    # Agent uses these in Step 2 to generate content.
    confidence: float = 1.0
    sources_count: int = 1


@dataclass
class DetectedConflict:
    """
    Conflict detected during Step 1 analysis.
    Not a blocker — ingest continues for non-conflicting pages.
    """
    conflict_type: str      # "factual_contradiction" | "version_mismatch"
                            # | "structural_overlap" | "cross_project_difference"
    existing_slug: str      # wiki page involved
    source_ref: str         # reference to source fragment (e.g. "line 42")
    context_existing: str   # first 300 chars of existing wiki page
    context_source: str     # first 300 chars of conflicting source fragment
    suggested_options: list[str]
    is_cross_project: bool = False
    # cross_project conflicts are recorded with type cross_project_difference
    # and do NOT block ingest — they are informational


@dataclass
class AnalysisResult:
    """
    Output of Step 1 (Analysis pass).
    This is the contract between Step 1 and Step 2.
    Agent MUST NOT write anything to wiki before this is complete.
    """
    source_file: str
    project: str
    pages_to_create: list[PlannedPage] = field(default_factory=list)
    pages_to_update: list[PlannedPage] = field(default_factory=list)
    pages_to_supersede: list[PlannedPage] = field(default_factory=list)
    conflicts: list[DetectedConflict] = field(default_factory=list)
    skills_triggered: list[str] = field(default_factory=list)
    # Names of skills from skills.md that influenced this analysis
    analysis_notes: str = ""
    # Free-form notes from LLM about what it found (for log)


@dataclass
class IngestResult:
    """
    Final result returned from IngestAgent.run().
    Consumed by API layer to build response.
    """
    success: bool
    source_file: str
    project: str
    pages_created: list[str]    # slugs
    pages_updated: list[str]    # slugs
    pages_superseded: list[str] # slugs
    conflict_ids: list[str]     # CONFLICT-NNN
    skills_triggered: list[str]
    lint_report: LintReport | None
    error: str | None = None
    analysis_notes: str = ""


# ─────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────

STEP1_SYSTEM = """You are a wiki knowledge engineer.
Your task is to ANALYZE a source document and PLAN wiki updates.
Do NOT generate wiki content yet. Only plan.

LANGUAGE: The wiki is in Russian. Plan page titles and tags accordingly —
use Russian for titles (e.g. "Кеширование сессий"), slugs stay English (e.g. "session-caching").

You will receive:
- AGENTS.md: domain instructions
- skills.md: accumulated rules (BINDING — follow them)
- wiki_context: relevant existing wiki pages
- source: the document to analyze

Output ONLY valid JSON matching AnalysisResult schema.
No prose before or after the JSON block.
"""

STEP1_PROMPT = """## Source File
Name: {source_file}
Project: {project}

## Source Content
{source_content}

## Existing Wiki Pages (potentially related)
{wiki_context}

## Task
Analyze the source and produce AnalysisResult JSON with:

1. pages_to_create: list of PlannedPage for new entities/concepts found
2. pages_to_update: list of PlannedPage for existing pages to update
3. pages_to_supersede: list of PlannedPage where existing page is outdated
4. conflicts: list of DetectedConflict for contradictions with existing wiki
5. skills_triggered: which skills from skills.md influenced this analysis
6. analysis_notes: brief summary of what you found

Rules:
- Max {max_pages} pages total across create+update+supersede
- Each PlannedPage.slug format: "{project}/category/page_name"
  Use lowercase, hyphens for spaces. Example: "myapp/storage/redis-cache"
- For pages_to_update: slug MUST match existing page slug exactly
- source_sections: copy relevant text fragments verbatim (max 1500 chars each)
- If two projects implement same thing differently: conflict_type = "cross_project_difference"
  is_cross_project = true — this is NOT a real conflict, do not block
- confidence: your certainty this page deserves to exist (0.0-1.0)

AnalysisResult JSON schema:
{schema}
"""

STEP2_SYSTEM = """You are a wiki content writer.
Your task is to GENERATE wiki page content based on analysis results.

LANGUAGE RULE (BINDING):
- All wiki content MUST be written in Russian.
- Keep technical terms, product names, acronyms, and code in their original form (English).
- Use Russian for explanations, descriptions, headings, and prose.
- Examples: "Redis кеш используется для хранения сессий", "FastAPI middleware обрабатывает запросы".

You will receive:
- One PlannedPage specification
- Source sections assigned to this page
- Existing page content (if updating)
- Link candidate list — known wiki pages for cross-referencing
- AGENTS.md and skills.md for conventions

Output ONLY valid JSON: {{"meta": {{...}}, "content": "..."}}
meta must include ALL required frontmatter fields.
content is Markdown body (no frontmatter block — that goes in meta).
No prose before or after JSON.
"""

STEP2_PROMPT = """## Planned Page
{planned_page_json}

## Source Sections for This Page
{source_sections}

## Existing Page Content (empty if creating new)
{existing_content}

## Known Wiki Pages / Link Candidates
{link_candidates}

## Today's Date
{today}

Generate the wiki page. Rules:
- LANGUAGE: Write all content in Russian. Keep technical terms, product names, acronyms in English.
- content: Markdown, use [[slug]] for all wiki cross-references
- All internal links MUST use [[slug]] format, never relative paths
- title: concise, matches official naming from source
- tags: 2-5 lowercase tags relevant to content
- confidence: {confidence}
- sources: {sources_count}
- last_confirmed: {today}
- Max content length: {char_limit} chars total (including frontmatter)
- End content with ## Sources section listing source_file
- Include a `synopsis` field (2-3 sentence summary for search/preview)
- Add a `## Связанные страницы` section when link candidates exist (at least 2, project-local first)
- Link known entities/concepts from the candidate list on first meaningful mention
- Do not invent slugs that are not in the candidate list
- Do not link every repeated mention

Output JSON schema:
{{"meta": {{"title": str, "project": str, "type": str, "tags": list,
           "confidence": float, "sources": int, "last_confirmed": str,
           "supersedes": null, "superseded_by": null, "created": str,
           "synopsis": str}},
 "content": str}}
"""

SKILL_EXTRACTION_PROMPT = """A wiki conflict was just resolved by a user.
Extract a reusable rule for skills.md (1-2 sentences, actionable).

Conflict: {conflict_summary}
Resolution chosen: {resolution}
User comment: {user_comment}

Which section does this rule belong to?
Sections: Source Trust Rules | Conflict Resolution Patterns |
          Domain Conventions | Query Formatting Rules | Ingest Patterns

Output JSON: {{"section": str, "rule": str}}
"""


# ─────────────────────────────────────────────
# IngestAgent
# ─────────────────────────────────────────────

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

            # ── Incremental lint ─────────────────────────────
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
            schema=_ANALYSIS_SCHEMA_HINT,
        )

        raw = self.llm.call(
            system=_build_system(STEP1_SYSTEM, agents_md, skills),
            prompt=prompt,
            temperature=0.1,
        )

        data = _parse_json_response(raw, context="Step1 analysis")
        return _dict_to_analysis_result(data, source_file, project)

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

# Extract significant words from source (length > 4, not stopwords)
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

# Sort by overlap, return top 10
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
        """Concatenate page raws for LLM context, respecting budget."""
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

            # Trim context to leave room for JSON response
            source_sections_text = "\n\n---\n\n".join(planned.source_sections)
            source_sections_text = source_sections_text[:3000]
            existing_trimmed = existing_content[:2000]

            # Build compact link candidate list for prompt
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
                    _planned_page_to_dict(planned), ensure_ascii=False, indent=2
                ),
                source_sections=source_sections_text,
                existing_content=existing_trimmed,
                link_candidates=link_candidates_text,
                today=today,
                confidence=planned.confidence,
                sources_count=planned.sources_count,
                char_limit=char_limit,
            )

            system = _build_system(STEP2_SYSTEM, agents_md, skills)
            raw = self.llm.call(
                system=system,
                prompt=prompt,
                temperature=0.1,
                json_mode=True,
                max_tokens=2500,
            )

            try:
                page_data = _parse_json_response(raw, context=f"Step2 {planned.slug}")
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
                page_data = _parse_json_response(raw, context=f"Step2 retry {planned.slug}")

            meta = page_data.get("meta", {})
            content = page_data.get("content", "")

            # Ensure created field
            if not meta.get("created"):
                meta["created"] = today

            # Auto-link post-processing: link known aliases
            all_candidates = self.fs.build_link_candidates()
            content = auto_link(content, all_candidates, current_slug=planned.slug)

            if action == "create":
                # Creates safe to write directly
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

            # Update / supersede → collect for draft
            frontmatter_and_content = _render_page_raw(meta, content)
            pending_updates.append({
                "slug": planned.slug,
                "content": frontmatter_and_content,
                "action": action,
            })

        # Create draft for pending updates/supersedes
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
                conflicts=[],  # conflicts already recorded separately
            )
            for u in pending_updates:
                if u["action"] in ("update", "supersede"):
                    pages_updated.append(u["slug"])
                    logger.info("Draft queued: slug=%s action=%s draft=%s",
                                u["slug"], u["action"], draft_id)

        return pages_created, pages_updated, pages_superseded

    def _log_failed_ingest(self, analysis: AnalysisResult) -> None:
        """Append a minimal log entry for a failed ingest step."""
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
        """
        Write each DetectedConflict to conflicts.md.
        Returns list of assigned conflict IDs.
        Cross-project differences get type=cross_project_difference.
        """
        existing_raw = self.fs.read_conflicts_raw()
        # Find next ID
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
        """
        After human resolves a conflict, extract a reusable skill.
        Appends to skills.md. Returns extracted rule text.

        Called by: API route POST /conflicts/{id}/resolve
        """
        conflicts_raw = self.fs.read_conflicts_raw()
        # Find conflict block
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

        data = _parse_json_response(raw, context="skill extraction")
        section = data.get("section", "Conflict Resolution Patterns")
        rule = data.get("rule", "")

        if rule:
            self.fs.append_skill(section=section, skill_text=rule)

        # Mark conflict resolved
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

        # Remove OPEN conflicts for raw files that no longer exist
        removed = self.fs.cleanup_orphan_conflicts(raw_files)
        if removed:
            logger.info("Rebuild: removed %d orphan conflicts", removed)

        self.fs.full_reset_wiki()
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


# ─────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────

def _build_system(base: str, agents_md: str, skills: str) -> str:
    parts = [base]
    if agents_md:
        parts.append(f"## Domain Instructions (AGENTS.md)\n{agents_md}")
    if skills:
        parts.append(f"## Skills (BINDING RULES)\n{skills}")
    return "\n\n".join(parts)


def _parse_json_response(raw: str, context: str = "") -> dict:
    """
    Extract JSON from LLM response.
    Handles: pure JSON, JSON in ```json block, JSON with prose around it.
    On failure: retry hint is embedded in WikiEngineError.
    """
    # Try direct parse
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try extracting from code block
    block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if block:
        try:
            return json.loads(block.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } span
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"[{context}] Could not parse JSON from LLM response. "
        f"First 200 chars: {raw[:200]}"
    )


def _dict_to_analysis_result(data: dict, source_file: str, project: str) -> AnalysisResult:
    """Validate and convert raw dict to AnalysisResult."""

    def parse_planned(items: list) -> list[PlannedPage]:
        pages = []
        for item in (items or []):
            pages.append(PlannedPage(
                slug=str(item.get("slug", "")),
                title=str(item.get("title", "")),
                project=str(item.get("project", project)),
                page_type=str(item.get("page_type", "entity")),
                tags=list(item.get("tags", [])),
                action=str(item.get("action", "create")),
                supersedes=item.get("supersedes"),
                source_sections=list(item.get("source_sections", [])),
                confidence=float(item.get("confidence", 1.0)),
                sources_count=int(item.get("sources_count", 1)),
            ))
        return pages

    def parse_conflicts(items: list) -> list[DetectedConflict]:
        conflicts = []
        for item in (items or []):
            conflicts.append(DetectedConflict(
                conflict_type=str(item.get("conflict_type", "factual_contradiction")),
                existing_slug=str(item.get("existing_slug", "")),
                source_ref=str(item.get("source_ref", "")),
                context_existing=str(item.get("context_existing", ""))[:300],
                context_source=str(item.get("context_source", ""))[:300],
                suggested_options=list(item.get("suggested_options", [])),
                is_cross_project=bool(item.get("is_cross_project", False)),
            ))
        return conflicts

    return AnalysisResult(
        source_file=source_file,
        project=project,
        pages_to_create=parse_planned(data.get("pages_to_create", [])),
        pages_to_update=parse_planned(data.get("pages_to_update", [])),
        pages_to_supersede=parse_planned(data.get("pages_to_supersede", [])),
        conflicts=parse_conflicts(data.get("conflicts", [])),
        skills_triggered=list(data.get("skills_triggered", [])),
        analysis_notes=str(data.get("analysis_notes", "")),
    )


def _planned_page_to_dict(page: PlannedPage) -> dict:
    return {
        "slug": page.slug,
        "title": page.title,
        "project": page.project,
        "page_type": page.page_type,
        "tags": page.tags,
        "action": page.action,
        "supersedes": page.supersedes,
        "confidence": page.confidence,
        "sources_count": page.sources_count,
    }


def _render_page_raw(meta: dict, content: str) -> str:
    """Render meta dict + markdown content into a full raw page (frontmatter + body)."""
    meta_str = yaml.dump(
        {k: v for k, v in meta.items() if v is not None},
        default_flow_style=False,
        allow_unicode=True,
    ).strip()
    return f"---\n{meta_str}\n---\n{content}\n"


# Schema hint injected into Step 1 prompt
_ANALYSIS_SCHEMA_HINT = """{
  "pages_to_create": [
    {"slug": "project/category/name", "title": str, "project": str,
     "page_type": "entity"|"concept", "tags": [str],
     "action": "create", "supersedes": null,
     "source_sections": [str], "confidence": float, "sources_count": int}
  ],
  "pages_to_update": [ ...same fields, action="update" ],
  "pages_to_supersede": [ ...same fields, action="supersede",
                          "supersedes": "old/slug" ],
  "conflicts": [
    {"conflict_type": str, "existing_slug": str, "source_ref": str,
     "context_existing": str, "context_source": str,
     "suggested_options": [str], "is_cross_project": bool}
  ],
  "skills_triggered": [str],
  "analysis_notes": str
}"""