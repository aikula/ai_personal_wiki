"""
ingest_generate.py — Standalone functions for generating wiki pages from merge analysis.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime

from app.agents.ingest_conflicts import record_single_conflict
from app.agents.ingest_helpers import (
    build_system_prompt,
    char_limit_for_type,
    derive_tags,
    format_source_sections,
    parse_json_response,
    read_agents_md,
    render_page_raw,
)
from app.agents.ingest_prompts import STEP2_PROMPT, STEP2_SYSTEM
from app.core.large_source_types import MergeAnalysisResult
from app.core.utils import normalize_wikilinks, slugify
from app.core.wiki_fs import WikiFS

logger = logging.getLogger("wiki.ingest")


def generate_single_page(
    fs: WikiFS,
    llm,
    settings,
    slug: str,
    project: str,
    source_sections: list[str],
    source_file: str,
    existing_content: str,
    action: str,
    force_char_limit: int | None = None,
) -> tuple[dict, str]:
    skills = fs.read_skills()
    agents_md = read_agents_md(fs.root)
    today = date.today().isoformat()
    char_limit = force_char_limit or char_limit_for_type("entity", settings)

    planned_page = {
        "slug": slug,
        "title": slug.split("/")[-1].replace("-", " ").title(),
        "project": project,
        "page_type": "entity",
        "tags": [project],
        "action": action,
    }

    candidates = fs.build_link_candidates()
    link_lines = []
    for c in candidates[:settings.ingest.link_candidates_limit]:
        alias_str = "; ".join(c["aliases"][:settings.ingest.link_aliases_per_candidate])
        link_lines.append(f"- [[{c['slug']}]] — {c['title']}; aliases: {alias_str}")
    link_candidates_text = "\n".join(link_lines) if link_lines else "(no candidates yet)"

    source_sections_text = format_source_sections(source_sections, settings)

    prompt = STEP2_PROMPT.format(
        planned_page_json=json.dumps(planned_page, ensure_ascii=False, indent=2),
        source_file=source_file,
        source_sections=source_sections_text,
        existing_content=existing_content[:settings.ingest.existing_content_limit],
        link_candidates=link_candidates_text,
        today=today,
        confidence=settings.ingest.default_confidence,
        sources_count=1,
        char_limit=char_limit,
    )

    system = build_system_prompt(STEP2_SYSTEM, agents_md, skills)
    raw = llm.call(system=system, prompt=prompt, temperature=0.1, json_mode=True, max_tokens=settings.ingest.max_completion_tokens)

    try:
        page_data = parse_json_response(raw, context=f"Step2 {slug}")
    except ValueError:
        retry_max_tokens = settings.ingest.max_completion_tokens * 2
        retry_prompt = prompt + "\n\nIMPORTANT: Your previous response was not valid JSON. Output ONLY valid JSON."
        raw = llm.call(system=system, prompt=retry_prompt, temperature=settings.ingest.retry_temperature, json_mode=True, max_tokens=retry_max_tokens)
        try:
            page_data = parse_json_response(raw, context=f"Step2 retry {slug}")
        except ValueError:
            raw = llm.call(system=system, prompt=retry_prompt, temperature=settings.ingest.retry_temperature, json_mode=True, max_tokens=settings.llm.max_completion_tokens)
            page_data = parse_json_response(raw, context=f"Step2 fallback {slug}")

    meta = page_data.get("meta", {})
    content = page_data.get("content", "")
    if not meta.get("created"):
        meta["created"] = today
    meta["project"] = project
    if not meta.get("tags"):
        meta["tags"] = derive_tags(slug, source_file)

    existing_slugs = {p.slug for p in fs.list_pages()}
    content = normalize_wikilinks(content, existing_slugs)

    return meta, content


def generate_from_merge(
    fs: WikiFS,
    llm,
    settings,
    merged: MergeAnalysisResult,
    project: str,
    source_content: str,
    allow_draft: bool,
) -> tuple[list[str], list[str], list[str], list[str]]:
    pages_created = []
    pages_updated = []
    pages_superseded = []
    conflict_ids = []

    for conflict_data in merged.all_conflicts:
        cid = record_single_conflict(fs, settings, conflict_data, project, merged.source_path)
        if cid:
            conflict_ids.append(cid)

    total_pages = len(merged.all_candidate_pages)
    require_review = allow_draft and total_pages > settings.ingest.require_review_if_pages_gt
    if require_review:
        logger.warning(
            "Large source: %d pages planned exceeds review threshold (%d). "
            "Writing draft for review instead of applying pages.",
            total_pages, settings.ingest.require_review_if_pages_gt,
        )
        pending_draft: dict[str, str] = {}

    for i, page_info in enumerate(merged.all_candidate_pages):
        if not require_review and i >= settings.ingest.max_auto_write_pages:
            logger.warning(
                "Reached max_auto_write_pages limit (%d). "
                "Remaining %d pages need manual ingest.",
                settings.ingest.max_auto_write_pages,
                total_pages - i,
            )
            break

        slug = slugify(page_info["slug"])
        existing = fs.read_page(slug)
        action = "update" if existing else "create"

        try:
            page_meta, page_content = generate_single_page(
                fs=fs, llm=llm, settings=settings,
                slug=slug, project=project,
                source_sections=page_info.get("source_sections", []),
                source_file=merged.source_path,
                existing_content=existing.raw if existing else "",
                action=action,
            )
            if require_review:
                pending_draft[slug] = render_page_raw(page_meta, page_content)
                continue
            fs.write_page(
                slug=slug, meta=page_meta, content=page_content,
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
                    compact_meta, compact_content = generate_single_page(
                        fs=fs, llm=llm, settings=settings,
                        slug=slug, project=project,
                        source_sections=page_info.get("source_sections", [])[:1],
                        source_file=merged.source_path,
                        existing_content="",
                        action=action,
                        force_char_limit=settings.limits.entity_page_chars,
                    )
                    if require_review:
                        pending_draft[slug] = render_page_raw(compact_meta, compact_content)
                    else:
                        fs.write_page(slug=slug, meta=compact_meta, content=compact_content, allow_overwrite=True)
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
        fs.create_draft(
            draft_id=draft_id,
            plan={
                "summary": f"{len(pending_draft)} page(s) planned from large source {merged.source_path}",
                "source_file": merged.source_path,
                "project": project,
                "review_reason": (
                    f"planned pages {total_pages} exceed threshold "
                    f"{settings.ingest.require_review_if_pages_gt}"
                ),
                "actions": [
                    {"slug": slug, "action": "update" if fs.read_page(slug) else "create"}
                    for slug in pending_draft
                ],
            },
            pages=pending_draft,
            conflicts=merged.all_conflicts,
        )

    return pages_created, pages_updated, pages_superseded, conflict_ids


def retry_compact_page(fs, llm, settings, planned, system, agents_md, skills, today, analysis, action, pages_created, pages_updated):
    compact_prompt = STEP2_PROMPT.format(
        planned_page_json=f'{{"slug": "{planned.slug}", "action": "{action}"}}',
        source_file=analysis.source_file,
        source_sections="(compacted — produce concise output)",
        existing_content="",
        link_candidates="",
        today=today,
        confidence=planned.confidence,
        sources_count=1,
        char_limit=settings.limits.entity_page_chars,
    ) + "\n\nIMPORTANT: Keep the content VERY concise. Under 2500 chars of body text. No long explanations."
    try:
        raw = llm.call(system=system, prompt=compact_prompt, temperature=settings.ingest.retry_temperature, json_mode=True, max_tokens=settings.ingest.max_completion_tokens)
        page_data = parse_json_response(raw, context=f"Step2 compact retry {planned.slug}")
        meta = page_data.get("meta", {})
        content = page_data.get("content", "")
        if not meta.get("created"):
            meta["created"] = today
        meta["project"] = planned.slug.split("/")[0] if "/" in planned.slug else analysis.project
        fs.write_page(slug=planned.slug, meta=meta, content=content, allow_overwrite=True)
        if action == "create":
            pages_created.append(planned.slug)
        else:
            pages_updated.append(planned.slug)
        return True
    except Exception:
        return False
