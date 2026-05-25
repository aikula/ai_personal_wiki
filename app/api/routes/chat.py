"""
chat.py — Chat and session management routes.

POST /api/chat                        — send message, get SSE stream
GET  /api/chat/sessions               — list all sessions
GET  /api/chat/sessions/{id}          — get session history
DELETE /api/chat/sessions/{id}        — delete session
POST /api/chat/sessions/{id}/crystallize — save session as wiki page
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.dependencies import (
    QueryAgent,
    get_or_create_session,
    get_query_agent,
    get_session_store,
)
from app.api.models import (
    ChatHistoryResponse,
    ChatMessageOut,
    ChatRequest,
    CrystallizeResponse,
    SessionListResponse,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ── Send message (SSE streaming) ─────────────────────────────────

@router.post("")
async def chat(
    body: ChatRequest,
    agent: Annotated[QueryAgent, Depends(get_query_agent)],
    store: Annotated[dict, Depends(get_session_store)],
):
    """
    Send a question and receive streaming response via SSE.

    SSE event types:
      data: {"type": "chunk", "content": "text fragment"}
      data: {"type": "replace", "content": "cleaned final answer"}
      data: {"type": "cited", "slug": "myapp/storage/redis"}
      data: {"type": "meta", "question_type": "factual", "pages_read": [...]}
      data: {"type": "done"}
      data: {"type": "error", "message": "..."}

    UI should:
      - Append "chunk" content to current message bubble
      - On "cited": add [[slug]] link to right panel highlight queue
      - On "done": finalize message, enable input
    """
    session = get_or_create_session(
        session_id=body.session_id,
        project_filter=body.project_filter,
        store=store,
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            # Run streaming in thread (LLM calls are sync)
            queue: asyncio.Queue = asyncio.Queue()

            def run_stream():
                try:
                    for chunk in agent.stream(
                        question=body.question,
                        session=session,
                    ):
                        if chunk.startswith("[CITED:") and chunk.endswith("]"):
                            slug = chunk[7:-1]
                            queue.put_nowait({"type": "cited", "slug": slug})
                        elif chunk.startswith("[REPLACE:") and chunk.endswith("]"):
                            content = json.loads(chunk[9:-1])
                            queue.put_nowait({"type": "replace", "content": content})
                        elif chunk == "[DONE]":
                            queue.put_nowait({"type": "done"})
                        else:
                            queue.put_nowait({"type": "chunk", "content": chunk})
                except Exception as exc:
                    queue.put_nowait({"type": "error", "message": str(exc)})
                finally:
                    queue.put_nowait(None)  # sentinel

            thread = asyncio.get_event_loop().run_in_executor(None, run_stream)

            while True:
                event = await asyncio.wait_for(queue.get(), timeout=120)
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") in ("done", "error"):
                    break

            await thread

        except TimeoutError:
            yield 'data: {"type": "error", "message": "LLM timeout"}\n\n'
        except Exception as exc:
            yield f'data: {{"type": "error", "message": {json.dumps(str(exc))}}}\n\n'

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Session management ───────────────────────────────────────────

@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    store: Annotated[dict, Depends(get_session_store)],
):
    """List all chat sessions with metadata."""
    sessions = []
    for sid, session in store.items():
        user_msgs = [m for m in session.messages if m.role == "user"]
        sessions.append({
            "session_id": sid,
            "created_at": session.created_at,
            "message_count": len(session.messages),
            "last_question": user_msgs[-1].content[:80] if user_msgs else "",
            "project_filter": session.project_filter,
        })
    # Sort newest first
    sessions.sort(key=lambda x: x["created_at"], reverse=True)
    return SessionListResponse(sessions=sessions)


@router.get("/sessions/{session_id}", response_model=ChatHistoryResponse)
async def get_session(
    session_id: str,
    store: Annotated[dict, Depends(get_session_store)],
):
    """Get full message history for a session."""
    if session_id not in store:
        raise HTTPException(404, f"Сессия {session_id!r} не найдена")
    session = store[session_id]
    return ChatHistoryResponse(
        session_id=session_id,
        messages=[
            ChatMessageOut(
                role=m.role,
                content=m.content,
                timestamp=m.timestamp,
                cited_slugs=m.cited_slugs,
            )
            for m in session.messages
        ],
    )


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    store: Annotated[dict, Depends(get_session_store)],
):
    """Delete a chat session from memory."""
    if session_id not in store:
        raise HTTPException(404, "Сессия не найдена")
    del store[session_id]
    return {"deleted": session_id}


@router.post("/sessions/{session_id}/crystallize",
             response_model=CrystallizeResponse)
async def crystallize_session(
    session_id: str,
    agent: Annotated[QueryAgent, Depends(get_query_agent)],
    store: Annotated[dict, Depends(get_session_store)],
):
    """
    Distill session Q&A into a wiki page.
    Requires >= 3 user messages in session.
    Returns slug of created page.
    """
    if session_id not in store:
        raise HTTPException(404, "Сессия не найдена")
    session = store[session_id]

    slug = await asyncio.to_thread(agent.crystallize_session, session)
    if slug:
        return CrystallizeResponse(
            slug=slug,
            message=f"Сессия кристаллизована в [[{slug}]]",
        )
    return CrystallizeResponse(
        slug=None,
        message="Сессия слишком короткая для кристаллизации (нужно >= 3 обмена)",
    )
