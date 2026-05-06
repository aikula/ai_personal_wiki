import pytest

from app.core.utils import extract_wikilinks, now_iso, parse_json_block


class TestParseJsonBlock:
    def test_bare_object(self):
        result = parse_json_block('{"key": "value"}')
        assert result == {"key": "value"}

    def test_fenced_json(self):
        result = parse_json_block('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_fenced_without_lang(self):
        result = parse_json_block('```\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_with_prose_around(self):
        text = "Here is the result:\n```json\n{\"a\": 1}\n```\nDone."
        result = parse_json_block(text)
        assert result == {"a": 1}

    def test_bare_array(self):
        result = parse_json_block('[1, 2, 3]')
        assert result == [1, 2, 3]

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_json_block("not json at all")


class TestExtractWikilinks:
    def test_simple_slug(self):
        result = extract_wikilinks("[[myapp/redis]]")
        assert result == ["myapp/redis"]

    def test_with_display_text(self):
        result = extract_wikilinks("[[myapp/redis|Redis Cache]]")
        assert result == ["myapp/redis"]

    def test_with_anchor(self):
        result = extract_wikilinks("[[myapp/redis#config]]")
        assert result == ["myapp/redis"]

    def test_multiple_unique(self):
        text = "[[a]] and [[b]] and [[a]] again"
        result = extract_wikilinks(text)
        assert result == ["a", "b"]

    def test_empty(self):
        assert extract_wikilinks("no links here") == []


class TestNowIso:
    def test_returns_string(self):
        result = now_iso()
        assert isinstance(result, str)
        assert "T" in result
