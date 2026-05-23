import pytest

from app.core.utils import (
    auto_link,
    extract_wikilinks,
    now_iso,
    parse_json_block,
    validate_raw_filename,
    validate_slug,
)


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


class TestValidateSlug:
    def test_valid_simple(self):
        validate_slug("myapp/redis")

    def test_valid_with_hyphens(self):
        validate_slug("myapp/redis-cache")

    def test_valid_with_underscores(self):
        validate_slug("myapp/my_page")

    def test_valid_deep(self):
        validate_slug("project/category/sub/page")

    def test_valid_numbers(self):
        validate_slug("myapp/v2")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="пустым"):
            validate_slug("")

    def test_leading_slash_raises(self):
        with pytest.raises(ValueError, match="начинаться"):
            validate_slug("/myapp/page")

    def test_trailing_slash_raises(self):
        with pytest.raises(ValueError, match="заканчиваться"):
            validate_slug("myapp/page/")

    def test_dotdot_raises(self):
        with pytest.raises(ValueError, match="\\.\\."):
            validate_slug("myapp/../etc")

    def test_backslash_raises(self):
        with pytest.raises(ValueError, match="косую"):
            validate_slug("myapp\\page")

    def test_uppercase_raises(self):
        with pytest.raises(ValueError, match="недопустимые символы"):
            validate_slug("MyApp/Page")

    def test_spaces_raises(self):
        with pytest.raises(ValueError, match="недопустимые символы"):
            validate_slug("my app/page")


class TestValidateRawFilename:
    def test_allows_double_dot_in_name_before_extension(self):
        validate_raw_filename("Методический документ от 12 апреля 2026 г..pdf")

    def test_rejects_path_separators(self):
        with pytest.raises(ValueError, match="разделители пути"):
            validate_raw_filename("../secret.pdf")


class TestAutoLink:
    def _candidate(self, slug, title, aliases=None):
        return {"slug": slug, "title": title, "project": "_general",
                "type": "entity", "tags": [], "synopsis": "",
                "aliases": aliases or [title, slug.split("/")[-1]]}

    def test_links_alias(self):
        content = "Redis is used for caching"
        candidates = [self._candidate("backend/redis", "Redis", ["Redis"])]
        result = auto_link(content, candidates)
        assert "[[backend/redis|Redis]]" in result

    def test_skips_existing_wikilink(self):
        content = "See [[backend/redis|Redis]] for details"
        candidates = [self._candidate("backend/redis", "Redis", ["Redis"])]
        result = auto_link(content, candidates)
        assert result == content  # unchanged

    def test_skips_code_block(self):
        content = "```\nRedis config\n```\nText about Redis"
        candidates = [self._candidate("backend/redis", "Redis", ["Redis"])]
        result = auto_link(content, candidates)
        # Should not link inside code block, but should link later mention
        assert "[[backend/redis|Redis]]" in result

    def test_caps_at_max(self):
        content = "Redis Redis Redis Redis Redis Redis Redis Redis"
        candidates = [self._candidate("backend/redis", "Redis", ["Redis"])]
        result = auto_link(content, candidates)
        count = result.count("[[backend/redis|Redis]]")
        assert count == 1  # one alias → at most one replacement

    def test_skips_short_alias(self):
        content = "App is running"
        candidates = [self._candidate("myapp", "App", ["App"])]
        result = auto_link(content, candidates)
        assert "[[myapp|App]]" not in result  # App is only 3 chars
