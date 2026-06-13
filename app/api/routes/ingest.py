"""File upload, batch ingest, raw listing, and rebuild routes."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import IngestAgent, WikiFS, get_ingest_agent, get_wiki_fs
from app.api.models import IngestFileResponse, RebuildRequest
from app.core.raw_sources import (
    RAW_ALLOWED_EXTENSIONS,
    check_source_state_bytes,
    list_raw_source_files,
    save_raw_file_bytes,
)
from app.core.utils import validate_project_name, validate_raw_filename

logger = logging.getLogger("wiki.api.ingest")
router = APIRouter(prefix="/api/ingest", tags=["ingest"])

# ── Ingest job tracking ──────────────────────────────────────────────
_active_jobs: dict[str, threading.Event] = {}


def _allowed_ext_text() -> str:
    return ", ".join(sorted(RAW_ALLOWED_EXTENSIONS))


def _to_response(result) -> IngestFileResponse:
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


async def _read_form_file(request: Request):
    form = await request.form()
    project = str(form.get("project") or "_general")
    source_file = form.get("file")
    if source_file is None or not getattr(source_file, "filename", None):
        logger.warning("Ingest rejected: file is missing project=%s", project)
        raise HTTPException(400, "Файл не передан")
    return project, source_file


async def _save_and_ingest(project: str, source_file, agent: IngestAgent, fs: WikiFS):
    filename = source_file.filename
    try:
        validate_project_name(project)
        validate_raw_filename(filename)
    except ValueError as exc:
        logger.warning(
            "Ingest validation rejected project=%s filename=%s reason=%s",
            project,
            filename,
            exc,
        )
        raise HTTPException(400, str(exc)) from exc

    if not any(filename.lower().endswith(ext) for ext in RAW_ALLOWED_EXTENSIONS):
        logger.warning(
            "Ingest rejected unsupported extension project=%s filename=%s",
            project,
            filename,
        )
        raise HTTPException(
            400,
            "Неподдерживаемый тип файла. Допустимые расширения: " + _allowed_ext_text(),
        )

    content = await source_file.read()
    raw_path = f"{project}/{filename}"
    state = check_source_state_bytes(fs.state_dir, raw_path, content)

    if state["status"] == "unchanged":
        return IngestFileResponse(
            success=True,
            source_file=raw_path,
            project=project,
            pages_created=[],
            pages_updated=[],
            pages_superseded=[],
            conflict_ids=[],
            skills_triggered=[],
            lint_errors=0,
            lint_warnings=0,
            analysis_notes="Source unchanged, skipped",
            error=None,
        )

    save_raw_file_bytes(fs.raw_dir, fs.state_dir, project, filename, content)

    job_id = str(uuid.uuid4())[:8]
    cancel_event = threading.Event()
    _active_jobs[job_id] = cancel_event
    try:
        result = await asyncio.to_thread(agent.run, raw_path, cancel_event=cancel_event)
        return _to_response(result)
    finally:
        _active_jobs.pop(job_id, None)


@router.post("", response_model=IngestFileResponse)
async def ingest_file(
    request: Request,
    agent: Annotated[IngestAgent, Depends(get_ingest_agent)] = None,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)] = None,
):
    project, source_file = await _read_form_file(request)
    return await _save_and_ingest(project, source_file, agent, fs)


@router.post("/batch")
async def ingest_batch(
    request: Request,
    agent: Annotated[IngestAgent, Depends(get_ingest_agent)] = None,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)] = None,
):
    form = await request.form()
    project = str(form.get("project") or "_general")
    try:
        validate_project_name(project)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    accepted_keys = {"files", "file"}
    items = [
        (key, value) for key, value in form.multi_items()
        if key in accepted_keys and getattr(value, "filename", None)
    ]
    if not items:
        raise HTTPException(400, "Пустой батч: нет файлов с ключом 'files' или 'file'")

    results = []
    skipped_details = []
    for _key, source_file in items:
        try:
            response = await _save_and_ingest(project, source_file, agent, fs)
            results.append(response.model_dump())
        except HTTPException as exc:
            skipped_details.append({
                "file": source_file.filename,
                "reason": exc.detail,
            })

    return {
        "total": len(items),
        "processed": len(results),
        "skipped": len(skipped_details),
        "successes": sum(1 for r in results if r["success"]),
        "failures": sum(1 for r in results if not r["success"]),
        "details": results,
        "skipped_details": skipped_details,
    }


@router.get("/raw")
async def list_raw_files(
    project: str | None = None,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)] = None,
):
    if project is not None:
        try:
            validate_project_name(project)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    files = list_raw_source_files(fs.raw_dir, project=project)
    return {
        "files": [
            {
                "path": str(f.relative_to(fs.raw_dir)),
                "project": fs.get_raw_project(f),
                "size": f.stat().st_size,
                "extension": f.suffix.lower(),
            }
            for f in files
        ],
        "total": len(files),
    }


@router.post("/cancel")
async def cancel_ingest():
    active = list(_active_jobs.keys())
    for job_id, event in _active_jobs.items():
        event.set()
        logger.info("Ingest cancel requested: job_id=%s", job_id)
    return {"cancelled": len(active), "job_ids": active}


@router.post("/rebuild")
async def rebuild_wiki(
    body: RebuildRequest,
    agent: Annotated[IngestAgent, Depends(get_ingest_agent)] = None,
):
    if not body.confirm:
        raise HTTPException(400, "Необходимо установить confirm=true для перестройки")

    async def event_stream() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        done = False

        def push_event(event: dict | None) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def progress_callback(current: int, total: int, filename: str):
            push_event({"current": current, "total": total, "filename": filename, "status": "processing"})

        def run_rebuild():
            try:
                result = agent.rebuild_from_scratch(progress_callback=progress_callback)
                push_event({"result": result, "status": "complete"})
            except Exception as exc:
                logger.exception("Rebuild failed")
                push_event({"status": "error", "message": str(exc)})
            finally:
                push_event(None)

        async def heartbeat():
            while not done:
                await asyncio.sleep(30)
                if not done:
                    push_event({"status": "alive"})

        hb = asyncio.create_task(heartbeat())
        thread = loop.run_in_executor(None, run_rebuild)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            done = True
            hb.cancel()
        await thread

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/drafts")
async def list_drafts(fs: Annotated[WikiFS, Depends(get_wiki_fs)]):
    return {"drafts": fs.list_drafts()}


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: str, fs: Annotated[WikiFS, Depends(get_wiki_fs)]):
    draft = fs.read_draft(draft_id)
    if draft is None:
        raise HTTPException(404, f"Черновик не найден: {draft_id}")
    return draft


@router.post("/drafts/{draft_id}/apply")
async def apply_draft(draft_id: str, fs: Annotated[WikiFS, Depends(get_wiki_fs)]):
    try:
        applied = fs.apply_draft(draft_id)
        return {"status": "applied", "pages": applied}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/drafts/{draft_id}/reject")
async def reject_draft(draft_id: str, fs: Annotated[WikiFS, Depends(get_wiki_fs)]):
    if fs.reject_draft(draft_id):
        return {"status": "rejected"}
    raise HTTPException(404, f"Черновик не найден: {draft_id}")


@router.post("/clear")
async def clear_wiki(
    body: RebuildRequest,
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
):
    """
    Clear all wiki data and reset to initial state.
    Requires confirm=true to prevent accidental deletion.
    """
    if not body.confirm:
        raise HTTPException(400, "Необходимо установить confirm=true для очистки вики")
    
    # Reset wiki to initial state
    fs.full_reset_wiki()
    
    # Clear all drafts
    fs.clear_all_drafts()
    
    # Clear conflicts and skills (reset to initial state)
    (fs.root / "conflicts.md").write_text("# Conflicts\n\n", encoding="utf-8")
    (fs.root / "skills.md").write_text("# Skills\n\n", encoding="utf-8")
    
    logger.info("Wiki cleared to initial state")
    
    return {"status": "cleared", "message": "Вики очищена и возвращена в исходное состояние"}
