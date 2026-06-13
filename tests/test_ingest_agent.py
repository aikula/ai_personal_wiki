"""
Integration tests for IngestAgent with mocked LLM.

Verifies the two-step ingest pipeline: analysis → page generation.
Uses a mock LLM that returns valid JSON matching each step's schema.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.agents.ingest_agent import IngestAgent
from app.config import Settings
from app.core.wiki_fs import WikiFS


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.wiki_data_path = str(tmp_path)
    s.ingest.large_source_threshold_chars = 50000
    return s


@pytest.fixture
def fs(settings):
    return WikiFS(settings)


def _mock_llm_step1() -> str:
    """Return a JSON response for Step 1 analysis."""
    return json.dumps({
        "pages_to_create": [
            {"slug": "myapp/config", "title": "Configuration Guide", "project": "myapp",
             "page_type": "concept", "tags": ["config", "setup"],
             "action": "create", "supersedes": None,
             "source_sections": ["## Configuration"], "confidence": 0.9, "sources_count": 1}
        ],
        "pages_to_update": [],
        "pages_to_supersede": [],
        "conflicts": [],
        "claims": [
            {"quote": "The default timeout is 30 seconds",
             "normalized": "Таймаут по умолчанию 30 секунд",
             "source_section": "## Configuration",
             "related_slugs": ["myapp/config"],
             "confidence": 0.9, "status": "active"}
        ],
        "skills_triggered": ["Primary docs are authoritative for infrastructure"],
        "analysis_notes": "Single page, no conflicts.",
    })


def _mock_llm_step2(extra_content: str = "") -> str:
    """Return a JSON response for Step 2 page generation."""
    body = extra_content or "# Configuration Guide\n\nThe default timeout is **30 seconds**."
    return json.dumps({
        "meta": {
            "title": "Configuration Guide",
            "type": "concept",
            "tags": ["config", "setup"],
            "confidence": 0.9,
            "sources": 1,
            "last_confirmed": "2026-06-13",
            "supersedes": None,
            "superseded_by": None,
        },
        "content": body,
    })


class TestIngestAgentAnalysis:
    """Test the two-step ingest pipeline."""

    def test_full_ingest_creates_page(self, settings, fs):
        """End-to-end: save raw file → run agent → verify page created."""
        fs.save_raw_file(
            "myapp", "config_guide.md",
            "# Configuration Guide\n\n## Configuration\nThe default timeout is 30 seconds.\n"
        )

        llm = MagicMock()
        llm.call.side_effect = [
            _mock_llm_step1(),  # Step 1 analysis
            _mock_llm_step2(),  # Step 2 page generation
        ]

        interpreter = MagicMock()
        agent = IngestAgent(fs, llm, interpreter, settings)

        result = agent.run("myapp/config_guide.md")

        assert result.success is True
        assert "myapp/config" in result.pages_created
        assert result.project == "myapp"
        assert result.error is None

        # Verify page was written
        page = fs.read_page("myapp/config")
        assert page is not None
        assert "таймаут" in page.content.lower() or "timeout" in page.content.lower()

    def test_ingest_with_conflict_detected(self, settings, fs):
        """When LLM reports a conflict, it should be logged and not block ingest."""
        fs.save_raw_file(
            "myapp", "new_source.md",
            "# New Source\n\n## Config\nPort is 8080.\n"
        )

        # Step 1 returns a conflict
        conflict_step1 = """{
  "pages_to_create": [
    {"slug": "myapp/config", "title": "Config", "project": "myapp",
     "page_type": "entity", "tags": ["config"],
     "action": "update", "supersedes": null,
     "source_sections": ["## Config"], "confidence": 0.9, "sources_count": 1}
  ],
  "pages_to_update": [],
  "pages_to_supersede": [],
  "conflicts": [
    {"conflict_type": "factual_contradiction",
     "existing_slug": "myapp/config",
     "source_ref": "raw/myapp/new_source.md",
     "description": "Port number differs between sources",
     "context_existing": "Port 3000",
     "context_source": "Port 8080",
     "suggested_options": ["Trust wiki", "Trust source"],
     "is_cross_project": false}
  ],
  "claims": [],
  "skills_triggered": [],
  "analysis_notes": "Conflict detected: port mismatch."
}"""

        llm = MagicMock()
        llm.call.side_effect = [
            conflict_step1,
            _mock_llm_step2(),
        ]

        interpreter = MagicMock()
        agent = IngestAgent(fs, llm, interpreter, settings)

        result = agent.run("myapp/new_source.md")

        assert result.success is True
        assert len(result.conflict_ids) > 0

        # Conflict should be written to conflicts.md
        conflicts_raw = fs.read_conflicts_raw()
        assert "CONFLICT" in conflicts_raw
        assert "factual_contradiction" in conflicts_raw

    def test_ingest_calls_step1_then_step2(self, settings, fs):
        """Verify the LLM is called twice: once for analysis, once for generation."""
        fs.save_raw_file(
            "myapp", "simple.md",
            "# Simple\n\nContent here.\n"
        )

        llm = MagicMock()
        llm.call.side_effect = [
            _mock_llm_step1(),
            _mock_llm_step2(),
        ]

        interpreter = MagicMock()
        agent = IngestAgent(fs, llm, interpreter, settings)

        agent.run("myapp/simple.md")

        # LLM called exactly twice: step 1 + step 2 (1 page)
        assert llm.call.call_count == 2

    def test_ingest_result_has_analysis_notes(self, settings, fs):
        """Analysis notes from step 1 appear in the result."""
        fs.save_raw_file(
            "myapp", "notes_test.md",
            "# Notes\n\nTest content.\n"
        )

        llm = MagicMock()
        llm.call.side_effect = [
            _mock_llm_step1(),
            _mock_llm_step2(),
        ]

        interpreter = MagicMock()
        agent = IngestAgent(fs, llm, interpreter, settings)

        result = agent.run("myapp/notes_test.md")

        assert "Single page" in result.analysis_notes or result.analysis_notes != ""

    def test_ingest_small_source_no_chunking(self, settings, fs):
        """Small sources (< large_source_threshold_chars) skip chunking."""
        fs.save_raw_file(
            "_general", "small_file.md",
            "# Small\n\nShort content.\n"
        )

        llm = MagicMock()
        llm.call.side_effect = [
            _mock_llm_step1(),
            _mock_llm_step2(),
        ]

        interpreter = MagicMock()
        agent = IngestAgent(fs, llm, interpreter, settings)

        result = agent.run("_general/small_file.md")

        assert result.success is True
        assert len(result.pages_created) >= 1


class TestIngestAgentErrors:
    """Error handling edge cases."""

    def test_missing_source_file(self, settings, fs):
        """Agent returns error (not crash) for nonexistent source."""
        llm = MagicMock()
        interpreter = MagicMock()
        agent = IngestAgent(fs, llm, interpreter, settings)

        result = agent.run("nonexistent/file.md")

        assert result.success is False
        assert "not found" in (result.error or "").lower()

    def test_llm_step1_malformed_json(self, settings, fs):
        """Agent handles malformed JSON from LLM gracefully."""
        fs.save_raw_file("myapp", "bad.json.md", "# Bad JSON\n\nTest")

        llm = MagicMock()
        llm.call.side_effect = [
            "This is not valid JSON at all",  # Step 1 fails
        ]

        interpreter = MagicMock()
        agent = IngestAgent(fs, llm, interpreter, settings)

        result = agent.run("myapp/bad.json.md")

        assert result.success is False

    def test_llm_step2_retry_on_parse_failure(self, settings, fs):
        """Agent retries step 2 when JSON parsing fails (3 tiers in step2 only)."""
        fs.save_raw_file("myapp", "retry_test.md", "# Retry\n\nTest")

        llm = MagicMock()
        llm.call.side_effect = [
            _mock_llm_step1(),                                   # Step 1 analysis
            "Invalid JSON",                                      # Step 2 — Tier 1 fails
            "Still not valid",                                   # Step 2 — Tier 2 (2x tokens)
            _mock_llm_step2(),                                   # Step 2 — Tier 3 (escalation, llm.max_completion_tokens)
        ]

        interpreter = MagicMock()
        agent = IngestAgent(fs, llm, interpreter, settings)

        result = agent.run("myapp/retry_test.md")

        assert result.success is True
        assert "myapp/config" in result.pages_created

    def test_conversion_error_preserves_project(self, settings, fs, monkeypatch):
        """When read_raw_source_file raises RawSourceError, project should be correct."""
        def _raise(*a, **kw):
            raise __import__("app.core.raw_sources", fromlist=["RawSourceError"]).RawSourceError("conversion failed")
        monkeypatch.setattr("app.agents.ingest_agent.read_raw_source_file", _raise)
        llm = MagicMock()
        interpreter = MagicMock()
        agent = IngestAgent(fs, llm, interpreter, settings)
        result = agent.run("eywa-demo/bad.pdf")
        assert result.success is False
        assert result.project == "eywa-demo"
        assert "conversion failed" in result.error

    def test_missing_source_preserves_project(self, settings, fs, monkeypatch):
        """When read_raw_source_file returns None, project should be correct."""
        monkeypatch.setattr("app.agents.ingest_agent.read_raw_source_file", lambda *a, **kw: None)
        llm = MagicMock()
        interpreter = MagicMock()
        agent = IngestAgent(fs, llm, interpreter, settings)
        result = agent.run("eywa-demo/missing.md")
        assert result.success is False
        assert result.project == "eywa-demo"
