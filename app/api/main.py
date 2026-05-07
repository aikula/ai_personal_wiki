"""
main.py — FastAPI application entrypoint.

Mounts:
  /api/ingest    — file upload, rebuild
  /api/chat      — streaming chat, sessions
  /api/wiki      — page tree, rendering, search
  /api/conflicts — conflict queue management
  /api/settings  — LLM connection config
  /              — serves ui/index.html (SPA)
  /wiki-data     — static mount for raw file access (read-only)
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import chat, conflicts, ingest, wiki
from app.api.routes import audit as audit_route
from app.api.routes import settings as settings_route
from app.config import setup_logging

setup_logging()

app = FastAPI(
    title="Wiki Engine",
    description="LLM-powered personal wiki from markdown documents",
    version="1.0.0",
)

# ── CORS (for local dev with separate frontend port) ─────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restrict in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ───────────────────────────────────────────────────
app.include_router(ingest.router)
app.include_router(chat.router)
app.include_router(wiki.router)
app.include_router(conflicts.router)
app.include_router(audit_route.router)
app.include_router(settings_route.router)

# ── Health check ─────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "wiki-engine"}

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