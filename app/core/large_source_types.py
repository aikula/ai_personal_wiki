"""Data types for large source ingest — outline, chunk, and merge analysis."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OutlineItem:
    text: str
    level: int
    start_pos: int
    end_pos: int
    char_count: int
    preview: str = ""


@dataclass
class DocumentOutline:
    source_path: str
    total_chars: int
    items: list[OutlineItem] = field(default_factory=list)

    @property
    def section_count(self) -> int:
        return len(self.items)


@dataclass
class Chunk:
    chunk_id: str
    source_path: str
    section_path: list[str]
    text: str
    char_count: int
    split_reason: str = "outline"
    headings: list[dict] = field(default_factory=list)


@dataclass
class ChunkAnalysisResult:
    chunk_id: str
    source_id: str
    section_path: list[str]
    candidate_pages: list[str] = field(default_factory=list)
    page_sections: dict[str, list[str]] = field(default_factory=dict)
    claims: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    ignored_sections: list[str] = field(default_factory=list)
    outcome: str = "pending"


@dataclass
class MergeAnalysisResult:
    source_id: str
    source_path: str
    total_chunks: int
    chunks_processed: int
    chunks_failed: int
    all_candidate_pages: list[dict] = field(default_factory=list)
    all_claims: list[dict] = field(default_factory=list)
    all_conflicts: list[dict] = field(default_factory=list)
    triage_report: str = ""
    page_write_plan: list[dict] = field(default_factory=list)
