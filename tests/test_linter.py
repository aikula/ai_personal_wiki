from datetime import date, timedelta

import pytest

from app.config import Settings
from app.core.linter import WikiLinter
from app.core.wiki_fs import SourceCard, WikiFS


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.wiki_data_path = str(tmp_path)
    return s


@pytest.fixture
def fs(settings):
    return WikiFS(settings)


@pytest.fixture
def linter(fs, settings):
    return WikiLinter(fs, settings)


def _meta(**kw):
    base = {
        "title": "Test Page",
        "project": "_general",
        "type": "entity",
        "tags": [],
        "confidence": 1.0,
        "sources": 1,
        "last_confirmed": date.today().isoformat(),
        "supersedes": None,
        "superseded_by": None,
        "created": date.today().isoformat(),
    }
    base.update(kw)
    return base


class TestLintBrokenWikilink:
    def test_detects_broken_wikilink(self, linter, fs):
        fs.write_page("test/page", meta=_meta(),
                      content="See [[nonexistent/page]] for details")
        report = linter.lint(slugs=["test/page"])
        assert any(e.kind == "broken_wikilink" for e in report.errors)

    def test_valid_wikilink_no_error(self, linter, fs):
        fs.write_page("other/page", meta=_meta(title="Other"), content="# Other")
        fs.write_page("test/page", meta=_meta(),
                      content="See [[other/page]] for details")
        report = linter.lint(slugs=["test/page"])
        assert not any(e.kind == "broken_wikilink" for e in report.errors)


class TestLintOrphanPage:
    def test_detects_orphan(self, linter, fs):
        fs.write_page("orphan/page", meta=_meta(), content="# Orphan")
        report = linter.lint(slugs=["orphan/page"])
        assert any(e.kind == "orphan_page" for e in report.issues)

    def test_linked_no_orphan(self, linter, fs):
        fs.write_page("linked/page", meta=_meta(title="Linked"), content="# Linked")
        fs.write_page("referrer/page", meta=_meta(title="Referrer"),
                      content="See [[linked/page]]")
        report = linter.lint(slugs=["linked/page"])
        assert not any(e.kind == "orphan_page" for e in report.warnings)


class TestLintMissingFrontmatter:
    def test_missing_type_raises(self, linter, fs):
        bad_meta = {"title": "No Type"}
        from app.core.wiki_fs import FrontmatterError
        with pytest.raises(FrontmatterError):
            fs.write_page("bad/page", meta=bad_meta, content="x")

    def test_missing_title_raises(self, linter, fs):
        bad_meta = {"project": "_general", "type": "entity"}
        from app.core.wiki_fs import FrontmatterError
        with pytest.raises(FrontmatterError):
            fs.write_page("bad/page", meta=bad_meta, content="x")

    def test_linter_requires_all_frontmatter_fields(self, linter, fs):
        path = fs.wiki_dir / "manual" / "page.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "---\n"
            "title: Manual\n"
            "project: _general\n"
            "type: entity\n"
            "confidence: 1.0\n"
            "sources: 1\n"
            f"last_confirmed: {date.today().isoformat()}\n"
            f"created: {date.today().isoformat()}\n"
            "---\n"
            "# Manual\n",
            encoding="utf-8",
        )

        report = linter.lint(slugs=["manual/page"])
        details = "\n".join(e.detail for e in report.errors)
        assert "'tags'" in details
        assert "'supersedes'" in details
        assert "'superseded_by'" in details


class TestLintCharLimit:
    def test_exceeds_limit(self, linter, fs, settings):
        limit = settings.limits.entity_page_chars
        big = "x" * (limit + 1)
        from app.core.wiki_fs import CharLimitExceededError
        with pytest.raises(CharLimitExceededError):
            fs.write_page("big/page", meta=_meta(), content=big)


class TestLintStaleness:
    def test_stale_page_warned(self, linter, fs):
        old = (date.today() - timedelta(days=120)).isoformat()
        fs.write_page("stale/page", meta=_meta(last_confirmed=old, confidence=0.3),
                      content="# Old")
        report = linter.lint(slugs=["stale/page"])
        assert any(e.kind == "stale_page" for e in report.issues)


class TestLintDuplicateTitle:
    def test_duplicate_title_detected(self, linter, fs):
        fs.write_page("proj/a", meta=_meta(title="Duplicate", project="proj"),
                      content="# A")
        fs.write_page("proj/b", meta=_meta(title="Duplicate", project="proj"),
                      content="# B")
        report = linter.lint(slugs=["proj/a", "proj/b"])
        assert any(e.kind == "duplicate_title" for e in report.issues)


class TestLintMissingWikilink:
    def test_detects_missing_wikilink(self, linter, fs):
        fs.write_page("known/page", meta=_meta(title="Known Page", project="proj"),
                      content="# Known")
        fs.write_page("test/page", meta=_meta(title="Test", project="proj"),
                      content="The Known Page is important")
        report = linter.lint(slugs=["test/page"])
        assert any(e.kind == "missing_wikilink" for e in report.issues)

    def test_skips_when_already_linked(self, linter, fs):
        fs.write_page("known/page", meta=_meta(title="Known Page", project="proj"),
                      content="# Known")
        fs.write_page("test/page", meta=_meta(title="Test", project="proj"),
                      content="See [[known/page]] for details")
        report = linter.lint(slugs=["test/page"])
        assert not any(e.kind == "missing_wikilink" for e in report.issues)


class TestLintProvenance:
    def test_valid_provenance_no_error(self, linter, fs):
        fs.save_raw_file("proj", "source.md", "# Source")
        fs.write_page("test/page", meta=_meta(),
                      content="Fact ^[raw/proj/source.md]")
        report = linter.lint(slugs=["test/page"])
        assert not any(e.kind == "invalid_provenance" for e in report.issues)

    def test_invalid_provenance_detected(self, linter, fs):
        fs.write_page("test/page", meta=_meta(),
                      content="Fact ^[raw/nonexistent/source.md]")
        report = linter.lint(slugs=["test/page"])
        assert any(e.kind == "invalid_provenance" for e in report.issues)


class TestLintReadOnly:
    def test_source_drift_missing_source_does_not_update_source_card(self, linter, fs):
        fs.save_raw_file("proj", "source.md", "old")
        old_sha = fs.compute_source_sha256("old")
        fs.write_source_card(SourceCard(
            source_id="proj/source",
            source_path="raw/proj/source.md",
            source_sha256=old_sha,
            title="Source: source.md",
            project="proj",
            ingest_status="active",
            created=date.today().isoformat(),
            last_confirmed=date.today().isoformat(),
            last_ingested="2026-05-25T00:00:00",
            outline=[],
            chunk_count=1,
            chunks_processed=1,
            chunks_failed=0,
            pages_planned=[],
            pages_written=["proj/page"],
            conflicts_opened=[],
            claims_files=[],
            drift_status="unknown",
        ))
        before = (fs.wiki_dir / "_sources" / "proj" / "source.md").read_text(encoding="utf-8")
        (fs.raw_dir / "proj" / "source.md").unlink()

        report = linter.lint()
        after = (fs.wiki_dir / "_sources" / "proj" / "source.md").read_text(encoding="utf-8")

        assert any(e.kind == "missing_source" for e in report.issues)
        assert after == before
