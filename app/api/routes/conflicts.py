"""
conflicts.py — Conflict review and resolution routes.

GET  /api/conflicts              — list all conflicts (open + resolved)
GET  /api/conflicts/{id}         — get single conflict
POST /api/conflicts/{id}/resolve — resolve with choice + comment
POST /api/conflicts/{id}/comment — add comment without resolving
"""

from __future__ import annotations

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
        raise HTTPException(404, f"Conflict {conflict_id} not found")
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
            f"Conflict {conflict_id} not found or already resolved",
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
        message=f"Conflict {conflict_id} resolved."
                + (" Skill added to skills.md." if skill else ""),
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
        raise HTTPException(404, f"Conflict {conflict_id} not found")

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
            page_a_slug=_extract_slug(extract("Page A", block)),
            page_b_ref=extract("Page B", block),
            context_a=extract("Context A", block),
            context_b=extract("Context B", block),
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

