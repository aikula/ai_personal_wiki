"""
settings.py — Runtime settings management.

GET  /api/admin/settings        — get current settings (api_key masked)
POST /api/admin/settings        — update LLM connection settings
GET  /api/admin/settings/test   — test LLM connectivity
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import Settings, build_base_llm_client, get_settings
from app.api.models import SettingsResponse, UpdateSettingsRequest
from app.api.routes.auth import get_current_admin

router = APIRouter(
    prefix="/api/admin/settings",
    tags=["admin"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("/language")
async def get_language(
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Return only the UI language code — lightweight call for frontend init."""
    return {"language": settings.language}


@router.get("", response_model=SettingsResponse)
async def get_current_settings(
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Get current settings. API key is masked."""
    return SettingsResponse(
        language=settings.language,
        llm_base_url=settings.llm.base_url,
        llm_model=settings.llm.model,
        wiki_data_path=settings.wiki_data_path,
        limits=settings.limits.__dict__,
        ingest=settings.ingest.__dict__,
        query=settings.query.__dict__,
        audit=settings.audit.__dict__,
    )


@router.post("")
async def update_settings(
    body: UpdateSettingsRequest,
    settings: Annotated[Settings, Depends(get_settings)],
):
    """
    Update LLM connection settings at runtime.
    Changes are applied in-memory immediately.
    Persist by updating config/settings.yaml manually or via volume.
    """
    if body.llm_base_url is not None:
        settings.llm.base_url = body.llm_base_url
    if body.llm_api_key is not None:
        settings.llm.api_key = body.llm_api_key
    if body.llm_model is not None:
        settings.llm.model = body.llm_model
    if body.temperature is not None:
        settings.llm.temperature = body.temperature
    if body.language is not None:
        settings.language = body.language

    return {"success": True, "message": "Настройки обновлены в памяти"}


@router.get("/test")
async def test_llm_connection(
    settings: Annotated[Settings, Depends(get_settings)],
):
    """
    Send a minimal test call to LLM to verify connectivity.
    Returns latency and model confirmation.
    """
    import time
    llm = build_base_llm_client(settings)
    try:
        start = time.monotonic()
        response = await asyncio.to_thread(
            llm.call,
            system="You are a test assistant.",
            prompt='Reply with exactly: {"status": "ok"}',
            temperature=0.0,
        )
        latency_ms = round((time.monotonic() - start) * 1000)
        return {
            "connected": True,
            "model": llm.model,
            "latency_ms": latency_ms,
            "response_preview": response[:100],
        }
    except Exception as exc:
        return {
            "connected": False,
            "error": str(exc),
        }
