"""
conflicts.py — Conflict review and resolution routes.

GET  /api/conflicts              — list all conflicts (open + resolved)
GET  /api/conflicts/{id}         — get single conflict
POST /api/conflicts/{id}/resolve — resolve with choice + comment
POST /api/conflicts/{id}/comment — add comment without resolving
"""

from __future__ import annotations

import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import (
    IngestAgent,
    WikiFS,
    get_ingest_agent,
    get_wiki_fs,
)
from app.api.models import (
    AddCommentRequest,
    ConflictOut,
    ConflictsListResponse,
    ResolveConflictRequest,
    ResolveConflictResponse,
)

router = APIRouter(prefix="/api/conflicts", tags=["conflicts"])


@router.get("", response_model=ConflictsListResponse)
async def list_conflicts(
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    """
    Parse conflicts.md and return structured list.
    Open conflicts returned first, sorted by date desc.
    """
    raw = fs.read_conflicts_raw()
    open_conflicts, resolved_conflicts = _parse_conflicts_md(raw)
    return ConflictsListResponse(
        open=open_conflicts,
        resolved=resolved_conflicts,
        total_open=len(open_conflicts),
    )


@router.get("/{conflict_id}", response_model=ConflictOut)
async def get_conflict(
    conflict_id: str,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    """Get details of a single conflict by ID (e.g. CONFLICT-007)."""
    raw = fs.read_conflicts_raw()
    all_c, _ = _parse_conflicts_md(raw)
    _, resolved = _parse_conflicts_md(raw)
    all_parsed = all_c + resolved
    match = next((c for c in all_parsed if c.id == conflict_id), None)
    if not match:
        raise HTTPException(404, f"Конфликт {conflict_id} не найден")
    return match


@router.post("/{conflict_id}/resolve", response_model=ResolveConflictResponse)
async def resolve_conflict(
    conflict_id: str,
    body: ResolveConflictRequest,
    agent: Annotated[IngestAgent, Depends(get_ingest_agent)],
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    """
    Resolve a conflict with user's choice and comment.
    Optionally extracts a skill from the resolution.
    Updates conflicts.md status and appends to skills.md.
    """
    # Verify conflict exists and is OPEN
    raw = fs.read_conflicts_raw()
    if f"## [OPEN] {conflict_id}" not in raw:
        raise HTTPException(
            404,
            f"Конфликт {conflict_id} не найден или уже решён",
        )

    skill = ""
    if body.extract_skill:
        import asyncio
        skill = await asyncio.to_thread(
            agent.extract_skill_from_resolution,
            conflict_id,
            body.resolution,
            body.user_comment,
        )
    else:
        fs.resolve_conflict(
            conflict_id=conflict_id,
            resolution=body.resolution,
            user_comment=body.user_comment,
        )

    return ResolveConflictResponse(
        success=True,
        conflict_id=conflict_id,
        skill_extracted=skill,
        message=f"Конфликт {conflict_id} решён."
                + (" Навык добавлен в skills.md." if skill else ""),
    )


@router.post("/{conflict_id}/comment")
async def add_comment(
    conflict_id: str,
    body: AddCommentRequest,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    """
    Add a comment to a conflict without resolving it.
    Comment is appended to the conflict block in conflicts.md.
    Used for 'instructions' and notes while conflict stays open.
    """
    raw = fs.read_conflicts_raw()
    if conflict_id not in raw:
        raise HTTPException(404, f"Конфликт {conflict_id} не найден")

    from datetime import datetime
    ts = datetime.now().isoformat(timespec="seconds")
    comment_line = f"- **Comment [{ts}]:** {body.comment}\n"

    # Inject comment after User comment field
    updated = raw.replace(
        "- **User comment:** _none_",
        f"- **User comment:** _none_\n{comment_line}",
        1,
    )
    if updated == raw:
        # Comment field already has content — append after last comment
        updated = re.sub(
            rf"(## \[(?:OPEN|RESOLVED)\] {re.escape(conflict_id)}[^\n]*\n)",
            rf"\1{comment_line}",
            raw,
        )

    (fs.root / "conflicts.md").write_text(updated, encoding="utf-8")
    return {"success": True, "conflict_id": conflict_id, "comment": body.comment}


@router.post("/{conflict_id}/prepare-update")
async def prepare_conflict_update(
    conflict_id: str,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    """
    Prepare a draft update for the wiki page affected by a resolved conflict.
    Returns draft metadata with affected slug and resolution details.
    """
    raw = fs.read_conflicts_raw()
    # Find conflict (open or resolved)
    if conflict_id not in raw:
        raise HTTPException(404, f"Конфликт {conflict_id} не найден")

    # Extract resolution info
    pattern = rf"## \[RESOLVED\] {re.escape(conflict_id)}.*?-\s*\*\*Resolution:\*\*\s*(.+?)(?=\n-|\Z)"
    res_match = re.search(pattern, raw, re.DOTALL)
    resolution = res_match.group(1).strip() if res_match else ""

    pattern2 = rf"## \[RESOLVED\] {re.escape(conflict_id)}.*?-\s*\*\*User comment:\*\*\s*(.+?)(?=\n-|\Z)"
    comment_match = re.search(pattern2, raw, re.DOTALL)
    user_comment = comment_match.group(1).strip() if comment_match else ""

    draft = fs.prepare_conflict_resolution_draft(
        conflict_id=conflict_id,
        resolution=resolution,
        user_comment=user_comment if user_comment != "_none_" else "",
    )
    if draft is None:
        raise HTTPException(400, f"Не удалось подготовить обновление для {conflict_id}")

    return {
        "draft_id": draft["draft_id"],
        "affected_slug": draft["affected_slug"],
        "resolution": draft["resolution"],
        "source_context": draft["source_context"],
    }


@router.post("/{conflict_id}/apply-update")
async def apply_conflict_update(
    conflict_id: str,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
    agent: Annotated[IngestAgent, Depends(get_ingest_agent)],
):
    """
    Apply a prepared conflict resolution draft to the wiki page.
    Uses LLM to generate updated content based on resolution, then applies it.
    """
    draft_id = f"conflict-{conflict_id}"
    draft_dir = fs.drafts_dir / draft_id
    if not draft_dir.exists():
        raise HTTPException(404, f"Draft для {conflict_id} не найден. Сначала вызовите prepare-update.")

    meta_path = draft_dir / "meta.json"
    if not meta_path.exists():
        raise HTTPException(404, f"meta.json не найден в draft {draft_id}")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    affected_slug = meta["affected_slug"]
    resolution = meta["resolution"]
    source_context = meta["source_context"]
    existing_content = meta["existing_content"]

    # Use LLM to generate updated page
    from app.agents.ingest_prompts import STEP2_SYSTEM
    from app.agents.ingest_helpers import build_system_prompt

    skills = fs.read_skills()
    agents_md_text = ""
    agents_md_path = fs.root / "AGENTS.md"
    if agents_md_path.exists():
        agents_md_text = agents_md_path.read_text(encoding="utf-8")[:2000]

    system = build_system_prompt(STEP2_SYSTEM, agents_md_text, skills)
    prompt = (
        f"Update the following wiki page based on the resolved conflict resolution.\n\n"
        f"Resolution: {resolution}\n"
        f"Source context: {source_context}\n\n"
        f"Existing page:\n{existing_content}\n\n"
        f"Rules:\n"
        f"- Keep the page structure and frontmatter intact.\n"
        f"- Update only the content that contradicts the resolution.\n"
        f"- Write all content in Russian.\n"
        f"- Keep technical terms in English.\n"
        f"- Return ONLY the updated markdown body (no frontmatter block).\n"
    )

    import asyncio
    new_content = await asyncio.to_thread(
        agent.llm.call,
        system=system,
        prompt=prompt,
        temperature=0.1,
    )

    # Read existing page and update content only
    existing_page = fs.read_page(affected_slug)
    if existing_page is None:
        raise HTTPException(404, f"Страница {affected_slug} не найдена")

    # Write updated page
    import frontmatter
    updated_post = frontmatter.Post(new_content, **existing_page.meta)
    updated_path = fs.wiki_dir / f"{affected_slug}.md"
    updated_path.parent.mkdir(parents=True, exist_ok=True)
    updated_path.write_text(
        frontmatter.dumps(updated_post),
        encoding="utf-8",
    )

    # Remove draft after applying
    import shutil
    shutil.rmtree(draft_dir)

    fs.rebuild_index()

    return {
        "success": True,
        "affected_slug": affected_slug,
        "message": f"Страница {affected_slug} обновлена согласно разрешению конфликта.",
    }


# ── Parser ───────────────────────────────────────────────────────

def _parse_conflicts_md(raw: str) -> tuple[list[ConflictOut], list[ConflictOut]]:
    """
    Parse conflicts.md into structured ConflictOut objects.
    Returns (open_list, resolved_list).
    """
    open_list: list[ConflictOut] = []
    resolved_list: list[ConflictOut] = []

    # Split into blocks
    blocks = re.split(r"\n---\n", raw)

    for block in blocks:
        status_match = re.search(r"## \[(OPEN|RESOLVED)\] (CONFLICT-\d+)", block)
        if not status_match:
            continue

        status = status_match.group(1)
        cid = status_match.group(2)

        def extract(field: str, b: str, default: str = "") -> str:
            m = re.search(rf"\*\*{re.escape(field)}:\*\*\s*(.+)", b)
            return m.group(1).strip() if m else default

        def extract_list(field: str, b: str) -> list[str]:
            section = re.search(
                rf"\*\*{re.escape(field)}:\*\*\n((?:\s+\d+\..+\n?)+)", b
            )
            if not section:
                return []
            return re.findall(r"\d+\.\s+(.+)", section.group(1))

        entry = ConflictOut(
            id=cid,
            status=status,
            date=extract("Date", block),
            project=extract("Project", block),
            source_file=extract("Source file", block),
            conflict_type=extract("Conflict type", block),
            page_a_slug=_extract_slug(extract("Page A (wiki)", block) or extract("Page A", block)),
            page_b_ref=extract("Page B (source)", block) or extract("Page B", block),
            description=extract("Description", block, ""),
            context_a=extract("Context A (wiki excerpt)", block) or extract("Context A", block),
            context_b=extract("Context B (source excerpt)", block) or extract("Context B", block),
            suggested_options=extract_list("Suggested options", block),
            user_comment=extract("User comment", block, "_none_"),
            resolution=extract("Resolution", block, "pending"),
            skill_extracted=extract("Skill extracted", block, ""),
            resolved_at=extract("Resolved at", block, ""),
        )

        if status == "OPEN":
            open_list.append(entry)
        else:
            resolved_list.append(entry)

    return open_list, resolved_list


def _extract_slug(text: str) -> str:
    """Extract slug from '[[slug]] — description' format."""
    m = re.search(r"\[\[([^\]]+)\]\]", text)
    return m.group(1) if m else text

