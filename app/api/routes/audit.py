"""
audit.py — Duplicate/collapse audit and synthesis queue routes.

GET  /api/audit/duplicates          — run duplicate detection
GET  /api/audit/synthesis            — list synthesis queue items
POST /api/audit/synthesis            — create a synthesis candidate
POST /api/audit/synthesis/{id}/resolve — resolve a queue item
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import AuditAgent, get_audit_agent

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/duplicates")
async def get_duplicates(
    project: str | None = None,
    agent: Annotated[AuditAgent, Depends(get_audit_agent)] = None,
):
    """Run deterministic duplicate/collapse detection."""
    candidates = agent.audit_duplicates(project=project)
    return {"candidates": candidates, "total": len(candidates)}


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
