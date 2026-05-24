"""
audit.py — Duplicate/collapse audit, synthesis queue, and structural lint routes.

GET  /api/audit/duplicates          — run duplicate detection
GET  /api/audit/lint                — run structural linter (all checks)
GET  /api/audit/synthesis            — list synthesis queue items
POST /api/audit/synthesis            — create a synthesis candidate
POST /api/audit/synthesis/{id}/resolve — resolve a queue item
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import AuditAgent, WikiFS, get_audit_agent, get_settings, get_wiki_fs
from app.config import Settings
from app.core.linter import WikiLinter

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/duplicates")
async def get_duplicates(
    project: str | None = None,
    agent: Annotated[AuditAgent, Depends(get_audit_agent)] = None,
):
    """Run deterministic duplicate/collapse detection."""
    return agent.audit_duplicates(project=project)


@router.get("/lint")
async def get_lint(
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Run full structural linter (17 checks)."""
    linter = WikiLinter(fs, settings)
    report = linter.lint()
    return {
        "ran_at": report.ran_at,
        "total_pages": report.total_pages,
        "total": len(report.issues),
        "errors": len(report.errors),
        "warnings": len(report.warnings),
        "is_clean": report.is_clean,
        "by_kind": report.by_kind,
        "issues": [str(i) for i in report.issues],
    }


@router.get("/synthesis")
async def list_synthesis(
    agent: Annotated[AuditAgent, Depends(get_audit_agent)] = None,
):
    """List all pending synthesis/collapse queue items."""
    return {"items": agent.list_synthesis_candidates()}


@router.post("/synthesis")
async def create_synthesis(
    body: dict,
    agent: Annotated[AuditAgent, Depends(get_audit_agent)] = None,
):
    """Create a new synthesis/collapse candidate."""
    cid = agent.create_synthesis_candidate(body)
    return {"id": cid}


@router.post("/synthesis/{cid}/resolve")
async def resolve_synthesis(
    cid: str,
    action: str = "resolved",
    agent: Annotated[AuditAgent, Depends(get_audit_agent)] = None,
):
    """Resolve (archive) a synthesis queue item."""
    if agent.resolve_synthesis_candidate(cid, action):
        return {"status": "resolved"}
    raise HTTPException(404, f"Synthesis candidate not found: {cid}")
