"""
test_large_source_ingest.py — Tests for outline parser, chunking, and merge analysis.
"""

import pytest

from app.config import Settings
from app.core.large_source_ingest import (
    chunk_by_outline,
    parse_outline,
)
from app.core.large_source_merge import merge_analysis
from app.core.large_source_types import ChunkAnalysisResult


@pytest.fixture
def settings():
    return Settings()


# ── Outline parser ──────────────────────────────────────────────

class TestParseOutline:
    def test_markdown_headings(self):
        content = (
            "# Introduction\n\n"
            "Intro text here.\n\n"
            "## Features\n\n"
            "Feature A and B.\n\n"
            "### Feature A\n\n"
            "Details about A.\n\n"
            "### Feature B\n\n"
            "Details about B.\n\n"
            "## Configuration\n\n"
            "Config details.\n"
        )
        outline = parse_outline(content, "test/doc.md")

        assert outline.source_path == "test/doc.md"
        assert outline.total_chars == len(content)
        assert len(outline.items) == 5
        assert outline.items[0].text == "Introduction"
        assert outline.items[0].level == 1
        assert outline.items[1].text == "Features"
        assert outline.items[1].level == 2
        assert outline.items[2].text == "Feature A"
        assert outline.items[2].level == 3

    def test_single_heading(self):
        content = "# Hello\n\nWorld."
        outline = parse_outline(content)
        assert len(outline.items) == 1
        assert outline.items[0].text == "Hello"

    def test_no_headings_fallback(self):
        content = "This is a paragraph.\n\nThis is another paragraph with more text.\n\nThird paragraph."
        outline = parse_outline(content)
        # Should use paragraph fallback or full document
        assert len(outline.items) >= 1
        assert outline.total_chars == len(content)

    def test_empty_content(self):
        outline = parse_outline("")
        assert len(outline.items) == 1
        assert outline.items[0].text == "(full document)"

    def test_deep_nesting(self):
        content = (
            "# L1\n\n"
            "## L2\n\n"
            "### L3\n\n"
            "#### L4\n\n"
            "##### L5\n\n"
            "###### L6\n\n"
        )
        outline = parse_outline(content)
        assert len(outline.items) == 6
        assert outline.items[5].level == 6

    def test_section_positions(self):
        content = "# First\n\nContent 1\n\n## Second\n\nContent 2"
        outline = parse_outline(content)
        assert outline.items[0].start_pos == 0
        assert outline.items[0].end_pos > outline.items[0].start_pos
        # First section should end before second heading
        assert outline.items[0].end_pos <= content.index("## Second")


# ── Chunking ────────────────────────────────────────────────────

class TestChunking:
    def test_small_source_single_chunk(self, settings):
        content = "# Intro\n\nSmall content here."
        outline = parse_outline(content)
        chunks = chunk_by_outline(outline, content, settings)

        assert len(chunks) == 1
        assert chunks[0].chunk_id == "chunk-001"
        assert chunks[0].split_reason == "outline"

    def test_multiple_sections(self, settings):
        content = (
            "# Section A\n\nContent A.\n\n"
            "# Section B\n\nContent B.\n\n"
            "# Section C\n\nContent C.\n"
        )
        outline = parse_outline(content)
        chunks = chunk_by_outline(outline, content, settings)

        assert len(chunks) == 3
        assert chunks[0].section_path == ["Section A"]
        assert chunks[1].section_path == ["Section B"]

    def test_large_section_split_by_subheadings(self, settings):
        # Create a section larger than max_chars with sub-headings
        big_content = "# Main\n\n"
        for i in range(20):
            big_content += f"\n## Sub {i}\n\n{'x' * 1000}\n"

        outline = parse_outline(big_content)
        chunks = chunk_by_outline(outline, big_content, settings)

        # Should split by sub-headings
        assert len(chunks) > 1
        # Each chunk should have "Main" in section path
        assert "Main" in chunks[0].section_path

    def test_chunk_ids_sequential(self, settings):
        content = "# A\n\nText\n\n# B\n\nText\n\n# C\n\nText"
        outline = parse_outline(content)
        chunks = chunk_by_outline(outline, content, settings)

        assert chunks[0].chunk_id == "chunk-001"
        assert chunks[1].chunk_id == "chunk-002"
        assert chunks[2].chunk_id == "chunk-003"

    def test_section_path_hierarchy(self, settings):
        content = "# Parent\n\n## Child\n\nGrandchild content."
        outline = parse_outline(content)
        chunks = chunk_by_outline(outline, content, settings)

        # Child chunk should have Parent in path
        child_chunks = [c for c in chunks if "Child" in c.section_path]
        assert len(child_chunks) >= 1
        assert "Parent" in child_chunks[0].section_path

    def test_section_path_uses_nearest_parent_hierarchy(self, settings):
        content = (
            "# A\n\n"
            "## B\n\nB text.\n\n"
            "## C\n\nC text.\n\n"
            "### D\n\nD text."
        )
        outline = parse_outline(content)
        chunks = chunk_by_outline(outline, content, settings)

        d_chunk = next(c for c in chunks if c.section_path[-1] == "D")
        assert d_chunk.section_path == ["A", "C", "D"]

    def test_chunk_preserves_content(self, settings):
        content = "# Test\n\nImportant content that must not be lost."
        outline = parse_outline(content)
        chunks = chunk_by_outline(outline, content, settings)

        combined = "".join(c.text for c in chunks)
        assert "Important content" in combined

    def test_split_chunks_preserve_source_path(self, settings):
        settings.ingest.chunk_max_chars = 500
        settings.ingest.chunk_min_chars = 100
        settings.ingest.chunk_target_chars = 300
        content = "# Main\n\n" + "\n\n".join(f"paragraph {i} " + "x" * 180 for i in range(8))
        outline = parse_outline(content, "raw/project/source.md")
        chunks = chunk_by_outline(outline, content, settings)

        assert len(chunks) > 1
        assert all(c.source_path == "raw/project/source.md" for c in chunks)


# ── Merge analysis ──────────────────────────────────────────────

class TestMergeAnalysis:
    def test_basic_merge(self):
        results = [
            ChunkAnalysisResult(
                chunk_id="chunk-001", source_id="test/doc",
                section_path=["Intro"],
                candidate_pages=["test/overview"],
                claims=[{"quote": "Redis uses volatile-lru", "normalized": "Redis configured with volatile-lru eviction policy"}],
                conflicts=[],
            ),
            ChunkAnalysisResult(
                chunk_id="chunk-002", source_id="test/doc",
                section_path=["Features"],
                candidate_pages=["test/overview", "test/features"],
                claims=[{"quote": "PostgreSQL stores data", "normalized": "PostgreSQL is primary data persistence layer"}],
                conflicts=[],
            ),
        ]
        merged = merge_analysis(results, "test/doc", "raw/test/doc.md")

        assert merged.source_id == "test/doc"
        assert merged.total_chunks == 2
        assert merged.chunks_processed == 2
        assert merged.chunks_failed == 0
        # Pages deduplicated by slug
        assert len(merged.all_candidate_pages) == 2
        assert len(merged.all_claims) == 2

    def test_merge_keeps_page_source_sections(self):
        results = [
            ChunkAnalysisResult(
                chunk_id="chunk-001", source_id="test/doc",
                section_path=["A"],
                candidate_pages=["test/a"],
                page_sections={"test/a": ["relevant section A"]},
            ),
            ChunkAnalysisResult(
                chunk_id="chunk-002", source_id="test/doc",
                section_path=["B"],
                candidate_pages=["test/a"],
                page_sections={"test/a": ["relevant section B"]},
            ),
        ]
        merged = merge_analysis(results, "test/doc", "raw/test/doc.md")

        page = merged.all_candidate_pages[0]
        assert page["source_sections"] == ["relevant section A", "relevant section B"]

    def test_duplicate_claims_deduped(self):
        results = [
            ChunkAnalysisResult(
                chunk_id="chunk-001", source_id="test/doc",
                section_path=["A"],
                claims=[{"quote": "Same fact", "normalized": "same fact normalized"}],
            ),
            ChunkAnalysisResult(
                chunk_id="chunk-002", source_id="test/doc",
                section_path=["B"],
                claims=[{"quote": "Same fact", "normalized": "same fact normalized"}],
            ),
        ]
        merged = merge_analysis(results, "test/doc", "raw/test/doc.md")

        assert len(merged.all_claims) == 1

    def test_conflicts_collected(self):
        results = [
            ChunkAnalysisResult(
                chunk_id="chunk-001", source_id="test/doc",
                section_path=["A"],
                conflicts=[{"conflict_type": "factual", "description": "Conflict 1"}],
            ),
            ChunkAnalysisResult(
                chunk_id="chunk-002", source_id="test/doc",
                section_path=["B"],
                conflicts=[{"conflict_type": "version", "description": "Conflict 2"}],
            ),
        ]
        merged = merge_analysis(results, "test/doc", "raw/test/doc.md")

        assert len(merged.all_conflicts) == 2

    def test_failed_chunks_counted(self):
        results = [
            ChunkAnalysisResult(
                chunk_id="chunk-001", source_id="test/doc",
                section_path=["A"],
                outcome="page",
            ),
            ChunkAnalysisResult(
                chunk_id="chunk-002", source_id="test/doc",
                section_path=["B"],
                outcome="failed",
            ),
        ]
        merged = merge_analysis(results, "test/doc", "raw/test/doc.md")

        assert merged.chunks_processed == 1
        assert merged.chunks_failed == 1

    def test_triage_report_generated(self):
        results = [
            ChunkAnalysisResult(
                chunk_id="chunk-001", source_id="test/doc",
                section_path=["A"],
                candidate_pages=["test/page"],
                outcome="page",
            ),
        ]
        merged = merge_analysis(results, "test/doc", "raw/test/doc.md")

        assert "test/doc" in merged.triage_report
        assert "Chunks:" in merged.triage_report
        assert "Pages planned:" in merged.triage_report

    def test_page_write_plan(self):
        results = [
            ChunkAnalysisResult(
                chunk_id="chunk-001", source_id="test/doc",
                section_path=["A"],
                candidate_pages=["test/page1", "test/page2"],
            ),
        ]
        merged = merge_analysis(results, "test/doc", "raw/test/doc.md")

        assert len(merged.page_write_plan) == 2
        assert merged.page_write_plan[0]["slug"] == "test/page1"
        assert "source_chunks" in merged.page_write_plan[0]

    def test_empty_results(self):
        merged = merge_analysis([], "test/doc", "raw/test/doc.md")

        assert merged.total_chunks == 0
        assert merged.chunks_processed == 0
        assert len(merged.all_candidate_pages) == 0
        assert len(merged.all_claims) == 0
