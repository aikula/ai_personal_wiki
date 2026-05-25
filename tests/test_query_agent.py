"""
Regression tests for QueryAgent policies.

Verifies that outline-first optimisation (index-first) is used
for factual and comparison queries.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from app.agents.query_agent import QueryAgent
from app.agents.query_types import ChatSession
from app.config import Settings
from app.core.wiki_fs import PageOutline, WikiPage


@pytest.fixture
def query_agent(tmp_path):
    """Build a QueryAgent with fully mocked dependencies."""
    fs = MagicMock()
    fs.root = tmp_path
    fs.read_skills.return_value = ""
    fs.read_agents_md = ""

    llm = MagicMock()
    interpreter = MagicMock()

    settings = Settings()
    settings.wiki_data_path = str(tmp_path)

    agent = QueryAgent(fs, llm, interpreter, settings)
    return agent, fs, llm


def _wiki_page(slug: str, project: str, title: str, content: str) -> WikiPage:
    meta = {
        "title": title,
        "project": project,
        "type": "entity",
        "tags": [],
        "confidence": 1.0,
        "sources": 1,
        "last_confirmed": date.today().isoformat(),
        "supersedes": None,
        "superseded_by": None,
        "created": date.today().isoformat(),
    }
    raw = f"---\n{meta}\n---\n{content}"
    return WikiPage(
        slug=slug,
        path=MagicMock(),
        meta=meta,
        content=content,
        raw=raw,
        char_count=len(raw),
    )


def _outline(slug: str, title: str, synopsis: str, headings: list[str]) -> PageOutline:
    return PageOutline(
        slug=slug,
        title=title,
        project=slug.split("/")[0],
        page_type="entity",
        tags=[],
        synopsis=synopsis,
        headings=[{"text": h, "anchor": h.lower().replace(" ", "-"), "level": 2,
                   "char_count": 10, "preview": ""} for h in headings],
        wikilinks=[],
        char_count=100,
        confidence=1.0,
    )


class TestPolicyComparisonOutlineFirst:
    """Ensure comparison policy reads outlines before full pages."""

    def test_reads_outline_for_each_project_page(self, query_agent):
        agent, fs, llm = query_agent

        # Mock classification
        agent._classify = lambda q: ("comparison", ["redis"])

        # Mock search results — two projects, two pages each
        fs.search_pages_weighted.return_value = [
            {"slug": "proj_a/redis", "score": 1.0},
            {"slug": "proj_a/cache", "score": 0.9},
            {"slug": "proj_b/redis", "score": 1.0},
            {"slug": "proj_b/store", "score": 0.8},
        ]

        # Mock read_page
        fs.read_page.side_effect = lambda slug: _wiki_page(
            slug, slug.split("/")[0], "Title", f"# {slug}\nContent"
        )

        # Mock read_page_outline — all match keyword "redis"
        fs.read_page_outline.side_effect = lambda slug: _outline(
            slug, "Title", "Uses redis for caching", ["Redis Config"]
        )

        # Mock answer generation
        llm.call.return_value = "Redis is used in both projects. [[proj_a/redis]] [[proj_b/redis]]"

        session = ChatSession(session_id="test-1", created_at="2026-05-25")
        result = agent.run("How do projects use Redis?", session)

        # Assert outlines were consulted
        assert fs.read_page_outline.call_count >= 2
        # Assert final answer contains citations
        assert "[[proj_a/redis]]" in result.answer
        assert result.question_type == "comparison"


class TestPolicyFactualOutlineFirst:
    """Ensure factual policy reads outlines before full pages."""

    def test_reads_outline_to_select_best_pages(self, query_agent):
        agent, fs, llm = query_agent

        agent._classify = lambda q: ("factual", ["redis"])

        fs.search_pages_weighted.return_value = [
            {"slug": "myapp/redis", "score": 1.0},
            {"slug": "myapp/cache", "score": 0.9},
        ]

        fs.read_page.side_effect = lambda slug: _wiki_page(
            slug, "myapp", "Title", f"# {slug}\nContent"
        )

        fs.read_page_outline.side_effect = lambda slug: _outline(
            slug, "Title", "Redis configuration details", ["Redis"]
        )

        llm.call.return_value = "Redis is configured. [[myapp/redis]]"

        session = ChatSession(session_id="test-2", created_at="2026-05-25")
        result = agent.run("How is Redis configured?", session)

        # Outline should be consulted for each top page
        assert fs.read_page_outline.call_count >= 1
        assert result.question_type == "factual"
