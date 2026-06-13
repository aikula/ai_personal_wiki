"""Tests for conflict resolution draft flow (prepare/apply/reject).

Spec reference: docs/review_fixes_spec_2026-06-13.md P0-1.
Verifies that apply-update does NOT call LLM and uses the stored candidate
from prepare-update; reject keeps the page unchanged.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import app as fastapi_app


@pytest.fixture(autouse=True)
def test_settings(monkeypatch, tmp_path):
    from app.api.dependencies import get_settings
    from app.config import Settings

    s = Settings()
    s.wiki_data_path = str(tmp_path)
    s.llm.api_key = "sk-test-fake"
    monkeypatch.setattr("app.config.Settings.load", lambda *a: s)
    get_settings.cache_clear()
    return s


@pytest.fixture
def mock_llm(monkeypatch):
    """Mock LLM client; individual tests set return_value as needed."""
    mock_client = MagicMock()
    mock_client.call.return_value = "# Updated content\n\nNew body from LLM."
    monkeypatch.setattr(
        "app.api.dependencies.build_base_llm_client", lambda settings: mock_client
    )
    return mock_client


@pytest.fixture
async def client(test_settings):
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def seeded_wiki(test_settings):
    """Set up wiki page + resolved conflict for CONFLICT-001."""
    from app.core.wiki_fs import WikiFS

    fs = WikiFS(test_settings)
    fs.write_page(
        slug="myapp/test-page",
        meta={
            "title": "Test Page",
            "project": "myapp",
            "type": "concept",
            "tags": [],
            "confidence": 0.8,
            "sources": 1,
            "last_confirmed": "2026-06-13",
            "supersedes": None,
            "superseded_by": None,
            "created": "2026-06-13",
        },
        content="# Test Page\n\nOriginal body.",
    )
    conflicts_md = """# Conflicts

---

## [RESOLVED] CONFLICT-001

- **Date:** 2026-06-13
- **Project:** myapp
- **Source file:** raw/myapp/source.md
- **Conflict type:** factual_contradiction
- **Page A (wiki):** [[myapp/test-page]]
- **Page B (source):** raw/myapp/source.md
- **Context A (wiki excerpt):**

  > Original body.

- **Context B (source excerpt):**

  > Updated body from source.

- **Suggested options:**
  1. Trust source
  2. Trust wiki
- **User comment:** Source is primary
- **Resolution:** option_1 — trust source
- **Skill extracted:** Trust primary source for myapp
- **Resolved at:** 2026-06-13T12:00:00
"""
    (fs.root / "conflicts.md").write_text(conflicts_md, encoding="utf-8")
    return fs


@pytest.mark.asyncio
async def test_prepare_creates_new_md(client, mock_llm, seeded_wiki):
    """After prepare-update, draft dir contains new.md, diff.patch, meta.json."""
    resp = await client.post("/api/conflicts/CONFLICT-001/prepare-update")
    assert resp.status_code == 200
    data = resp.json()
    assert data["draft_id"] == "conflict-CONFLICT-001"
    assert data["affected_slug"] == "myapp/test-page"
    assert "diff" in data

    draft_dir = seeded_wiki.drafts_dir / "conflict-CONFLICT-001"
    assert (draft_dir / "new.md").exists()
    assert (draft_dir / "diff.patch").exists()
    assert (draft_dir / "meta.json").exists()
    assert (draft_dir / "existing.md").exists()


@pytest.mark.asyncio
async def test_apply_uses_stored_candidate(client, mock_llm, seeded_wiki):
    """apply-update must NOT call LLM — uses stored new.md from prepare."""
    await client.post("/api/conflicts/CONFLICT-001/prepare-update")
    assert mock_llm.call.call_count == 1

    resp = await client.post("/api/conflicts/CONFLICT-001/apply-update")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    assert mock_llm.call.call_count == 1, "apply must not invoke LLM"


@pytest.mark.asyncio
async def test_apply_preserves_frontmatter(client, mock_llm, seeded_wiki):
    """After apply, page frontmatter is preserved (title, project, created)."""
    await client.post("/api/conflicts/CONFLICT-001/prepare-update")
    await client.post("/api/conflicts/CONFLICT-001/apply-update")

    page = seeded_wiki.read_page("myapp/test-page")
    assert page is not None
    assert page.meta.get("title") == "Test Page"
    assert page.meta.get("project") == "myapp"
    assert page.meta.get("created") == "2026-06-13"
    assert "New body from LLM" in page.content


@pytest.mark.asyncio
async def test_apply_rebuilds_index(client, mock_llm, seeded_wiki):
    """After apply, index.md still has the page listed."""
    await client.post("/api/conflicts/CONFLICT-001/prepare-update")
    await client.post("/api/conflicts/CONFLICT-001/apply-update")

    index = (seeded_wiki.wiki_dir / "index.md").read_text(encoding="utf-8")
    assert "Pages:" in index
    assert "myapp" in index


@pytest.mark.asyncio
async def test_reject_keeps_page_unchanged(client, mock_llm, seeded_wiki):
    """After reject, page content is unchanged and draft is removed."""
    original = seeded_wiki.read_page("myapp/test-page").raw

    await client.post("/api/conflicts/CONFLICT-001/prepare-update")
    resp = await client.post("/api/conflicts/CONFLICT-001/reject-update")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    after = seeded_wiki.read_page("myapp/test-page").raw
    assert after == original
    draft_dir = seeded_wiki.drafts_dir / "conflict-CONFLICT-001"
    assert not draft_dir.exists()


@pytest.mark.asyncio
async def test_apply_without_prepare_returns_400(client, mock_llm, seeded_wiki):
    """apply-update without prior prepare-update returns 400."""
    resp = await client.post("/api/conflicts/CONFLICT-001/apply-update")
    assert resp.status_code in (400, 404)
