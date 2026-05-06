"""
models.py — Pydantic request/response models for all API routes.
These are the contracts between frontend and backend.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ─────────────────────────────────────────────
# Ingest
# ─────────────────────────────────────────────

class IngestFileRequest(BaseModel):
    project: str = Field(
        description="Project name. Use '_general' for cross-project docs.",
        example="myapp",
    )
    filename: str = Field(description="Target filename in raw/<project>/")
    content: str = Field(description="UTF-8 content of the markdown file")


class IngestFileResponse(BaseModel):
    success: bool
    source_file: str
    project: str
    pages_created: list[str]
    pages_updated: list[str]
    pages_superseded: list[str]
    conflict_ids: list[str]
    skills_triggered: list[str]
    lint_errors: int
    lint_warnings: int
    analysis_notes: str
    error: str | None = None


class RebuildRequest(BaseModel):
    confirm: bool = Field(
        description="Must be true. Prevents accidental rebuild.",
    )


class RebuildProgressEvent(BaseModel):
    """SSE event emitted during rebuild."""
    current: int
    total: int
    filename: str
    status: str  # "processing" | "done" | "failed"


class RebuildResult(BaseModel):
    total: int
    success: int
    failed: int
    errors: list[dict]
    conflict_ids: list[str]


# ─────────────────────────────────────────────
# Chat
# ─────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str = Field(description="Client-generated UUID for this session")
    question: str = Field(description="User question", min_length=1, max_length=4000)
    project_filter: str | None = Field(
        default=None,
        description="Limit search to this project. null = search all projects.",
    )


class ChatMessageOut(BaseModel):
    role: str
    content: str
    timestamp: str
    cited_slugs: list[str]


class ChatHistoryResponse(BaseModel):
    session_id: str
    messages: list[ChatMessageOut]


class SessionListResponse(BaseModel):
    sessions: list[dict]
    # [{session_id, created_at, message_count, last_question}]


class CrystallizeRequest(BaseModel):
    session_id: str


class CrystallizeResponse(BaseModel):
    slug: str | None
    message: str


# ─────────────────────────────────────────────
# Wiki
# ─────────────────────────────────────────────

class WikiTreeResponse(BaseModel):
    projects: dict[str, list[dict]]
    # {project_name: [{slug, title, type, confidence, tags}]}
    total_pages: int
    open_conflicts: int


class WikiPageResponse(BaseModel):
    slug: str
    title: str
    project: str
    page_type: str
    tags: list[str]
    confidence: float
    sources: int
    last_confirmed: str
    content_html: str       # rendered markdown → HTML for UI display
    content_raw: str        # raw markdown for editing
    char_count: int
    wikilinks: list[str]    # outgoing [[links]]
    superseded_by: str | None
    supersedes: str | None


class WikiSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    project: str | None = None


class WikiSearchResponse(BaseModel):
    results: list[dict]
    # [{slug, title, project, excerpt, score}]


# ─────────────────────────────────────────────
# Conflicts
# ─────────────────────────────────────────────

class ConflictOut(BaseModel):
    id: str
    status: str             # "OPEN" | "RESOLVED"
    date: str
    project: str
    source_file: str
    conflict_type: str
    page_a_slug: str
    page_b_ref: str
    context_a: str
    context_b: str
    suggested_options: list[str]
    user_comment: str
    resolution: str
    skill_extracted: str
    resolved_at: str


class ConflictsListResponse(BaseModel):
    open: list[ConflictOut]
    resolved: list[ConflictOut]
    total_open: int


class ResolveConflictRequest(BaseModel):
    resolution: str = Field(description="Chosen option text or custom decision")
    user_comment: str = Field(description="User's explanation for the decision")
    extract_skill: bool = Field(
        default=True,
        description="Whether to auto-extract a skill from this resolution",
    )


class ResolveConflictResponse(BaseModel):
    success: bool
    conflict_id: str
    skill_extracted: str
    message: str


class AddCommentRequest(BaseModel):
    comment: str = Field(description="Comment or instruction for this conflict")


# ─────────────────────────────────────────────
# Audit
# ─────────────────────────────────────────────

class AuditRequest(BaseModel):
    llm_audit: bool = Field(default=False)
    project: str | None = None


class AuditResponse(BaseModel):
    ran_at: str
    total_pages: int
    duration_seconds: float
    llm_audit_ran: bool
    summary: str
    structural: dict
    semantic: list[dict]


# ─────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────

class SettingsResponse(BaseModel):
    llm_base_url: str
    llm_model: str
    wiki_data_path: str
    limits: dict
    ingest: dict
    query: dict
    audit: dict


class UpdateSettingsRequest(BaseModel):
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    temperature: float | None = None