"""
ingest_conflicts.py — Standalone conflict management functions for the ingest pipeline.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from app.agents.ingest_helpers import parse_json_response
from app.agents.ingest_prompts import SKILL_EXTRACTION_PROMPT
from app.agents.ingest_types import AnalysisResult
from app.config import Settings, language_instruction
from app.core.wiki_fs import WikiFS
from app.core.wiki_types import ConflictEntry

logger = logging.getLogger("wiki.ingest")


def record_conflicts(fs: WikiFS, settings: Settings, analysis: AnalysisResult) -> list[str]:
    existing_raw = fs.read_conflicts_raw()
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
            context_a=conflict.context_existing[:settings.ingest.conflict_context_limit],
            context_b=conflict.context_source[:settings.ingest.conflict_context_limit],
            suggested_options=conflict.suggested_options,
            description=conflict.description,
            is_cross_project=conflict.is_cross_project,
        )
        fs.append_conflict(entry)
        try_auto_resolve_conflict(fs, cid, conflict.conflict_type, conflict.description)
        assigned_ids.append(cid)
    return assigned_ids


def extract_skill_from_resolution(
    fs: WikiFS,
    llm,
    settings: Settings,
    conflict_id: str,
    resolution: str,
    user_comment: str,
) -> str:
    conflicts_raw = fs.read_conflicts_raw()
    pattern = rf"## \[(?:OPEN|RESOLVED)\] {re.escape(conflict_id)}(.*?)(?=\n---|\Z)"
    match = re.search(pattern, conflicts_raw, re.DOTALL)
    conflict_summary = match.group(1).strip() if match else conflict_id
    lang_rule = language_instruction(settings)
    raw = llm.call(
        system="You extract reusable rules from conflict resolutions.",
        prompt=SKILL_EXTRACTION_PROMPT.format(
            conflict_summary=conflict_summary[:settings.ingest.skill_extraction_limit],
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
        fs.append_skill(section=section, skill_text=rule)
    fs.resolve_conflict(conflict_id=conflict_id, resolution=resolution, user_comment=user_comment, skill_extracted=rule)
    return rule


def try_auto_resolve_conflict(
    fs: WikiFS,
    conflict_id: str,
    conflict_type: str,
    description: str,
) -> bool:
    """Try to auto-resolve a conflict using skills.md rules. Returns True if resolved."""
    skills_raw = fs.read_skills()
    if not skills_raw:
        return False

    type_lower = conflict_type.lower()
    desc_lower = (description or "").lower()

    stop_terms = {
        "conflict", "конфликт", "conflicts", "конфликты",
        "different", "разные", "difference", "различие",
        "page", "страница", "source", "источник",
        "update", "обновление", "data", "данные",
        "factual", "фактический", "resolution", "решение",
    }

    for line in skills_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue
        line_lower = line.lower()
        conflict_terms = type_lower.replace("_", " ").split() + desc_lower.split()[:8]
        specific_terms = [
            t for t in conflict_terms
            if len(t) > 3 and t not in stop_terms
        ]
        if len(specific_terms) < 2:
            continue
        match_count = sum(1 for term in specific_terms if term in line_lower)
        if match_count >= 3:
            resolution = f"auto_skill: {line}"
            fs.resolve_conflict(
                conflict_id=conflict_id,
                resolution=resolution,
                user_comment="Auto-resolved by skill matching",
                skill_extracted="",
            )
            logger.info("Auto-resolved %s via skill: %s", conflict_id, line[:80])
            return True
    return False


def record_single_conflict(
    fs: WikiFS,
    settings: Settings,
    conflict_data: dict,
    project: str,
    source_path: str,
) -> str | None:
    """Record a single conflict from chunk analysis."""
    try:
        existing_raw = fs.read_conflicts_raw()
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
            context_a=conflict_data.get("context_existing", "")[:settings.ingest.conflict_context_limit],
            context_b=conflict_data.get("context_source", "")[:settings.ingest.conflict_context_limit],
            suggested_options=conflict_data.get("suggested_options", []),
            description=conflict_data.get("description", ""),
            is_cross_project=conflict_data.get("is_cross_project", False),
        )
        fs.append_conflict(entry)
        try_auto_resolve_conflict(
            fs,
            cid,
            conflict_data.get("conflict_type", ""),
            conflict_data.get("description", ""),
        )
        return cid
    except Exception as exc:
        logger.error("Failed to record conflict: %s", exc)
        return None
