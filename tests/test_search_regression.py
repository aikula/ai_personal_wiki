"""
test_search_regression.py — Search quality regression tests (Phase 5).

These tests verify that:
1. Title matches outrank body matches
2. Tag matches outrank body matches
3. Summary matches outrank body matches
4. Heading matches outrank body matches
5. Multi-word queries work correctly
6. Project filtering works
7. Empty queries return empty results
8. Search is case-insensitive
"""

from datetime import date

import pytest

from app.config import Settings
from app.core.search_provider import (
    WeightedFieldSearch,
    create_search_provider,
)
from app.core.wiki_fs import WikiFS


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.wiki_data_path = str(tmp_path)
    return s


@pytest.fixture
def fs(settings):
    return WikiFS(settings)


@pytest.fixture
def search(fs):
    return WeightedFieldSearch(fs)


def _make_page(fs, slug, title, content, tags=None, synopsis=None, project="_general"):
    meta = {
        "title": title,
        "project": project,
        "type": "entity",
        "tags": tags or [],
        "confidence": 0.9,
        "sources": 1,
        "last_confirmed": date.today().isoformat(),
        "supersedes": None,
        "superseded_by": None,
        "created": date.today().isoformat(),
    }
    if synopsis:
        meta["synopsis"] = synopsis
    return fs.write_page(slug, meta=meta, content=content)


# ── Field ranking tests ─────────────────────────────────────────

class TestFieldRanking:
    def test_title_outranks_body(self, fs, search):
        """Title match should have higher score than body-only match."""
        _make_page(fs, "test/title-match", "Redis Cache",
                    content="Some generic text without redis.")
        _make_page(fs, "test/body-match", "Generic Page",
                    content="Redis is mentioned here in the body.")

        results = search.search("Redis")
        assert len(results) >= 2
        assert results[0]["slug"] == "test/title-match"
        assert results[0]["score"] > results[1]["score"]
        # Title weight is 8x, body is 1x
        assert results[0]["field_scores"]["title"] >= 1
        assert results[1]["field_scores"]["body"] >= 1

    def test_tag_outranks_body(self, fs, search):
        """Tag match should have higher score than body-only match."""
        _make_page(fs, "test/tag-match", "Page A",
                    content="No redis here.", tags=["redis", "cache"])
        _make_page(fs, "test/body-match-2", "Page B",
                    content="Redis is in the body.", tags=[])

        results = search.search("redis")
        tag_result = next(r for r in results if r["slug"] == "test/tag-match")
        body_result = next(r for r in results if r["slug"] == "test/body-match-2")
        assert tag_result["score"] > body_result["score"]

    def test_summary_outranks_body(self, fs, search):
        """Synopsis match should outrank body-only match."""
        _make_page(fs, "test/summary-match", "Page A",
                    content="Body text without the keyword.",
                    synopsis="This page is about Redis caching.")
        _make_page(fs, "test/body-match-3", "Page B",
                    content="Redis is mentioned in the body.",
                    synopsis="Something else entirely.")

        results = search.search("Redis")
        summary_result = next(r for r in results if r["slug"] == "test/summary-match")
        body_result = next(r for r in results if r["slug"] == "test/body-match-3")
        assert summary_result["score"] > body_result["score"]

    def test_heading_outranks_body(self, fs, search):
        """Heading match should outrank body-only match."""
        _make_page(fs, "test/heading-match", "Page A",
                    content="# Redis Configuration\n\nDetails here.")
        _make_page(fs, "test/body-match-4", "Page B",
                    content="Redis is mentioned in the body.")

        results = search.search("Redis")
        heading_result = next(r for r in results if r["slug"] == "test/heading-match")
        body_result = next(r for r in results if r["slug"] == "test/body-match-4")
        assert heading_result["score"] > body_result["score"]


# ── Multi-word query tests ──────────────────────────────────────

class TestMultiWordQuery:
    def test_multi_word_query(self, fs, search):
        """Multi-word queries should match pages with multiple terms."""
        _make_page(fs, "test/multi", "Redis PostgreSQL",
                    content="Using Redis and PostgreSQL together.",
                    tags=["redis", "postgresql"])
        _make_page(fs, "test/single", "Redis Only",
                    content="Only Redis is used here.",
                    tags=["redis"])

        results = search.search("Redis PostgreSQL")
        multi_result = next(r for r in results if r["slug"] == "test/multi")
        single_result = next(r for r in results if r["slug"] == "test/single")
        # Multi-word page should score higher due to more matches
        assert multi_result["score"] > single_result["score"]

    def test_partial_match(self, fs, search):
        """Pages matching some query terms should still appear."""
        _make_page(fs, "test/partial", "Redis Cache",
                    content="Redis caching system.",
                    tags=["redis"])

        results = search.search("Redis PostgreSQL")
        assert len(results) >= 1
        assert results[0]["slug"] == "test/partial"


# ── Project filtering ───────────────────────────────────────────

class TestProjectFiltering:
    def test_filter_by_project(self, fs, search):
        """Search should respect project filter."""
        _make_page(fs, "proja/page", "Redis", content="Redis in A.", project="proja")
        _make_page(fs, "projb/page", "Redis", content="Redis in B.", project="projb")

        results = search.search("Redis", project="proja")
        # Filter out index pages that may match
        non_index = [r for r in results if "index" not in r["slug"]]
        assert all(r["project"] == "proja" for r in non_index)
        assert len(non_index) == 1

    def test_filter_by_projects_list(self, fs, search):
        """Search should support multiple project filter."""
        _make_page(fs, "proja/page", "Redis", content="Redis in A.", project="proja")
        _make_page(fs, "projb/page", "Redis", content="Redis in B.", project="projb")
        _make_page(fs, "projc/page", "Redis", content="Redis in C.", project="projc")

        results = search.search("Redis", projects=["proja", "projb"])
        # Filter out index pages that may match
        non_index = [r for r in results if "index" not in r["slug"]]
        assert all(r["project"] in ("proja", "projb") for r in non_index)
        assert len(non_index) == 2


# ── Edge cases ──────────────────────────────────────────────────

class TestSearchEdgeCases:
    def test_empty_query(self, fs, search):
        """Empty query should return empty results."""
        _make_page(fs, "test/edge", "Redis", content="Redis content.")
        results = search.search("")
        assert results == []

    def test_case_insensitive(self, fs, search):
        """Search should be case-insensitive."""
        _make_page(fs, "test/case", "REDIS Cache", content="Redis content.")

        results_lower = search.search("redis")
        results_upper = search.search("REDIS")
        results_mixed = search.search("ReDiS")

        assert len(results_lower) >= 1
        assert len(results_upper) >= 1
        assert len(results_mixed) >= 1
        assert results_lower[0]["slug"] == results_upper[0]["slug"]

    def test_no_results(self, fs, search):
        """Search for non-existent term should return empty."""
        _make_page(fs, "test/nomatch", "Redis", content="Redis content.")
        results = search.search("nonexistent_term_xyz")
        assert results == []

    def test_top_k_limit(self, fs, search):
        """Search should respect top_k limit."""
        for i in range(30):
            _make_page(fs, f"test/page{i}", f"Redis Page {i}",
                        content=f"Redis content {i}.")

        results = search.search("Redis", top_k=5)
        assert len(results) == 5


# ── Provider factory tests ──────────────────────────────────────

class TestSearchProviderFactory:
    def test_create_weighted_provider(self, fs):
        provider = create_search_provider(fs, "weighted")
        assert provider.name == "weighted_field_search"
        assert isinstance(provider, WeightedFieldSearch)

    def test_create_bm25_provider(self, fs):
        """BM25 provider should fall back to weighted search for now."""
        provider = create_search_provider(fs, "bm25")
        assert provider.name == "bm25_search"
        # Should still work (falls back to weighted)
        _make_page(fs, "test/fallback", "Redis", content="Redis content.")
        results = provider.search("Redis")
        assert len(results) >= 1

    def test_default_provider(self, fs):
        """Default should be weighted."""
        provider = create_search_provider(fs)
        assert provider.name == "weighted_field_search"


# ── Benchmark fixture ───────────────────────────────────────────

class TestSearchBenchmark:
    """
    Benchmark fixture for search quality regression.

    This test creates a controlled set of pages and verifies that
    search ranking is consistent across runs. If this test fails,
    it means search quality has regressed.
    """

    @pytest.fixture
    def benchmark_pages(self, fs):
        """Create a controlled set of pages for benchmarking."""
        pages = [
            ("redis/overview", "Redis Overview",
             "Redis is an in-memory data store.",
             ["redis", "database", "cache"],
             "Redis overview and basic concepts."),
            ("redis/configuration", "Redis Configuration",
             "# Redis Configuration\n\nSet maxmemory to 256mb.",
             ["redis", "config"],
             "How to configure Redis."),
            ("postgres/overview", "PostgreSQL Overview",
             "PostgreSQL is a relational database.",
             ["postgres", "database"],
             "PostgreSQL overview and features."),
            ("postgres/redis-integration", "PostgreSQL with Redis",
             "Use Redis as a cache layer for PostgreSQL.",
             ["postgres", "redis", "integration"],
             "Integrating PostgreSQL with Redis."),
            ("cache/strategies", "Caching Strategies",
             "Common caching patterns include write-through and write-behind.",
             ["cache", "patterns"],
             "Different caching strategies."),
        ]
        for slug, title, content, tags, synopsis in pages:
            _make_page(fs, slug, title, content, tags=tags, synopsis=synopsis)
        return pages

    def test_benchmark_redis_query(self, fs, search, benchmark_pages):
        """Query 'redis' should return Redis pages in top results."""
        results = search.search("redis")
        slugs = [r["slug"] for r in results]

        # Redis configuration should be first (title + heading match = higher score)
        assert slugs[0] == "redis/configuration"
        # Redis overview should be in top 3 (title match)
        assert "redis/overview" in slugs[:3]
        # PostgreSQL with Redis should appear (body/tag match)
        assert "postgres/redis-integration" in slugs

    def test_benchmark_database_query(self, fs, search, benchmark_pages):
        """Query 'database' should return database-related pages."""
        results = search.search("database")
        slugs = [r["slug"] for r in results]

        # Both Redis and PostgreSQL overviews should appear
        assert "redis/overview" in slugs
        assert "postgres/overview" in slugs

    def test_benchmark_configuration_query(self, fs, search, benchmark_pages):
        """Query 'configuration' should return config pages."""
        results = search.search("configuration")
        slugs = [r["slug"] for r in results]

        assert slugs[0] == "redis/configuration"

    def test_benchmark_multi_term_query(self, fs, search, benchmark_pages):
        """Query 'postgres redis' should return integration page high."""
        results = search.search("postgres redis")
        slugs = [r["slug"] for r in results]

        # Integration page should be in top results (has both terms)
        assert "postgres/redis-integration" in slugs[:3]

    def test_benchmark_consistency(self, fs, search, benchmark_pages):
        """
        Same query should return same results in same order.
        This ensures deterministic search behavior.
        """
        results1 = search.search("redis")
        results2 = search.search("redis")

        assert len(results1) == len(results2)
        for r1, r2 in zip(results1, results2, strict=True):
            assert r1["slug"] == r2["slug"]
            assert r1["score"] == r2["score"]
