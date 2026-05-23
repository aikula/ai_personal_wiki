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
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

from app.config import Settings
from app.core.linter import LintReport
from app.core.llm_client import LLMGateway
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
        llm: LLMGateway,
        settings: Settings,
    ):
        self.fs = fs
        self.llm = llm
        self.settings = settings
        self.budget = ContextBudget(settings)

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
                ran_at=start.isoformat(timespec="seconds"),
                duration_seconds=duration,
                structural=structural,
                semantic=semantic,
            )

        logger.info("Audit complete: duration=%.1fs structural_issues=%d",
                     duration, len(structural.issues))

        return AuditReport(
            ran_at=start.isoformat(timespec="seconds"),
            duration_seconds=duration,
            structural=structural,
            semantic=[],
        )

    # ── Duplicate / collapse audit ──────────────────────────────

    def audit_duplicates(self, project: str | None = None) -> dict:
        """Deterministic duplicate/collapse candidate detection.

        Returns::

            {"candidates": [{"id", "kind", "pages", "titles": {slug: title},
                             "reason", "recommended_action", "score"}],
             "total": int}
        """
        pages = self.fs.list_pages(project=project)
        page_map = {p.slug: p for p in pages}
        candidates: list[dict] = []
        seen: set[str] = set()

        def _make(slugs: list[str], kind: str, reason: str, action: str, score: float) -> dict:
            titles = {}
            for s in slugs:
                p = page_map.get(s)
                titles[s] = p.title if p else s
            return {
                "id": f"{kind}-{len(candidates) + 1:03d}",
                "kind": kind,
                "pages": slugs,
                "titles": titles,
                "reason": reason,
                "recommended_action": action,
                "score": score,
            }

        # 1. Same title (score 0.9) — true duplicates
        by_title: dict[tuple[str, str], list[str]] = {}
        for p in pages:
            if p.page_type in ("index", "log"):
                continue
            key = (p.project, p.title.lower().strip())
            by_title.setdefault(key, []).append(p.slug)
        for (proj, title), slugs in by_title.items():
            if len(slugs) > 1:
                candidates.append(_make(
                    slugs, "duplicate",
                    f"Same title '{title}' in project '{proj}'",
                    "merge or differentiate titles", 0.9,
                ))
                seen.update(slugs)

        # 2. Same source references within same project (score 0.6)
        ref_map: dict[str, list[str]] = {}
        for p in pages:
            if p.page_type in ("index", "log") or p.slug in seen:
                continue
            for ref in p.wikilinks:
                if ref.startswith(p.project):
                    ref_map.setdefault(ref, []).append(p.slug)
        for ref, slugs in ref_map.items():
            if len(slugs) > 1:
                candidates.append(_make(
                    slugs, "overlap",
                    f"Both link to [[{ref}]]",
                    "cross-link or merge", 0.6,
                ))

        # 3. Tag overlap (score 0.5) — only if 3+ shared tags and same project
        for p in pages:
            if p.page_type in ("index", "log") or p.slug in seen:
                continue
            for q in pages:
                if q.slug <= p.slug or q.slug in seen:
                    continue
                if q.project != p.project:
                    continue
                common = set(p.tags) & set(q.tags)
                if len(common) >= 3 and len(p.tags) >= 2 and len(q.tags) >= 2:
                    candidates.append(_make(
                        [p.slug, q.slug], "overlap",
                        f"Shared tags: {common}",
                        "review for merge or cross-link", 0.5,
                    ))

        return {"candidates": candidates, "total": len(candidates)}

    # ── Synthesis queue ──────────────────────────────────────────

    @property
    def synthesis_dir(self) -> Path:
        return self.fs.root / "synthesis_queue"

    def list_synthesis_candidates(self) -> list[dict]:
        """List all pending synthesis/collapse queue items."""
        if not self.synthesis_dir.exists():
            return []
        candidates = []
        for f in sorted(self.synthesis_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                if data:
                    candidates.append(data)
            except Exception:
                pass
        return candidates

    def create_synthesis_candidate(self, candidate: dict) -> str:
        """Store a collapse/synthesis candidate in the queue."""
        self.synthesis_dir.mkdir(parents=True, exist_ok=True)
        cid = candidate.get("id", f"cluster-{len(os.listdir(str(self.synthesis_dir))) + 1:03d}")
        candidate["created"] = date.today().isoformat()
        path = self.synthesis_dir / f"{cid}.yaml"
        path.write_text(yaml.dump(candidate, default_flow_style=False, allow_unicode=True),
                        encoding="utf-8")
        return cid

    def resolve_synthesis_candidate(self, cid: str, action: str) -> bool:
        """Remove or archive a synthesis queue item."""
        path = self.synthesis_dir / f"{cid}.yaml"
        if not path.exists():
            return False
        archive = path.parent / ".archive"
        archive.mkdir(exist_ok=True)
        path.rename(archive / f"{cid}_{action}.yaml")
        return True

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
