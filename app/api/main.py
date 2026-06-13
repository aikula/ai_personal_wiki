"""
main.py — FastAPI application entrypoint.

Mounts:
  /api/ingest    — file upload, rebuild
  /api/chat      — streaming chat, sessions
  /api/wiki      — page tree, rendering, search
  /api/conflicts — conflict queue management
  /api/admin/settings — server-level LLM connection config
  /              — serves ui/index.html (SPA)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.dependencies import build_base_llm_client, build_control_store, get_settings
from app.api.routes import audit as audit_route
from app.api.routes import auth, chat, conflicts, ingest, onboarding, usage, wiki
from app.api.routes import settings as settings_route
from app.config import Settings, setup_logging
from app.core.metered_llm_client import QuotaExceededError, UsageRecordingError

setup_logging()
logger = logging.getLogger("wiki.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _check_auth_config(settings)
    if settings.app_mode == "multi_user":
        store = build_control_store(settings)
        store.reset_all_due_daily_buckets()
    app.state.llm_status = await _check_llm_connection()
    yield


app = FastAPI(
    title="Wiki Engine",
    description="LLM-powered personal wiki from markdown documents",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS (for local dev with separate frontend port) ─────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restrict in production
    allow_methods=["*"],
    allow_headers=["*"],
)


def _check_auth_config(settings: Settings) -> None:
    if settings.auth.enabled and (
        not settings.auth.username or not settings.auth.password
    ):
        raise RuntimeError(
            "WIKI_AUTH_ENABLED=true requires WIKI_AUTH_USERNAME and WIKI_AUTH_PASSWORD"
        )


def _unauthorized_response() -> Response:
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Wiki Engine"'},
    )


def _basic_auth_valid(header: str, settings: Settings) -> bool:
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except Exception:
        return False
    username, sep, password = decoded.partition(":")
    if not sep:
        return False
    return (
        secrets.compare_digest(username, settings.auth.username)
        and secrets.compare_digest(password, settings.auth.password)
    )


@app.middleware("http")
async def basic_auth_middleware(request: Request, call_next):
    settings = get_settings()
    if not settings.auth.enabled:
        return await call_next(request)
    if _basic_auth_valid(request.headers.get("authorization", ""), settings):
        return await call_next(request)
    return _unauthorized_response()

# ── API routes ───────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(ingest.router)
app.include_router(chat.router)
app.include_router(wiki.router)
app.include_router(conflicts.router)
app.include_router(audit_route.router)
app.include_router(onboarding.router)
app.include_router(settings_route.router)
app.include_router(usage.router)

# ── Startup checks ───────────────────────────────────────────────
async def _check_llm_connection() -> dict:
    settings = get_settings()
    if not settings.llm.api_key:
        message = (
            "LLM API key is not configured. Running in degraded mode; "
            "LLM-backed features will remain unavailable until configured."
        )
        logger.warning(message)
        return {"connected": False, "warning": message}

    llm = build_base_llm_client(settings)
    try:
        response = await asyncio.to_thread(
            llm.call,
            system="You are a test assistant.",
            prompt='Reply with exactly: {"status": "ok"}',
            temperature=0.0,
        )
        return {
            "connected": True,
            "model": llm.model,
            "response_preview": response[:100],
        }
    except Exception as exc:
        message = (
            f"LLM connectivity check failed. Running in degraded mode: {exc}"
        )
        logger.warning(message)
        return {"connected": False, "warning": message}


@app.exception_handler(QuotaExceededError)
async def quota_exceeded_handler(_: Request, exc: QuotaExceededError) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "detail": str(exc),
            "code": "quota_exceeded",
            "required": exc.required,
            "available": exc.available,
        },
    )


@app.exception_handler(UsageRecordingError)
async def usage_recording_handler(_: Request, exc: UsageRecordingError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(exc),
            "code": "usage_recording_failed",
        },
    )

# ── Health check ─────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    settings = get_settings()
    payload = {"status": "ok", "service": "wiki-engine"}
    payload["mode"] = settings.app_mode
    payload["llm"] = getattr(app.state, "llm_status", {"connected": None})
    return payload

# ── Serve UI ─────────────────────────────────────────────────────
_UI_DIR = Path(__file__).parent.parent / "ui"

if _UI_DIR.exists():
    # Static assets (CSS, JS if any)
    assets = _UI_DIR / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/", include_in_schema=False)
    async def serve_ui():
        return FileResponse(str(_UI_DIR / "index.html"))

    # SPA catch-all: any non-API route returns index.html
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            from fastapi import HTTPException
            raise HTTPException(404)
        return FileResponse(str(_UI_DIR / "index.html"))
