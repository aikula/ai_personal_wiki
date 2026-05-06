"""
ingest.py — File upload, batch ingest, raw file listing, rebuild.

POST /api/ingest              — upload single .md file
POST /api/ingest/batch        — upload multiple .md files
GET  /api/ingest/raw          — list raw files
POST /api/ingest/rebuild      — rebuild wiki from raw (SSE stream)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.api.dependencies import (
    IngestAgent,
    WikiFS,
    get_ingest_agent,
    get_wiki_fs,
)
from app.api.models import (
    IngestFileResponse,
    RebuildRequest,
)

logger = logging.getLogger("wiki.api.ingest")

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("", response_model=IngestFileResponse)
async def ingest_file(
    project: str = Form(default="_general"),
    file: UploadFile = File(...),
    agent: Annotated[IngestAgent, Depends(get_ingest_agent)] = None,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)] = None,
):
    """
    Upload a single markdown file and ingest it into wiki.
    File is saved to raw/<project>/<filename>, then processed.
    """
    if not file.filename or not file.filename.endswith(".md"):
        raise HTTPException(400, "Only .md files are accepted")

    content = await file.read()
    content_str = content.decode("utf-8")

    fs.save_raw_file(project, file.filename, content_str)

    raw_path = f"{project}/{file.filename}"
    logger.info("Ingest file: %s project=%s", raw_path, project)
    result = agent.run(raw_path)
    logger.info("Ingest result: success=%s created=%d updated=%d",
                result.success, len(result.pages_created), len(result.pages_updated))

    return IngestFileResponse(
        success=result.success,
        source_file=result.source_file,
        project=result.project,
        pages_created=result.pages_created,
        pages_updated=result.pages_updated,
        pages_superseded=result.pages_superseded,
        conflict_ids=result.conflict_ids,
        skills_triggered=result.skills_triggered,
        lint_errors=len(result.lint_report.errors) if result.lint_report else 0,
        lint_warnings=len(result.lint_report.warnings) if result.lint_report else 0,
        analysis_notes=result.analysis_notes,
        error=result.error,
    )


@router.post("/batch")
async def ingest_batch(
    project: str = Form(default="_general"),
    files: list[UploadFile] = File(...),
    agent: Annotated[IngestAgent, Depends(get_ingest_agent)] = None,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)] = None,
):
    """
    Upload and ingest multiple markdown files sequentially.
    Returns summary of all ingests.
    """
    results = []
    for file in files:
        if not file.filename or not file.filename.endswith(".md"):
            continue
        content = await file.read()
        content_str = content.decode("utf-8")
        fs.save_raw_file(project, file.filename, content_str)
        raw_path = f"{project}/{file.filename}"
        result = agent.run(raw_path)
        results.append({
            "file": file.filename,
            "success": result.success,
            "pages_created": result.pages_created,
            "error": result.error,
        })

    return {
        "total": len(results),
        "successes": sum(1 for r in results if r["success"]),
        "failures": sum(1 for r in results if not r["success"]),
        "details": results,
    }


@router.get("/raw")
async def list_raw_files(
    project: str | None = None,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)] = None,
):
    """List raw markdown files, optionally filtered by project."""
    files = fs.list_raw_files(project=project)
    return {
        "files": [
            {
                "path": str(f.relative_to(fs.raw_dir)),
                "project": fs.get_raw_project(f),
                "size": f.stat().st_size,
            }
            for f in files
        ],
        "total": len(files),
    }


@router.post("/rebuild")
async def rebuild_wiki(
    body: RebuildRequest,
    agent: Annotated[IngestAgent, Depends(get_ingest_agent)] = None,
):
    """
    Rebuild entire wiki from raw files.
    Requires confirm=true to prevent accidental deletion.
    Returns SSE stream with progress updates.
    """
    if not body.confirm:
        raise HTTPException(400, "Must set confirm=true to rebuild")

    async def event_stream() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()

        def progress_callback(current: int, total: int, filename: str):
            queue.put_nowait({
                "current": current,
                "total": total,
                "filename": filename,
                "status": "processing",
            })

        def run_rebuild():
            try:
                logger.info("Rebuild requested")
                result = agent.rebuild_from_scratch(progress_callback=progress_callback)
                queue.put_nowait({"result": result, "status": "complete"})
                logger.info("Rebuild completed: success=%d failed=%d",
                            result["success"], result["failed"])
            except Exception as exc:
                logger.exception("Rebuild failed")
                queue.put_nowait({"status": "error", "message": str(exc)})
            finally:
                queue.put_nowait(None)

        loop = asyncio.get_event_loop()
        thread = loop.run_in_executor(None, run_rebuild)

        while True:
            event = await asyncio.wait_for(queue.get(), timeout=120)
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        await thread

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
