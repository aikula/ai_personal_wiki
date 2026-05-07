import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import app as fastapi_app
from app.config import Settings


@pytest.fixture(autouse=True)
def test_settings(monkeypatch, tmp_path):
    s = Settings()
    s.wiki_data_path = str(tmp_path)
    # Make Settings.load return our test settings (accepts any args for classmethod compatibility)
    monkeypatch.setattr("app.config.Settings.load", lambda *a: s)
    from app.api.dependencies import get_settings
    get_settings.cache_clear()
    return s


@pytest.fixture
async def client(test_settings):
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_wiki_tree_empty(client):
    resp = await client.get("/api/wiki/tree")
    assert resp.status_code == 200
    data = resp.json()
    assert "projects" in data
    assert "total_pages" in data


@pytest.mark.asyncio
async def test_wiki_page_missing_returns_404(client):
    resp = await client.get("/api/wiki/page/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_search_no_query_returns_422(client):
    resp = await client.get("/api/wiki/search")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_empty_result(client):
    resp = await client.get("/api/wiki/search?q=zzzznonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []


@pytest.mark.asyncio
async def test_ingest_reject_non_md(client):
    resp = await client.post(
        "/api/ingest",
        data={"project": "_general"},
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_rebuild_requires_confirm(client):
    resp = await client.post("/api/ingest/rebuild", json={"confirm": False})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_settings_get(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_metrics_endpoint(client):
    resp = await client.get("/api/wiki/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_pages" in data
    assert "orphan_count" in data


@pytest.mark.asyncio
async def test_page_with_synopsis(client, test_settings):
    from app.core.wiki_fs import WikiFS
    fs = WikiFS(test_settings)
    fs.write_page("synopsis/test", meta={
        "title": "Synopsis Page", "project": "_general", "type": "entity",
        "tags": [], "confidence": 1.0, "sources": 1,
        "last_confirmed": "2026-05-07", "supersedes": None,
        "superseded_by": None, "created": "2026-05-07",
        "synopsis": "A short summary",
    }, content="# Test")
    resp = await client.get("/api/wiki/page/synopsis/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["synopsis"] == "A short summary"
