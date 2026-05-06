import pytest

from app.core.token_budget import CharLimitError, ContextBudget, count_chars


class TestContextBudget:
    def test_count_chars(self):
        assert count_chars("hello") == 5

    def test_trim_within_limit(self):
        budget = ContextBudget()
        budget.wiki_context = 100
        result = budget.trim("short text", "wiki_context")
        assert result == "short text"

    def test_trim_exceeds_limit(self):
        budget = ContextBudget()
        budget.wiki_context = 50
        result = budget.trim("this is a longer text that exceeds the budget limit significantly and should be trimmed down", "wiki_context")
        assert "TRIMMED" in result

    def test_fit_wiki_pages_all_fit(self):
        budget = ContextBudget()
        budget.wiki_context = 1000
        pages = ["page one", "page two", "page three"]
        result = budget.fit_wiki_pages(pages)
        assert result == pages

    def test_fit_wiki_pages_partial(self):
        budget = ContextBudget()
        budget.wiki_context = 15
        pages = ["short", "also short", "this is way too long to fit"]
        result = budget.fit_wiki_pages(pages)
        assert len(result) >= 1
        assert len("".join(result)) <= 20  # some truncation allowed

    def test_check_fits(self):
        budget = ContextBudget()
        budget.wiki_context = 100
        assert budget.check("short", "wiki_context") is True

    def test_check_exceeds_no_raise(self):
        budget = ContextBudget()
        budget.wiki_context = 5
        assert budget.check("way too long", "wiki_context") is False

    def test_check_exceeds_raises(self):
        budget = ContextBudget()
        budget.wiki_context = 5
        with pytest.raises(CharLimitError):
            budget.check("way too long", "wiki_context", raise_on_exceed=True)

    def test_default_budgets(self):
        budget = ContextBudget()
        assert budget.agents_md == 4_200
        assert budget.skills_md == 8_750
        assert budget.wiki_context == 21_000
        assert budget.history == 7_000
