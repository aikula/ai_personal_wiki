"""Tests for /api/ingest/batch endpoint."""

from io import BytesIO
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


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    """Mock LLMClient to avoid real OpenAI calls."""
    mock_client = MagicMock()
    mock_client.call.return_value = '{}'
    monkeypatch.setattr("app.api.dependencies.build_base_llm_client", lambda settings: mock_client)


@pytest.fixture
async def client(test_settings, mock_llm):
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _fake_model(**kw):
    from app.api.models import IngestFileResponse
    base = dict(
        success=True, source_file="x", project="p",
        pages_created=[], pages_updated=[], pages_superseded=[],
        conflict_ids=[], skills_triggered=[],
        lint_errors=0, lint_warnings=0, analysis_notes="", error=None,
    )
    base.update(kw)
    return IngestFileResponse(**base)


@pytest.mark.asyncio
async def test_batch_empty_returns_400(client):
    resp = await client.post("/api/ingest/batch", data={"project": "testproj"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_batch_invalid_project_returns_400(client):
    resp = await client.post(
        "/api/ingest/batch",
        data={"project": "Bad Project!"},
        files={"files": ("t.md", BytesIO(b"# T"), "text/markdown")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_batch_unsupported_extension_in_skipped(client):
    resp = await client.post(
        "/api/ingest/batch",
        data={"project": "testproj"},
        files={"files": ("bad.exe", BytesIO(b"bin"), "application/octet-stream")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["processed"] == 0
    assert len(data["skipped_details"]) == 1
    assert "bad.exe" in data["skipped_details"][0]["file"]


@pytest.mark.asyncio
async def test_batch_accepts_files_key(client, monkeypatch):
    async def fake(project, sf, agent, fs):
        return _fake_model(source_file="testproj/test.md", project="testproj")
    monkeypatch.setattr("app.api.routes.ingest._save_and_ingest", fake)

    resp = await client.post(
        "/api/ingest/batch", data={"project": "testproj"},
        files={"files": ("test.md", BytesIO(b"# T"), "text/markdown")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["processed"] == 1


@pytest.mark.asyncio
async def test_batch_accepts_file_alias(client, monkeypatch):
    async def fake(project, sf, agent, fs):
        return _fake_model(source_file="testproj/test.md", project="testproj")
    monkeypatch.setattr("app.api.routes.ingest._save_and_ingest", fake)

    resp = await client.post(
        "/api/ingest/batch", data={"project": "testproj"},
        files={"file": ("test.md", BytesIO(b"# T"), "text/markdown")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["processed"] == 1


@pytest.mark.asyncio
async def test_batch_mixed_files_and_file_keys(client, monkeypatch):
    async def fake(project, sf, agent, fs):
        return _fake_model()
    monkeypatch.setattr("app.api.routes.ingest._save_and_ingest", fake)

    resp = await client.post(
        "/api/ingest/batch", data={"project": "testproj"},
        files=[
            ("files", ("a.md", BytesIO(b"# A"), "text/markdown")),
            ("file", ("b.md", BytesIO(b"# B"), "text/markdown")),
        ],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["processed"] == 2


@pytest.mark.asyncio
async def test_batch_response_has_all_fields(client, monkeypatch):
    async def fake(project, sf, agent, fs):
        return _fake_model()
    monkeypatch.setattr("app.api.routes.ingest._save_and_ingest", fake)

    resp = await client.post(
        "/api/ingest/batch", data={"project": "testproj"},
        files={"files": ("test.md", BytesIO(b"# T"), "text/markdown")},
    )
    assert resp.status_code == 200
    data = resp.json()
    for field in ("total", "processed", "skipped", "successes", "failures", "details", "skipped_details"):
        assert field in data


@pytest.mark.asyncio
async def test_batch_one_file_fails_others_processed(client, monkeypatch):
    async def fake_partial(project, source_file, agent, fs):
        if "bad" in source_file.filename:
            from fastapi import HTTPException
            raise HTTPException(400, "Unsupported")
        return _fake_model()
    monkeypatch.setattr("app.api.routes.ingest._save_and_ingest", fake_partial)

    resp = await client.post(
        "/api/ingest/batch", data={"project": "testproj"},
        files=[
            ("files", ("good.md", BytesIO(b"# Good"), "text/markdown")),
            ("files", ("bad.txt", BytesIO(b"bad"), "text/plain")),
        ],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["processed"] == 1
    assert data["skipped"] == 1
    assert data["successes"] == 1
    assert data["failures"] == 0
