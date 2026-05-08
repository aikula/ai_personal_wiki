"""
ingest_helpers.py — Module-level helpers for the ingest pipeline.

JSON parsing, schema conversion, page rendering, and system prompt assembly.
All pure functions — no agent state required.
"""

from __future__ import annotations

import json
import re

import yaml

from app.agents.ingest_types import (
    AnalysisResult,
    DetectedConflict,
    PlannedPage,
)


def build_system_prompt(base: str, agents_md: str, skills: str) -> str:
    parts = [base]
    if agents_md:
        parts.append(f"## Domain Instructions (AGENTS.md)\n{agents_md}")
    if skills:
        parts.append(f"## Skills (BINDING RULES)\n{skills}")
    return "\n\n".join(parts)


def parse_json_response(raw: str, context: str = "") -> dict:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if block:
        try:
            return json.loads(block.group(1))
        except json.JSONDecodeError:
            pass

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


def dict_to_analysis_result(data: dict, source_file: str, project: str) -> AnalysisResult:
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
                context_existing=str(item.get("context_existing", ""))[:600],
                context_source=str(item.get("context_source", ""))[:600],
                suggested_options=list(item.get("suggested_options", [])),
                description=str(item.get("description", "")),
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


def planned_page_to_dict(page: PlannedPage) -> dict:
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


REQUIRED_FRONTMATTER_FIELDS = {
    "title", "project", "type", "tags",
    "confidence", "sources", "last_confirmed",
    "supersedes", "superseded_by", "created",
}


def render_page_raw(meta: dict, content: str) -> str:
    # Preserve null values for required frontmatter fields
    filtered_meta = {}
    for k, v in meta.items():
        if v is not None:
            filtered_meta[k] = v
        elif k in REQUIRED_FRONTMATTER_FIELDS:
            filtered_meta[k] = None
    meta_str = yaml.dump(filtered_meta, default_flow_style=False, allow_unicode=True).strip()
    return f"---\n{meta_str}\n---\n{content}\n"


ANALYSIS_SCHEMA_HINT = """{
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
     "description": str,
     "context_existing": str, "context_source": str,
     "suggested_options": [str], "is_cross_project": bool}
  ],
  "skills_triggered": [str],
  "analysis_notes": str
}"""
