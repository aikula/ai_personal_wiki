"""
search_provider.py — Abstract search interface for wiki pages.

Allows swapping the search implementation (weighted field search, BM25, TF-IDF)
without changing QueryAgent tool contracts or prompts.

Current default: WeightedFieldSearch (title/tags/summary/headings/body scoring).
Future: BM25Search, HybridSearch can be added by implementing SearchProvider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.wiki_fs import WikiFS


class SearchProvider(ABC):
    """Abstract interface for wiki page search."""

    @abstractmethod
    def search(
        self,
        query: str,
        project: str | None = None,
        projects: list[str] | None = None,
        top_k: int = 20,
    ) -> list[dict]:
        """
        Search wiki pages.

        Returns list of {slug, title, project, excerpt, score, field_scores?}
        sorted by score descending.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of the search provider."""
        ...


class WeightedFieldSearch(SearchProvider):
    """
    Weighted field search: scores matches by which field they appear in.

    score =
      8 * matches_in_title +
      5 * matches_in_tags +
      4 * matches_in_summary +
      3 * matches_in_headings +
      1 * matches_in_body

    This is the default provider — no external dependencies.
    """

    def __init__(self, fs: WikiFS):
        self.fs = fs

    @property
    def name(self) -> str:
        return "weighted_field_search"

    def search(
        self,
        query: str,
        project: str | None = None,
        projects: list[str] | None = None,
        top_k: int = 20,
    ) -> list[dict]:
        return self.fs.search_pages_weighted(
            query=query,
            project=project,
            projects=projects,
            top_k=top_k,
        )


class BM25Search(SearchProvider):
    """
    BM25 search provider — placeholder for future implementation.

    To activate: install rank-bm25, implement _bm25_search method,
    and set as default in QueryAgent.

    The QueryAgent tool contract remains unchanged — only the provider swaps.
    """

    def __init__(self, fs: WikiFS):
        self.fs = fs
        self._index_built = False

    @property
    def name(self) -> str:
        return "bm25_search"

    def search(
        self,
        query: str,
        project: str | None = None,
        projects: list[str] | None = None,
        top_k: int = 20,
    ) -> list[dict]:
        # TODO: implement BM25 with rank-bm25 library
        # For now, fall back to weighted field search
        return self.fs.search_pages_weighted(
            query=query,
            project=project,
            projects=projects,
            top_k=top_k,
        )


def create_search_provider(
    fs: WikiFS,
    provider_type: str = "weighted",
) -> SearchProvider:
    """
    Factory function to create a search provider.

    provider_type: "weighted" | "bm25"
    """
    if provider_type == "bm25":
        return BM25Search(fs)
    return WeightedFieldSearch(fs)
