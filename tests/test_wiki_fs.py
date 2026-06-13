from datetime import date

import pytest

from app.config import Settings
from app.core.wiki_fs import (
    CharLimitExceededError,
    FrontmatterError,
    IngestLog,
    SlugConflictError,
    WikiFS,
)
from app.core.wiki_types import ConflictEntry


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.wiki_data_path = str(tmp_path)
    return s


@pytest.fixture
def fs(settings):
    return WikiFS(settings)


# ── WikiFS initialisation ──────────────────────────────────────

class TestWikiFSInit:
    def test_creates_wiki_dir(self, fs, settings):
        assert (fs.root / "wiki").exists()

    def test_creates_raw_general(self, fs, settings):
        assert (fs.root / "raw" / "_general").exists()

    def test_bootstraps_conflicts(self, fs):
        assert (fs.root / "conflicts.md").exists()

    def test_bootstraps_skills(self, fs):
        assert (fs.root / "skills.md").exists()

    def test_bootstraps_index(self, fs):
        assert (fs.root / "wiki" / "index.md").exists()

    def test_bootstraps_log(self, fs):
        assert (fs.root / "wiki" / "log.md").exists()


# ── Page write / read ──────────────────────────────────────────

class TestWriteReadPage:
    def _meta(self):
        return {
            "title": "Test Page",
            "project": "_general",
            "type": "entity",
            "tags": ["test"],
            "confidence": 0.9,
            "sources": 1,
            "last_confirmed": date.today().isoformat(),
            "supersedes": None,
            "superseded_by": None,
            "created": date.today().isoformat(),
        }

    def test_write_and_read(self, fs):
        page = fs.write_page("test/page", meta=self._meta(), content="# Hello")
        assert page is not None
        assert page.slug == "test/page"

        read = fs.read_page("test/page")
        assert read is not None
        assert read.title == "Test Page"
        assert read.content == "# Hello"

    def test_read_nonexistent_returns_none(self, fs):
        assert fs.read_page("nonexistent") is None

    def test_write_missing_frontmatter_raises(self, fs):
        bad_meta = {"title": "No Type"}
        with pytest.raises(FrontmatterError):
            fs.write_page("bad/page", meta=bad_meta, content="x")

    def test_write_no_overwrite_raises(self, fs):
        fs.write_page("dup/page", meta=self._meta(), content="v1")
        with pytest.raises(SlugConflictError):
            fs.write_page("dup/page", meta=self._meta(), content="v2", allow_overwrite=False)

    def test_delete_page(self, fs):
        fs.write_page("del/me", meta=self._meta(), content="x")
        assert fs.delete_page("del/me") is True
        assert fs.read_page("del/me") is None

    def test_delete_nonexistent(self, fs):
        assert fs.delete_page("no/such") is False


# ── Char limit ─────────────────────────────────────────────────

class TestCharLimit:
    def _meta(self):
        return {
            "title": "Big Page",
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

    def test_exceeds_entity_limit(self, fs, settings):
        limit = settings.limits.entity_page_chars
        big = "x" * (limit + 1)
        with pytest.raises(CharLimitExceededError):
            fs.write_page("big/page", meta=self._meta(), content=big)


# ── Raw files ──────────────────────────────────────────────────

class TestRawFiles:
    def test_save_and_read_raw(self, fs):
        fs.save_raw_file("myproj", "guide.md", "# Guide")
        files = fs.list_raw_files(project="myproj")
        assert len(files) == 1
        content = fs.read_raw_file("myproj/guide.md")
        assert "# Guide" in content

    def test_list_raw_general(self, fs):
        fs.save_raw_file("_general", "note.md", "# Note")
        files = fs.list_raw_files(project="_general")
        assert len(files) == 1


# ── Conflicts ──────────────────────────────────────────────────

class TestConflicts:
    def test_append_conflict(self, fs):
        entry = ConflictEntry(
            id="CONFLICT-001", status="OPEN", date="2026-05-01",
            project="myapp", source_file="raw/myapp/x.md",
            conflict_type="factual_contradiction",
            page_a_slug="myapp/foo", page_b_ref="raw/myapp/x.md:10",
            context_a="existing wiki", context_b="source says",
            suggested_options=["Trust wiki", "Trust source"],
        )
        fs.append_conflict(entry)
        raw = fs.read_conflicts_raw()
        assert "CONFLICT-001" in raw
        assert "[OPEN]" in raw

    def test_count_open_conflicts(self, fs):
        entry = ConflictEntry(
            id="CONFLICT-002", status="OPEN", date="2026-05-01",
            project="x", source_file="x.md",
            conflict_type="factual_contradiction",
            page_a_slug="a", page_b_ref="b",
            context_a="", context_b="",
            suggested_options=[],
        )
        fs.append_conflict(entry)
        assert fs.count_open_conflicts() >= 1


# ── Skills ─────────────────────────────────────────────────────

class TestSkills:
    def test_append_skill(self, fs):
        fs.append_skill("Source Trust Rules", "Primary docs override secondary")
        skills = fs.read_skills()
        assert "Primary docs override secondary" in skills

    def test_append_skill_creates_section(self, fs):
        fs.append_skill("Ingest Patterns", "New pattern")
        skills = fs.read_skills()
        assert "## Ingest Patterns" in skills


# ── Log ────────────────────────────────────────────────────────

class TestLog:
    def test_append_log(self, fs):
        entry = IngestLog(
            timestamp="2026-05-01T10:00:00",
            source_file="myapp/guide.md",
            project="myapp",
            pages_created=["myapp/foo"],
            pages_updated=[],
            conflicts_detected=[],
            skills_triggered=[],
            char_delta=500,
        )
        fs.append_log(entry)
        path = fs.root / "wiki" / "log.md"
        content = path.read_text()
        assert "myapp/guide.md" in content


# ── Wiki tree ──────────────────────────────────────────────────

class TestWikiTree:
    def test_empty_tree(self, fs):
        tree = fs.get_wiki_tree()
        assert tree["total_pages"] >= 1  # index.md exists

    def test_tree_with_pages(self, fs):
        meta = {
            "title": "Redis", "project": "myapp", "type": "entity",
            "tags": ["cache"], "confidence": 0.9, "sources": 1,
            "last_confirmed": date.today().isoformat(),
            "supersedes": None, "superseded_by": None,
            "created": date.today().isoformat(),
        }
        fs.write_page("myapp/redis", meta=meta, content="# Redis")
        tree = fs.get_wiki_tree()
        assert "myapp" in tree["projects"]


# ── Search ─────────────────────────────────────────────────────

class TestSearch:
    def test_search_finds_page(self, fs):
        meta = {
            "title": "Redis Cache", "project": "_general", "type": "entity",
            "tags": [], "confidence": 1.0, "sources": 1,
            "last_confirmed": date.today().isoformat(),
            "supersedes": None, "superseded_by": None,
            "created": date.today().isoformat(),
        }
        fs.write_page("search/redis", meta=meta, content="Redis is used for caching")
        results = fs.search_pages("redis caching")
        assert len(results) > 0
        assert results[0]["slug"] == "search/redis"


# ── Reset ──────────────────────────────────────────────────────

class TestReset:
    def test_full_reset(self, fs):
        meta = {
            "title": "Temp", "project": "_general", "type": "entity",
            "tags": [], "confidence": 1.0, "sources": 1,
            "last_confirmed": date.today().isoformat(),
            "supersedes": None, "superseded_by": None,
            "created": date.today().isoformat(),
        }
        fs.write_page("temp/page", meta=meta, content="x")
        fs.full_reset_wiki()
        assert fs.read_page("temp/page") is None
        assert (fs.root / "wiki" / "index.md").exists()


# ── Link candidates and graph metrics ─────────────────────────

class TestLinkCandidates:
    def _meta(self, **kw):
        base = {
            "title": "Test", "project": "_general", "type": "entity",
            "tags": ["test", "demo"], "confidence": 1.0, "sources": 1,
            "last_confirmed": date.today().isoformat(),
            "supersedes": None, "superseded_by": None,
            "created": date.today().isoformat(),
        }
        base.update(kw)
        return base

    def test_build_link_candidates(self, fs):
        fs.write_page("proj/page", meta=self._meta(title="My Page", project="proj"),
                       content="# Page")
        candidates = fs.build_link_candidates()
        slugs = [c["slug"] for c in candidates]
        assert "proj/page" in slugs
        match = next(c for c in candidates if c["slug"] == "proj/page")
        assert match["title"] == "My Page"
        assert "page" in match["aliases"]

    def test_build_link_candidates_excludes_index_log(self, fs):
        cand = fs.build_link_candidates()
        slugs = [c["slug"] for c in cand]
        assert "index" not in slugs
        assert "log" not in slugs


class TestGraphMetrics:
    def _meta(self, **kw):
        base = {
            "title": "P", "project": "_general", "type": "entity",
            "tags": [], "confidence": 1.0, "sources": 1,
            "last_confirmed": date.today().isoformat(),
            "supersedes": None, "superseded_by": None,
            "created": date.today().isoformat(),
        }
        base.update(kw)
        return base

    def test_basic_metrics(self, fs):
        metrics = fs.get_graph_metrics()
        assert "total_pages" in metrics
        assert "avg_outgoing_per_page" in metrics
        assert "orphan_count" in metrics

    def test_orphan_detected(self, fs):
        fs.write_page("orphan/page", meta=self._meta(), content="# Orphan")
        metrics = fs.get_graph_metrics()
        assert metrics["orphan_count"] >= 1
        assert "orphan/page" in metrics["orphan_slugs"]


# ── Source manifest ───────────────────────────────────────────

class TestSourceManifest:
    def test_new_source(self, fs):
        state = fs.check_source_state("proj/file.md", "# content")
        assert state["status"] == "new"

    def test_unchanged_after_save(self, fs):
        fs.save_raw_file("proj", "file.md", "# content")
        state = fs.check_source_state("proj/file.md", "# content")
        assert state["status"] == "unchanged"

    def test_changed_after_modify(self, fs):
        fs.save_raw_file("proj", "file.md", "# v1")
        state = fs.check_source_state("proj/file.md", "# v2")
        assert state["status"] == "changed"

    def test_duplicate_detected(self, fs):
        fs.save_raw_file("proj", "a.md", "# same")
        state = fs.check_source_state("proj/b.md", "# same")
        assert state["status"] == "duplicate"
        assert "a.md" in state["duplicate_of"]


# ── Page Outline ───────────────────────────────────────────────

class TestReadPageOutline:
    def _meta(self, **kw):
        base = {
            "title": "Test Page", "project": "_general", "type": "entity",
            "tags": ["test"], "confidence": 0.9, "sources": 1,
            "last_confirmed": date.today().isoformat(),
            "supersedes": None, "superseded_by": None,
            "created": date.today().isoformat(),
        }
        base.update(kw)
        return base

    def test_outline_basic(self, fs):
        content = (
            "# Introduction\n"
            "This is the intro paragraph.\n\n"
            "## Features\n"
            "- Feature A\n- Feature B\n\n"
            "## Configuration\n"
            "Config details here.\n"
        )
        fs.write_page("test/page", meta=self._meta(title="Test Page"), content=content)
        outline = fs.read_page_outline("test/page")

        assert outline is not None
        assert outline.slug == "test/page"
        assert outline.title == "Test Page"
        assert len(outline.headings) == 3
        assert outline.headings[0]["text"] == "Introduction"
        assert outline.headings[0]["level"] == 1
        assert outline.headings[1]["text"] == "Features"
        assert outline.headings[1]["level"] == 2

    def test_outline_with_synopsis(self, fs):
        meta = self._meta(synopsis="Custom synopsis from frontmatter")
        fs.write_page("test/syn", meta=meta, content="# Hello\n\nBody text.")
        outline = fs.read_page_outline("test/syn")
        assert outline.synopsis == "Custom synopsis from frontmatter"

    def test_outline_synopsis_from_content(self, fs):
        content = "# Title\n\nThis is the first paragraph that becomes synopsis.\n\n## Other"
        fs.write_page("test/auto-syn", meta=self._meta(), content=content)
        outline = fs.read_page_outline("test/auto-syn")
        assert "first paragraph" in outline.synopsis

    def test_outline_nonexistent_page(self, fs):
        assert fs.read_page_outline("nonexistent") is None

    def test_outline_headings_have_anchors(self, fs):
        content = "# My Heading\n\nText\n\n## Another Section\n\nMore text."
        fs.write_page("test/anchors", meta=self._meta(), content=content)
        outline = fs.read_page_outline("test/anchors")
        assert outline.headings[0]["anchor"] == "my-heading"
        assert outline.headings[1]["anchor"] == "another-section"

    def test_outline_headings_have_previews(self, fs):
        content = "# Intro\n\nPreview text here.\n\n## Next\n\nOther content."
        fs.write_page("test/previews", meta=self._meta(), content=content)
        outline = fs.read_page_outline("test/previews")
        assert "Preview text" in outline.headings[0]["preview"]


# ── Section Reading ────────────────────────────────────────────

class TestReadPageSection:
    def _meta(self, **kw):
        base = {
            "title": "Test", "project": "_general", "type": "entity",
            "tags": [], "confidence": 1.0, "sources": 1,
            "last_confirmed": date.today().isoformat(),
            "supersedes": None, "superseded_by": None,
            "created": date.today().isoformat(),
        }
        base.update(kw)
        return base

    def test_read_section_by_heading(self, fs):
        content = (
            "# Intro\n\nIntro text.\n\n"
            "## Redis\n\nRedis is used for caching.\n\n"
            "## PostgreSQL\n\nPostgreSQL is the main database.\n"
        )
        fs.write_page("test/db", meta=self._meta(), content=content)
        section = fs.read_page_section("test/db", "Redis")

        assert section is not None
        assert "Redis is used for caching" in section.content
        assert section.heading == "Redis"
        assert section.anchor == "redis"

    def test_read_section_by_anchor(self, fs):
        content = "# Intro\n\nText\n\n## My Section\n\nSection content here."
        fs.write_page("test/anchor", meta=self._meta(), content=content)
        section = fs.read_page_section("test/anchor", "my-section")
        assert section is not None
        assert "Section content" in section.content

    def test_read_section_nonexistent_heading(self, fs):
        fs.write_page("test/no", meta=self._meta(), content="# Hello\n\nWorld.")
        assert fs.read_page_section("test/no", "Nonexistent") is None

    def test_read_section_nonexistent_page(self, fs):
        assert fs.read_page_section("nonexistent", "Heading") is None

    def test_read_section_with_char_limit(self, fs):
        content = "# Intro\n\n" + "x" * 500
        fs.write_page("test/long", meta=self._meta(), content=content)
        section = fs.read_page_section("test/long", "Intro", char_limit=100)
        assert section.char_count <= 100
        assert "TRIMMED" in section.content

    def test_read_section_provenance_markers(self, fs):
        content = "## Data\n\nFact ^[raw/source.md] and another ^[raw/other.md]."
        fs.write_page("test/prov", meta=self._meta(), content=content)
        section = fs.read_page_section("test/prov", "Data")
        assert len(section.provenance_markers) == 2
        assert "raw/source.md" in section.provenance_markers

    def test_read_last_section(self, fs):
        content = "# Intro\n\nText\n\n## Last Section\n\nFinal content."
        fs.write_page("test/last", meta=self._meta(), content=content)
        section = fs.read_page_section("test/last", "Last Section")
        assert section is not None
        assert "Final content" in section.content

    def test_read_parent_section_includes_nested_subsections(self, fs):
        content = (
            "# Page\n\n"
            "## Deployment\n\nOverview.\n\n"
            "### Docker\n\nDocker details.\n\n"
            "### Env\n\nEnv details.\n\n"
            "## Other\n\nOther details."
        )
        fs.write_page("test/nested", meta=self._meta(), content=content)
        section = fs.read_page_section("test/nested", "Deployment")

        assert section is not None
        assert "Overview" in section.content
        assert "### Docker" in section.content
        assert "Docker details" in section.content
        assert "## Other" not in section.content


# ── Multi Read Sections ────────────────────────────────────────

class TestMultiReadSections:
    def _meta(self, **kw):
        base = {
            "title": "Test", "project": "_general", "type": "entity",
            "tags": [], "confidence": 1.0, "sources": 1,
            "last_confirmed": date.today().isoformat(),
            "supersedes": None, "superseded_by": None,
            "created": date.today().isoformat(),
        }
        base.update(kw)
        return base

    def test_batch_read(self, fs):
        content = "# Intro\n\nA\n\n## Section1\n\nB\n\n## Section2\n\nC"
        fs.write_page("test/multi", meta=self._meta(), content=content)

        requests = [
            {"slug": "test/multi", "heading": "Section1"},
            {"slug": "test/multi", "heading": "Section2"},
        ]
        results = fs.multi_read_sections(requests)

        assert len(results) == 2
        assert results[0] is not None
        assert "B" in results[0].content
        assert results[1] is not None
        assert "C" in results[1].content

    def test_batch_read_with_missing(self, fs):
        requests = [
            {"slug": "nonexistent", "heading": "X"},
        ]
        results = fs.multi_read_sections(requests)
        assert results[0] is None


# ── Weighted Search ────────────────────────────────────────────

class TestWeightedSearch:
    def _meta(self, **kw):
        base = {
            "title": "Test", "project": "_general", "type": "entity",
            "tags": [], "confidence": 1.0, "sources": 1,
            "last_confirmed": date.today().isoformat(),
            "supersedes": None, "superseded_by": None,
            "created": date.today().isoformat(),
        }
        base.update(kw)
        return base

    def test_title_match_ranks_higher(self, fs):
        fs.write_page("test/redis", meta=self._meta(title="Redis Cache"),
                       content="Some generic content.")
        fs.write_page("test/generic", meta=self._meta(title="Generic Page"),
                       content="Redis is mentioned in the body.")

        results = fs.search_pages_weighted("Redis")
        assert len(results) >= 2
        assert results[0]["slug"] == "test/redis"
        assert results[0]["score"] > results[1]["score"]

    def test_tag_match_scored(self, fs):
        fs.write_page("test/tagged", meta=self._meta(title="Page", tags=["redis", "cache"]),
                       content="Content without redis word.")
        results = fs.search_pages_weighted("redis")
        assert len(results) >= 1
        assert results[0]["slug"] == "test/tagged"

    def test_field_scores_included(self, fs):
        fs.write_page("test/fields", meta=self._meta(title="Redis"),
                       content="Redis content here.")
        results = fs.search_pages_weighted("Redis")
        assert len(results) >= 1
        assert "field_scores" in results[0]
        assert results[0]["field_scores"]["title"] >= 1

    def test_top_k_limit(self, fs):
        for i in range(25):
            fs.write_page(f"test/page{i}", meta=self._meta(title=f"Redis Page {i}"),
                           content=f"Content {i}.")
        results = fs.search_pages_weighted("Redis", top_k=5)
        assert len(results) == 5

    def test_project_filter(self, fs):
        fs.write_page("proja/page", meta=self._meta(title="Redis", project="proja"),
                       content="Content.")
        fs.write_page("projb/page", meta=self._meta(title="Redis", project="projb"),
                       content="Content.")
        results = fs.search_pages_weighted("Redis", project="proja")
        assert all(r["project"] == "proja" for r in results)

    def test_empty_query(self, fs):
        results = fs.search_pages_weighted("")
        assert results == []


# ── Source Cards ───────────────────────────────────────────────

class TestSourceCards:
    def test_compute_sha256(self, fs):
        sha = fs.compute_source_sha256("hello world")
        assert len(sha) == 64
        assert sha == fs.compute_source_sha256("hello world")

    def test_write_and_read_source_card(self, fs):
        from app.core.wiki_fs import SourceCard

        card = SourceCard(
            source_id="myproj/guide",
            source_path="raw/myproj/guide.md",
            source_sha256="abc123",
            title="Source: guide.md",
            project="myproj",
            ingest_status="active",
            created="2026-05-20",
            last_confirmed="2026-05-20",
            last_ingested="2026-05-20T10:00:00",
            outline=[{"text": "Intro", "level": 1, "char_count": 100}],
            chunk_count=1,
            chunks_processed=1,
            chunks_failed=0,
            pages_planned=["myproj/overview"],
            pages_written=["myproj/overview"],
            conflicts_opened=[],
            claims_files=[],
            drift_status="unknown",
        )
        fs.write_source_card(card)

        read = fs.read_source_card("myproj/guide")
        assert read is not None
        assert read.source_id == "myproj/guide"
        assert read.source_sha256 == "abc123"
        assert read.ingest_status == "active"
        assert len(read.outline) == 1

    def test_read_nonexistent_source_card(self, fs):
        assert fs.read_source_card("nonexistent") is None

    def test_list_source_cards(self, fs):
        from app.core.wiki_fs import SourceCard

        card1 = SourceCard(
            source_id="projA/doc1", source_path="raw/projA/doc1.md",
            source_sha256="sha1", title="Source: doc1.md", project="projA",
            ingest_status="active", created="2026-05-20",
            last_confirmed="2026-05-20", last_ingested="2026-05-20T10:00:00",
            outline=[], chunk_count=0, chunks_processed=0, chunks_failed=0,
            pages_planned=[], pages_written=[], conflicts_opened=[],
            claims_files=[], drift_status="unknown",
        )
        card2 = SourceCard(
            source_id="projB/doc2", source_path="raw/projB/doc2.md",
            source_sha256="sha2", title="Source: doc2.md", project="projB",
            ingest_status="active", created="2026-05-20",
            last_confirmed="2026-05-20", last_ingested="2026-05-20T10:00:00",
            outline=[], chunk_count=0, chunks_processed=0, chunks_failed=0,
            pages_planned=[], pages_written=[], conflicts_opened=[],
            claims_files=[], drift_status="unknown",
        )
        fs.write_source_card(card1)
        fs.write_source_card(card2)

        all_cards = fs.list_source_cards()
        assert len(all_cards) == 2

        proja_cards = fs.list_source_cards(project="projA")
        assert len(proja_cards) == 1
        assert proja_cards[0].project == "projA"

    def test_source_card_file_exists(self, fs):
        from app.core.wiki_fs import SourceCard

        card = SourceCard(
            source_id="myproj/test", source_path="raw/myproj/test.md",
            source_sha256="sha", title="Source: test.md", project="myproj",
            ingest_status="active", created="2026-05-20",
            last_confirmed="2026-05-20", last_ingested="2026-05-20T10:00:00",
            outline=[], chunk_count=0, chunks_processed=0, chunks_failed=0,
            pages_planned=[], pages_written=[], conflicts_opened=[],
            claims_files=[], drift_status="unknown",
        )
        fs.write_source_card(card)

        path = fs.wiki_dir / "_sources" / "myproj" / "test.md"
        assert path.exists()
        content = path.read_text()
        assert "source_sha256" in content
        assert "myproj/test" in content


# ── Source Drift ───────────────────────────────────────────────

class TestSourceDrift:
    def test_unchanged_source(self, fs):
        fs.save_raw_file("proj", "file.md", "# content")
        result = fs.check_source_drift("proj/file.md")
        assert result["status"] == "unchanged"
        assert result["old_sha256"] == result["new_sha256"]

    def test_changed_source(self, fs):
        fs.save_raw_file("proj", "file.md", "# v1")
        # Modify the raw file directly (bypassing save_raw_file to simulate drift)
        raw_path = fs.raw_dir / "proj" / "file.md"
        raw_path.write_text("# v2 modified", encoding="utf-8")
        result = fs.check_source_drift("proj/file.md")
        assert result["status"] == "changed"
        assert result["old_sha256"] != result["new_sha256"]

    def test_missing_source(self, fs):
        result = fs.check_source_drift("nonexistent/file.md")
        assert result["status"] == "missing_source"

    def test_no_card_for_existing_source(self, fs):
        # Save a raw file but don't create a card
        fs.save_raw_file("proj", "new.md", "# new")
        # The manifest entry exists from save_raw_file, but no card
        result = fs.check_source_drift("proj/new.md")
        # Should be unchanged since we just saved it
        assert result["status"] in ("unchanged", "no_card")

    def test_update_source_card_drift(self, fs):
        from app.core.wiki_fs import SourceCard

        card = SourceCard(
            source_id="proj/drift", source_path="raw/proj/drift.md",
            source_sha256="old_sha", title="Source: drift.md", project="proj",
            ingest_status="active", created="2026-05-20",
            last_confirmed="2026-05-20", last_ingested="2026-05-20T10:00:00",
            outline=[], chunk_count=0, chunks_processed=0, chunks_failed=0,
            pages_planned=[], pages_written=[], conflicts_opened=[],
            claims_files=[], drift_status="unknown",
        )
        fs.write_source_card(card)

        fs.update_source_card_drift("proj/drift", "changed")
        read = fs.read_source_card("proj/drift")
        assert read is not None
        assert read.drift_status == "changed"
        assert read.ingest_status == "changed"
