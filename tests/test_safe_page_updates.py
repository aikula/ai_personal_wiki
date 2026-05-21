"""
test_safe_page_updates.py — Tests for Phase 4: Safe Page Updates.
"""

from datetime import date

import pytest

from app.config import Settings
from app.core.safe_page_updates import (
    AddProvenanceMarker,
    AppendSection,
    PageWritePlan,
    ReplaceSection,
    UpdateFrontmatterField,
    apply_operations,
    generate_diff,
    validate_operation,
    validate_plan,
)
from app.core.wiki_fs import WikiFS, WikiPage


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.wiki_data_path = str(tmp_path)
    return s


@pytest.fixture
def fs(settings):
    return WikiFS(settings)


def _make_page(fs, slug, content, **meta_overrides):
    meta = {
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
    meta.update(meta_overrides)
    return fs.write_page(slug, meta=meta, content=content)


# ── ReplaceSection ──────────────────────────────────────────────

class TestReplaceSection:
    def test_replace_existing_section(self, fs):
        content = "# Intro\n\nOld intro.\n\n## Features\n\nOld features."
        page = _make_page(fs, "test/replace", content)

        plan = PageWritePlan(
            slug="test/replace",
            operations=[ReplaceSection(heading="Features", new_content="New features content.")],
        )
        meta, new_content = apply_operations(page, plan)

        assert "New features content." in new_content
        assert "Old intro." in new_content  # preserved
        assert "Old features" not in new_content

    def test_replace_preserves_provenance(self, fs):
        content = "# Intro\n\n## Data\n\nFact ^[raw/source.md]"
        page = _make_page(fs, "test/prov", content)

        plan = PageWritePlan(
            slug="test/prov",
            operations=[ReplaceSection(heading="Data", new_content="New fact.")],
        )
        meta, new_content = apply_operations(page, plan)

        assert "^[raw/source.md]" in new_content

    def test_replace_heading_not_found(self, fs):
        content = "# Intro\n\nText."
        page = _make_page(fs, "test/nofind", content)

        plan = PageWritePlan(
            slug="test/nofind",
            operations=[ReplaceSection(heading="Nonexistent", new_content="New")],
        )
        with pytest.raises(ValueError, match="Heading not found"):
            apply_operations(page, plan)

    def test_replace_nested_heading(self, fs):
        content = "# Main\n\n## Sub\n\nOld sub.\n\n## Other\n\nOther content."
        page = _make_page(fs, "test/nested", content)

        plan = PageWritePlan(
            slug="test/nested",
            operations=[ReplaceSection(heading="Sub", new_content="New sub content.")],
        )
        meta, new_content = apply_operations(page, plan)

        assert "New sub content." in new_content
        assert "Other content." in new_content  # preserved


# ── AppendSection ───────────────────────────────────────────────

class TestAppendSection:
    def test_append_to_existing_section(self, fs):
        content = "# Intro\n\nText.\n\n## Features\n\nFeature A."
        page = _make_page(fs, "test/append", content)

        plan = PageWritePlan(
            slug="test/append",
            operations=[AppendSection(heading="Features", content="Feature B.")],
        )
        meta, new_content = apply_operations(page, plan)

        assert "Feature A." in new_content
        assert "Feature B." in new_content

    def test_append_creates_new_section(self, fs):
        content = "# Intro\n\nText."
        page = _make_page(fs, "test/newsection", content)

        plan = PageWritePlan(
            slug="test/newsection",
            operations=[AppendSection(heading="New Section", content="New content.")],
        )
        meta, new_content = apply_operations(page, plan)

        assert "## New Section" in new_content
        assert "New content." in new_content

    def test_append_as_subsection(self, fs):
        content = "# Main\n\nMain text."
        page = _make_page(fs, "test/subsec", content)

        plan = PageWritePlan(
            slug="test/subsec",
            operations=[AppendSection(heading="Sub", content="Sub text.", as_subsection=True)],
        )
        meta, new_content = apply_operations(page, plan)

        assert "## Sub" in new_content
        assert "Sub text." in new_content


# ── UpdateFrontmatterField ──────────────────────────────────────

class TestUpdateFrontmatterField:
    def test_update_existing_field(self, fs):
        page = _make_page(fs, "test/fm", "# Content", confidence=0.5)

        plan = PageWritePlan(
            slug="test/fm",
            operations=[UpdateFrontmatterField(field_name="confidence", field_value=0.95)],
        )
        meta, content = apply_operations(page, plan)

        assert meta["confidence"] == 0.95

    def test_add_new_field(self, fs):
        page = _make_page(fs, "test/fm2", "# Content")

        plan = PageWritePlan(
            slug="test/fm2",
            operations=[UpdateFrontmatterField(field_name="synopsis", field_value="A test page.")],
        )
        meta, content = apply_operations(page, plan)

        assert meta["synopsis"] == "A test page."

    def test_remove_field(self, fs):
        page = _make_page(fs, "test/fm3", "# Content", tags=["test", "temp"])

        plan = PageWritePlan(
            slug="test/fm3",
            operations=[UpdateFrontmatterField(field_name="tags", field_value=None)],
        )
        meta, content = apply_operations(page, plan)

        assert "tags" not in meta


# ── AddProvenanceMarker ─────────────────────────────────────────

class TestAddProvenanceMarker:
    def test_add_after_text(self, fs):
        content = "# Intro\n\nRedis is used for caching."
        page = _make_page(fs, "test/prov2", content)

        plan = PageWritePlan(
            slug="test/prov2",
            operations=[AddProvenanceMarker(
                after_text="Redis is used for caching.",
                source_ref="raw/myapp/deploy.md",
            )],
        )
        meta, new_content = apply_operations(page, plan)

        assert "^[raw/myapp/deploy.md]" in new_content
        assert "Redis is used for caching. ^[raw/myapp/deploy.md]" in new_content

    def test_add_when_text_not_found(self, fs):
        content = "# Intro\n\nSome text."
        page = _make_page(fs, "test/prov3", content)

        plan = PageWritePlan(
            slug="test/prov3",
            operations=[AddProvenanceMarker(
                after_text="Not found text",
                source_ref="raw/myapp/deploy.md",
            )],
        )
        meta, new_content = apply_operations(page, plan)

        assert "^[raw/myapp/deploy.md]" in new_content


# ── Diff generation ─────────────────────────────────────────────

class TestDiffGeneration:
    def test_generate_diff(self, fs):
        page = _make_page(fs, "test/diff", "# Intro\n\nOld content here.")

        plan = PageWritePlan(
            slug="test/diff",
            operations=[ReplaceSection(heading="Intro", new_content="Much longer new content.")],
        )
        diff = generate_diff(page, plan)

        assert diff.slug == "test/diff"
        assert diff.char_delta > 0
        assert diff.frontmatter_preserved
        assert len(diff.diff_lines) > 0

    def test_empty_plan_diff(self, fs):
        page = _make_page(fs, "test/empty", "# Content")

        plan = PageWritePlan(slug="test/empty")
        diff = generate_diff(page, plan)

        assert diff.char_delta == 0
        assert not diff.requires_review
        assert diff.diff_lines == []

    def test_review_required_for_large_change(self, fs):
        page = _make_page(fs, "test/large", "# Intro\n\nShort.")

        plan = PageWritePlan(
            slug="test/large",
            operations=[ReplaceSection(heading="Intro", new_content="x" * 3000)],
        )
        diff = generate_diff(page, plan, review_threshold_chars=1000)

        assert diff.requires_review
        assert diff.char_delta > 1000

    def test_review_for_many_operations(self, fs):
        page = _make_page(fs, "test/many", "# A\n\n## B\n\n## C\n\n## D\n\n## E\n\n## F")

        plan = PageWritePlan(
            slug="test/many",
            operations=[
                UpdateFrontmatterField("tags", ["t1"]),
                UpdateFrontmatterField("tags", ["t2"]),
                UpdateFrontmatterField("tags", ["t3"]),
                UpdateFrontmatterField("tags", ["t4"]),
                UpdateFrontmatterField("tags", ["t5"]),
                UpdateFrontmatterField("tags", ["t6"]),
            ],
        )
        diff = generate_diff(page, plan)

        assert diff.requires_review
        assert "Large number of operations" in diff.review_reason


# ── Validation ──────────────────────────────────────────────────

class TestValidation:
    def test_valid_replace_section(self):
        op = ReplaceSection(heading="Features", new_content="New content.")
        assert validate_operation(op) == []

    def test_empty_heading_invalid(self):
        op = ReplaceSection(heading="", new_content="New")
        errors = validate_operation(op)
        assert len(errors) == 1
        assert "heading cannot be empty" in errors[0]

    def test_empty_content_invalid(self):
        op = ReplaceSection(heading="Features", new_content="")
        errors = validate_operation(op)
        assert len(errors) == 1

    def test_valid_provenance_marker(self):
        op = AddProvenanceMarker(after_text="text", source_ref="raw/source.md")
        assert validate_operation(op) == []

    def test_invalid_provenance_source(self):
        op = AddProvenanceMarker(after_text="text", source_ref="not_raw/source.md")
        errors = validate_operation(op)
        assert len(errors) == 1
        assert "must start with 'raw/'" in errors[0]

    def test_validate_plan_multiple_errors(self):
        plan = PageWritePlan(
            slug="test/plan",
            operations=[
                ReplaceSection(heading="", new_content=""),
                AddProvenanceMarker(after_text="x", source_ref="bad/source.md"),
            ],
        )
        errors = validate_plan(plan)
        assert len(errors) >= 2


# ── WikiFS integration ──────────────────────────────────────────

class TestWikiFSSafeUpdate:
    def test_apply_safe_update(self, fs):
        page = _make_page(fs, "test/safe", "# Intro\n\nOld text here.")

        plan = PageWritePlan(
            slug="test/safe",
            operations=[ReplaceSection(heading="Intro", new_content="Much longer new text.")],
        )
        updated, diff = fs.apply_safe_update("test/safe", plan, force=True)

        assert updated is not None
        assert "Much longer new text." in updated.content
        assert diff.char_delta > 0

    def test_apply_safe_update_page_not_found(self, fs):
        plan = PageWritePlan(slug="nonexistent/page")
        with pytest.raises(Exception):
            fs.apply_safe_update("nonexistent/page", plan)

    def test_generate_update_diff(self, fs):
        page = _make_page(fs, "test/gendiff", "# Intro\n\nText.")

        plan = PageWritePlan(
            slug="test/gendiff",
            operations=[ReplaceSection(heading="Intro", new_content="Changed.")],
        )
        diff = fs.generate_update_diff("test/gendiff", plan)

        assert diff is not None
        assert diff.slug == "test/gendiff"
        assert len(diff.diff_lines) > 0

    def test_generate_diff_nonexistent_page(self, fs):
        plan = PageWritePlan(slug="nonexistent")
        diff = fs.generate_update_diff("nonexistent", plan)
        assert diff is None
