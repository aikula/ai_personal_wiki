"""
ingest_types.py — Typed schemas for the ingest pipeline.

All dataclasses used as contract between Step 1 (analysis) and Step 2 (generation),
and between IngestAgent and the API layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.linter import LintReport


@dataclass
class PlannedPage:
    slug: str
    title: str
    project: str
    page_type: str          # "entity" | "concept"
    tags: list[str]
    action: str             # "create" | "update" | "supersede"
    supersedes: str | None = None
    source_sections: list[str] = field(default_factory=list)
    confidence: float = 1.0
    sources_count: int = 1


@dataclass
class DetectedConflict:
    conflict_type: str
    existing_slug: str
    source_ref: str
    context_existing: str
    context_source: str
    suggested_options: list[str]
    description: str = ""       # 1-2 sentence summary of what exactly conflicts
    is_cross_project: bool = False


@dataclass
class AnalysisResult:
    source_file: str
    project: str
    pages_to_create: list[PlannedPage] = field(default_factory=list)
    pages_to_update: list[PlannedPage] = field(default_factory=list)
    pages_to_supersede: list[PlannedPage] = field(default_factory=list)
    conflicts: list[DetectedConflict] = field(default_factory=list)
    skills_triggered: list[str] = field(default_factory=list)
    analysis_notes: str = ""


@dataclass
class IngestResult:
    success: bool
    source_file: str
    project: str
    pages_created: list[str]
    pages_updated: list[str]
    pages_superseded: list[str]
    conflict_ids: list[str]
    skills_triggered: list[str]
    lint_report: LintReport | None
    error: str | None = None
    analysis_notes: str = ""
