"""
audit_agent.py — Parallel structural + optional LLM semantic audit.

Architecture: Graph Agent via asyncio.gather
Each check is an independent async node. All run in parallel.
LLM semantic audit is opt-in (expensive).

Structural checks (WikiLinter — always run, free):
  - broken_wikilink, broken_path_link, missing_anchor
  - orphan_page, missing_frontmatter, char_limit
  - superseded_active, stale_page, duplicate_title

LLM semantic checks (opt-in):
  - factual_contradiction  — same fact stated differently across pages
  - duplicate_content      — two pages covering same topic
  - missing_backlinks      — entity mentioned by name but not linked
  - stale_facts            — dates/versions that look outdated

Output: AuditReport with all issues, actionable fix hints.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from app.config import Settings
from app.core.linter import LintReport, WikiLinter
from app.core.llm_client import LLMClient
from app.core.token_budget import ContextBudget
from app.core.wiki_fs import WikiFS, WikiPage

logger = logging.getLogger("wiki.audit")


# ─────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────

@dataclass
class SemanticIssue:
    """Issue found by LLM semantic audit."""
    kind: str        # "factual_contradiction"|"duplicate_content"|
                     # "missing_backlink"|"stale_fact"
    slugs: list[str] # pages involved
    detail: str
    severity: str    # "error" | "warning" | "info"
    fix_hint: str
    auto_conflict: bool = False
    # True → agent should auto-create ConflictEntry for this issue


@dataclass
class AuditReport:
    ran_at: str
    total_pages: int
    structural: LintReport
    semantic: list[SemanticIssue] = field(default_factory=list)
    llm_audit_ran: bool = False
    duration_seconds: float = 0.0

    @property
    def all_errors(self) -> list:
        return self.structural.errors + [
            s for s in self.semantic if s.severity == "error"
        ]

    @property
    def total_issues(self) -> int:
        return len(self.structural.issues) + len(self.semantic)

    def summary(self) -> str:
        s = self.structural.summary()
        if self.llm_audit_ran:
            s += f" Semantic: {len(self.semantic)} issues."
        return s

    def to_dict(self) -> dict:
        return {
            "ran_at": self.ran_at,
            "total_pages": self.total_pages,
            "duration_seconds": self.duration_seconds,
            "llm_audit_ran": self.llm_audit_ran,
            "summary": self.summary(),
            "structural": {
                "total": len(self.structural.issues),
                "errors": len(self.structural.errors),
                "warnings": len(self.structural.warnings),
                "by_kind": {
                    kind: [str(i) for i in issues]
                    for kind, issues in self.structural.by_kind.items()
                },
            },
            "semantic": [
                {
                    "kind": s.kind,
                    "slugs": s.slugs,
                    "detail": s.detail,
                    "severity": s.severity,
                    "fix_hint": s.fix_hint,
                }
                for s in self.semantic
            ],
        }


# ─────────────────────────────────────────────
# LLM Audit Prompts
# ─────────────────────────────────────────────

SEMANTIC_AUDIT_SYSTEM = """You are a wiki quality auditor.
Find semantic issues across wiki pages: contradictions, duplicates,
missing links, stale facts.

Output ONLY valid JSON list of issues. Empty list [] if nothing found.
"""

SEMANTIC_AUDIT_PROMPT = """## Wiki Pages to Audit
Project: {project}
Pages ({count}):

{pages_content}

## Find these issues:
1. factual_contradiction: same entity/fact stated differently across pages
   (different values, dates, versions for the same thing)
2. duplicate_content: two pages describe the same topic with >70% overlap
3. missing_backlink: entity mentioned by name (e.g. "Redis") but not linked
   as [[slug]] — only flag if a page for that entity exists in wiki
4. stale_fact: version number or date that appears outdated compared to
   other pages in this set

## Output JSON list:
[
  {{
    "kind": "factual_contradiction"|"duplicate_content"|
            "missing_backlink"|"stale_fact",
    "slugs": [str],       // affected page slugs
    "detail": str,        // specific description with quotes from pages
    "severity": "error"|"warning"|"info",
    "fix_hint": str,
    "auto_conflict": bool // true only for factual_contradiction
  }}
]
"""

MISSING_BACKLINK_CODE = """
# Find entity names mentioned without [[wikilink]] in a given page
import re

page_slug = {slug!r}
page_content = {content!r}
all_slugs = {all_slugs!r}  # list of all wiki slugs

# Build name→slug map from page titles
# Use slug last segment as entity name hint
entity_names = {{
    slug.split('/')[-1].replace('-', ' '): slug
    for slug in all_slugs
}}

existing_links = set(re.findall(r'\\[\\[([^\\]|#]+)', page_content))
issues = []

for name, target_slug in entity_names.items():
    if target_slug == page_slug:
        continue
    if target_slug in existing_links:
        continue
    # Check if name appears in content as plain text (case-insensitive)
    pattern = r'\\b' + re.escape(name) + r'\\b'
    if re.search(pattern, page_content, re.IGNORECASE):
        issues.append({{'name': name, 'target_slug': target_slug}})

result = issues[:10]  # cap at 10 per page
"""


# ─────────────────────────────────────────────
# AuditAgent
# ─────────────────────────────────────────────

class AuditAgent:
    """
    Parallel audit agent using asyncio.gather.

    Usage:
        agent = AuditAgent(fs, llm, settings)

        # Structural only (fast, free)
        report = await agent.run(llm_audit=False)

        # Full audit with LLM semantic checks
        report = await agent.run(llm_audit=True)

        # Sync wrapper for non-async contexts
        report = agent.run_sync(llm_audit=False)
    """

    def __init__(
        self,
        fs: WikiFS,
        llm: LLMClient,
        settings: Settings,
    ):
        self.fs = fs
        self.llm = llm
        self.settings = settings
        self.budget = ContextBudget()

    # ── Public entrypoints ───────────────────────────────────────

    async def run(
        self,
        llm_audit: bool = False,
        project: str | None = None,
    ) -> AuditReport:
        start = datetime.now()
        all_pages = self.fs.list_pages(project=project)
        logger.info("Audit started: pages=%d llm_audit=%s project=%s",
                     len(all_pages), llm_audit, project or "all")

        structural_task = asyncio.to_thread(
            self._run_structural_lint, all_pages
        )

        if llm_audit:
            clusters = self._build_clusters(all_pages)
            logger.info("LLM audit: %d clusters", len(clusters))
            semantic_tasks = [
                asyncio.to_thread(self._run_semantic_audit, cluster)
                for cluster in clusters
            ]
            results = await asyncio.gather(
                structural_task, *semantic_tasks,
                return_exceptions=True
            )
            structural: LintReport = results[0]
            semantic_results = results[1:]
            semantic: list[SemanticIssue] = []
            for r in semantic_results:
                if isinstance(r, list):
                    semantic.extend(r)
        else:
            structural = await structural_task
            semantic = []

        duration = (datetime.now() - start).total_seconds()

        if llm_audit:
            await asyncio.to_thread(
                self._auto_create_conflicts, semantic
            )

        logger.info("Audit complete: duration=%.1fs structural_issues=%d semantic_issues=%d",
                     duration, len(structural.issues), len(semantic))

        return AuditReport(
            ran_at=datetime.now().isoformat(timespec="seconds"),
            total_pages=len(all_pages),
            structural=structural,
            semantic=semantic,
            llm_audit_ran=llm_audit,
            duration_seconds=duration,
        )

    def run_sync(
        self,
        llm_audit: bool = False,
        project: str | None = None,
    ) -> AuditReport:
        """Synchronous wrapper. Use in CLI or non-async contexts."""
        return asyncio.run(self.run(llm_audit=llm_audit, project=project))

    # ── Structural audit node ────────────────────────────────────

    def _run_structural_lint(self, pages: list[WikiPage]) -> LintReport:
        """Runs WikiLinter on provided pages. Pure reads, no LLM."""
        linter = WikiLinter(self.fs, self.settings)
        # Pass slugs to run incremental mode on specific pages
        return linter.lint(slugs=[p.slug for p in pages])

    # ── Semantic audit nodes ─────────────────────────────────────

    def _run_semantic_audit(
        self,
        pages: list[WikiPage],
    ) -> list[SemanticIssue]:
        """
        LLM semantic audit for a cluster of related pages.
        Called in thread pool (not async itself).
        """
        if not pages:
            return []

        project = pages[0].project
        # Fit pages into LLM context budget
        contents = [p.raw for p in pages]
        fitted_contents = self.budget.fit_wiki_pages(contents)
        pages_text = "\n\n---\n\n".join(fitted_contents)

        raw = self.llm.call(
            system=SEMANTIC_AUDIT_SYSTEM,
            prompt=SEMANTIC_AUDIT_PROMPT.format(
                project=project,
                count=len(pages),
                pages_content=pages_text,
            ),
            temperature=0.1,
        )

        try:
            items = _parse_json_list(raw)
        except ValueError:
            return []

        issues = []
        for item in items:
            kind = item.get("kind", "")
            if kind not in {"factual_contradiction", "duplicate_content",
                            "missing_backlink", "stale_fact"}:
                continue
            issues.append(SemanticIssue(
                kind=kind,
                slugs=list(item.get("slugs", [])),
                detail=str(item.get("detail", "")),
                severity=str(item.get("severity", "warning")),
                fix_hint=str(item.get("fix_hint", "")),
                auto_conflict=bool(item.get("auto_conflict", False)),
            ))
        return issues

    # ── Auto-conflict creation ───────────────────────────────────

    def _auto_create_conflicts(self, issues: list[SemanticIssue]) -> None:
        """
        For semantic issues with auto_conflict=True,
        create ConflictEntry in conflicts.md automatically.
        Avoids duplicates by checking existing conflicts.
        """
        import re as _re

        from app.core.wiki_fs import ConflictEntry

        existing_raw = self.fs.read_conflicts_raw()
        ids = _re.findall(r"CONFLICT-(\d+)", existing_raw)
        next_num = max((int(i) for i in ids), default=0) + 1

        for issue in issues:
            if not issue.auto_conflict:
                continue
            if len(issue.slugs) < 2:
                continue

            # Check if this conflict already exists (same slugs)
            slug_sig = "|".join(sorted(issue.slugs))
            if slug_sig in existing_raw:
                continue

            cid = f"CONFLICT-{next_num:03d}"
            next_num += 1

            page_a = self.fs.read_page(issue.slugs[0])
            page_b = self.fs.read_page(issue.slugs[1]) if len(issue.slugs) > 1 else None

            entry = ConflictEntry(
                id=cid,
                status="OPEN",
                date=datetime.now().date().isoformat(),
                project=page_a.project if page_a else "_general",
                source_file="semantic_audit",
                conflict_type="factual_contradiction",
                page_a_slug=issue.slugs[0],
                page_b_ref=issue.slugs[1] if len(issue.slugs) > 1 else "unknown",
                context_a=page_a.content[:300] if page_a else "",
                context_b=page_b.content[:300] if page_b else "",
                suggested_options=[
                    f"Trust {issue.slugs[0]}",
                    f"Trust {issue.slugs[1]}" if len(issue.slugs) > 1 else "Manual review",
                    "Both are true in different contexts",
                ],
            )
            self.fs.append_conflict(entry)

    # ── Cluster builder ──────────────────────────────────────────

    def _build_clusters(
        self,
        pages: list[WikiPage],
        max_cluster_chars: int = 18_000,
    ) -> list[list[WikiPage]]:
        """
        Group pages into clusters for LLM audit.
        Cluster by project first, then split if too large for context.
        Returns list of page groups.
        """
        # Group by project
        by_project: dict[str, list[WikiPage]] = {}
        for page in pages:
            by_project.setdefault(page.project, []).append(page)

        clusters = []
        for proj_pages in by_project.values():
            # Split project pages into context-sized chunks
            current_cluster: list[WikiPage] = []
            current_size = 0

            for page in proj_pages:
                if current_size + page.char_count > max_cluster_chars:
                    if current_cluster:
                        clusters.append(current_cluster)
                    current_cluster = [page]
                    current_size = page.char_count
                else:
                    current_cluster.append(page)
                    current_size += page.char_count

            if current_cluster:
                clusters.append(current_cluster)

        return clusters


# ─────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────

def _parse_json_list(raw: str) -> list:
    raw = raw.strip()
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    block = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    if block:
        return json.loads(block.group(1))
    start, end = raw.find("["), raw.rfind("]")
    if start != -1 and end != -1:
        return json.loads(raw[start:end + 1])
    raise ValueError(f"No JSON list found in: {raw[:200]}")