"""
onboarding.py — Demo data seeding and welcome status.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends

from app.agents.ingest_agent import IngestAgent
from app.api.dependencies import get_ingest_agent, get_wiki_fs
from app.core.raw_sources import save_raw_file_bytes
from app.core.wiki_fs import WikiFS

logger = logging.getLogger("wiki.api")
router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

SEED_DIR = Path(__file__).parent.parent.parent / "seed_data"

SEED_FILES = [
    "smartlight_intro.md",
    "smartlight_hub.md",
    "smartlight_api.md",
]

@router.get("/status")
async def onboarding_status(
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    pages = fs.list_pages()
    raw_files = list(fs.raw_dir.rglob("*")) if fs.raw_dir.exists() else []
    return {
        "wiki_empty": len(pages) == 0,
        "total_pages": len(pages),
        "raw_files": len([f for f in raw_files if f.is_file()]),
        "needs_onboarding": len(pages) == 0,
    }

@router.post("/seed-demo")
async def seed_demo(
    agent: Annotated[IngestAgent, Depends(get_ingest_agent)],
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    if not SEED_DIR.exists():
        return {"success": False, "error": "Seed data directory not found"}

    results = []
    for filename in SEED_FILES:
        filepath = SEED_DIR / filename
        if not filepath.exists():
            results.append({"file": filename, "success": False, "error": "file not found"})
            continue

        project = "_general"
        content = filepath.read_bytes()

        save_raw_file_bytes(fs.raw_dir, fs.state_dir, project, filename, content)

        raw_path = f"{project}/{filename}"
        try:
            result = await asyncio.to_thread(agent.run, raw_path)
            pages_created = result.pages_created if hasattr(result, "pages_created") else []
            pages_updated = result.pages_updated if hasattr(result, "pages_updated") else []
            results.append({
                "file": filename,
                "success": True,
                "pages_created": pages_created,
                "pages_updated": pages_updated,
            })
        except Exception as exc:
            logger.exception("Seed ingest failed for %s", filename)
            results.append({"file": filename, "success": False, "error": str(exc)})

    total_created = sum(len(r.get("pages_created", [])) for r in results if r.get("success"))
    total_updated = sum(len(r.get("pages_updated", [])) for r in results if r.get("success"))

    return {
        "success": True,
        "files_processed": len(results),
        "files_ok": sum(1 for r in results if r.get("success")),
        "pages_created": total_created,
        "pages_updated": total_updated,
        "details": results,
    }
