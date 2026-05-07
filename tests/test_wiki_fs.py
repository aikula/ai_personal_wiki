from datetime import date

import pytest

from app.config import Settings
from app.core.wiki_fs import (
    CharLimitExceededError,
    ConflictEntry,
    FrontmatterError,
    IngestLog,
    SlugConflictError,
    WikiFS,
)


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
