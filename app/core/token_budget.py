"""
token_budget.py — Character budget management for LLM context slots.

Uses chars (not tokens) for speed — 1 token ≈ 4 chars is sufficient heuristic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings


def count_chars(text: str) -> int:
    """Return character count. Simple len()."""
    return len(text)


class CharLimitError(Exception):
    """Raised when a single unit exceeds absolute hard limit."""
    pass


class ContextBudget:
    """
    Manages character budgets for different context slots.
    """

    agents_md: int = 4_200
    skills_md: int = 8_750
    index_l0: int = 10_500
    index_l1: int = 5_250
    entity_page: int = 3_500
    concept_page: int = 5_250
    log_md: int = 3_500
    conflicts_md: int = 35_000
    wiki_context: int = 21_000
    history: int = 7_000

    def __init__(self, settings: Settings | None = None):
        if settings:
            lim = settings.limits
            self.agents_md = lim.agents_md_chars
            self.skills_md = lim.skills_md_chars
            self.index_l0 = lim.index_l0_chars
            self.index_l1 = lim.index_l1_chars
            self.entity_page = lim.entity_page_chars
            self.concept_page = lim.concept_page_chars
            self.log_md = lim.log_md_chars
            self.conflicts_md = lim.conflicts_md_chars
            self.wiki_context = settings.query.context_budget_chars
            self.history = settings.query.history_budget_chars

    def trim(self, text: str, slot: str) -> str:
        """
        Trim text to slot budget.
        slot: "wiki_context" | "agents_md" | "skills_md" | ...
        Trims from the end, adds truncation marker.
        """
        limit: int = getattr(self, slot, self.wiki_context)
        if len(text) <= limit:
            return text
        return text[:limit - 40] + "\n\n… [TRIMMED — char limit reached]"

    def fit_wiki_pages(self, pages: list[str]) -> list[str]:
        """
        Fit as many wiki pages as possible into wiki_context budget.
        Returns subset of pages that fits, trimming last one if needed.
        Maintains page order (relevance ranking preserved).
        """
        result = []
        used = 0
        for page in pages:
            page_len = len(page)
            if used + page_len <= self.wiki_context:
                result.append(page)
                used += page_len
            else:
                remaining = self.wiki_context - used
                if remaining > 300:
                    result.append(page[:remaining] + "\n…[TRIMMED]")
                break
        return result

    def check(self, text: str, slot: str, raise_on_exceed: bool = False) -> bool:
        """
        Check if text fits slot budget.
        If raise_on_exceed=True → raises CharLimitError.
        Returns True if fits, False if exceeds.
        """
        limit: int = getattr(self, slot, self.wiki_context)
        fits = len(text) <= limit
        if not fits and raise_on_exceed:
            raise CharLimitError(
                f"Text ({len(text)} chars) exceeds {slot} limit ({limit} chars)"
            )
        return fits
