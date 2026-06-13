"""
Regression tests for QueryAgent policies.

Verifies that outline-first optimisation (index-first) is used
for factual and comparison queries.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

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

    @patch("app.agents.query_agent.classify_question")
    def test_reads_outline_for_each_project_page(self, mock_classify, query_agent):
        agent, fs, llm = query_agent

        mock_classify.return_value = ("comparison", ["redis"])

        fs.search_pages_weighted.return_value = [
            {"slug": "proj_a/redis", "score": 1.0},
            {"slug": "proj_a/cache", "score": 0.9},
            {"slug": "proj_b/redis", "score": 1.0},
            {"slug": "proj_b/store", "score": 0.8},
        ]

        fs.read_page.side_effect = lambda slug: _wiki_page(
            slug, slug.split("/")[0], "Title", f"# {slug}\nContent"
        )

        fs.read_page_outline.side_effect = lambda slug: _outline(
            slug, "Title", "Uses redis for caching", ["Redis Config"]
        )

        llm.call.return_value = "Redis is used in both projects. [[proj_a/redis]] [[proj_b/redis]]"

        session = ChatSession(session_id="test-1", created_at="2026-05-25")
        result = agent.run("How do projects use Redis?", session)

        assert fs.read_page_outline.call_count >= 2
        assert "[[proj_a/redis]]" in result.answer
        assert result.question_type == "comparison"


class TestPolicyFactualOutlineFirst:
    """Ensure factual policy reads outlines before full pages."""

    @patch("app.agents.query_agent.classify_question")
    def test_reads_outline_to_select_best_pages(self, mock_classify, query_agent):
        agent, fs, llm = query_agent

        mock_classify.return_value = ("factual", ["redis"])

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

        assert fs.read_page_outline.call_count >= 1
        assert result.question_type == "factual"


class TestClaimsInFactualPolicy:
    """Ensure factual policy searches and formats claims."""

    @patch("app.agents.query_agent.classify_question")
    def test_claims_retrieved_for_factual(self, mock_classify, query_agent):
        from app.core.wiki_fs import Claim

        agent, fs, llm = query_agent

        mock_classify.return_value = ("factual", ["redis"])

        fs.search_pages_weighted.return_value = [
            {"slug": "myapp/redis", "score": 1.0},
        ]
        fs.read_page.side_effect = lambda slug: _wiki_page(
            slug, "myapp", "Redis Config", "# myapp/redis\nRedis 7.2"
        )
        fs.read_page_outline.side_effect = lambda slug: _outline(
            slug, "Title", "Redis configuration", ["Redis"]
        )

        # Mock claims search
        claim = Claim(
            claim_id="myapp/deploy_guide#chunk-001-claim-001",
            source_id="myapp/deploy_guide",
            source_path="raw/myapp/deploy_guide.md",
            source_sha256="abc123",
            source_section="## Redis",
            quote="Redis 7.2 is used for caching",
            normalized="Redis 7.2 используется для кеширования",
            related_slugs=["myapp/redis"],
            confidence=0.9,
            status="active",
            chunk_id="chunk-001",
            project="myapp",
            created="2026-06-13",
        )
        fs.search_claims.return_value = [claim]

        llm.call.return_value = "Redis 7.2 используется. [[myapp/redis]] [[_claims/myapp/myapp__deploy_guide/chunk-001/chunk-001-claim-001]]"

        session = ChatSession(session_id="test-3", created_at="2026-05-25")
        result = agent.run("Какая версия Redis используется?", session)

        fs.search_claims.assert_called_once()
        assert "_claims/" in result.answer


class TestClaimsInReAct:
    """Ensure ReAct policy supports search_claims tool."""

    @patch("app.agents.query_agent.classify_question")
    def test_search_claims_tool_in_react(self, mock_classify, query_agent):
        from app.core.wiki_fs import Claim

        agent, fs, llm = query_agent

        mock_classify.return_value = ("exploratory", ["redis", "cache"])

        # First call returns search_claims action, second returns answer
        llm.call.side_effect = [
            json.dumps({
                "action": "search_claims",
                "input": {"query": "Redis caching"}
            }),
            json.dumps({
                "action": "answer",
                "content": "Redis is used for caching [[myapp/redis]] [[_claims/myapp/myapp__deploy_guide/chunk-001/chunk-001-claim-001]]"
            }),
        ]

        claim = Claim(
            claim_id="myapp/deploy_guide#chunk-001-claim-001",
            source_id="myapp/deploy_guide",
            source_path="raw/myapp/deploy_guide.md",
            source_sha256="abc123",
            source_section="## Redis",
            quote="Redis 7.2 is used for caching",
            normalized="Redis 7.2 используется для кеширования",
            related_slugs=["myapp/redis"],
            confidence=0.9,
            status="active",
            chunk_id="chunk-001",
            project="myapp",
            created="2026-06-13",
        )
        fs.search_claims.return_value = [claim]

        session = ChatSession(session_id="test-4", created_at="2026-05-25")
        result = agent.run("Расскажи про кеширование", session)

        assert "_claims/myapp/myapp__deploy_guide/" in result.answer
        assert result.iterations == 2  # 1 search_claims + 1 answer


class TestFormatClaimsForContext:
    """Verify format_claims_for_context output."""

    def test_formats_claims_with_wikilinks(self):
        from app.core.wiki_fs import Claim
        from app.agents.query_search import format_claims_for_context

        claim = Claim(
            claim_id="myapp/deploy_guide#chunk-001-claim-001",
            source_id="myapp/deploy_guide",
            source_path="raw/myapp/deploy_guide.md",
            source_sha256="abc123",
            source_section="## Redis",
            quote="Redis 7.2 is used for caching",
            normalized="Redis 7.2 используется для кеширования",
            related_slugs=["myapp/redis"],
            confidence=0.9,
            status="active",
            chunk_id="chunk-001",
            project="myapp",
            created="2026-06-13",
        )

        result = format_claims_for_context([claim])

        assert "[[_claims/myapp/myapp__deploy_guide/chunk-001/chunk-001-claim-001]]" in result
        assert "Redis 7.2 используется" in result
        assert "Relevant Claims" in result

    def test_empty_claims(self):
        from app.agents.query_search import format_claims_for_context

        assert format_claims_for_context([]) == ""
