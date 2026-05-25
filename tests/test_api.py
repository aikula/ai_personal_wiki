from base64 import b64encode
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import (
    _check_auth_config,
    _check_llm_connection,
)
from app.api.main import (
    app as fastapi_app,
)
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
    assert data["mode"] == "personal_local"
    assert "llm" in data


@pytest.mark.asyncio
async def test_basic_auth_blocks_api_when_enabled(client, test_settings):
    test_settings.auth.enabled = True
    test_settings.auth.username = "admin"
    test_settings.auth.password = "secret"

    resp = await client.get("/api/wiki/tree")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Basic")


@pytest.mark.asyncio
async def test_basic_auth_accepts_valid_credentials(client, test_settings):
    test_settings.auth.enabled = True
    test_settings.auth.username = "admin"
    test_settings.auth.password = "secret"
    token = b64encode(b"admin:secret").decode("ascii")

    resp = await client.get("/api/wiki/tree", headers={"Authorization": f"Basic {token}"})
    assert resp.status_code == 200


def test_basic_auth_requires_credentials_when_enabled():
    settings = Settings()
    settings.auth.enabled = True
    with pytest.raises(RuntimeError):
        _check_auth_config(settings)


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
async def test_wiki_page_rejects_invalid_slug(client):
    resp = await client.get("/api/wiki/page/%2E%2E/conflicts")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_raw_page_accepts_access_token_query(client, test_settings, monkeypatch, tmp_path):
    from app.core.wiki_fs import WikiFS

    test_settings.app_mode = "multi_user"
    test_settings.wiki_data_path = str(tmp_path)

    fs = WikiFS(test_settings)
    fs.write_page(
        "_general/raw-test",
        meta={
            "title": "Raw Test",
            "project": "_general",
            "type": "entity",
            "tags": [],
            "confidence": 1.0,
            "sources": 1,
            "last_confirmed": "2026-05-25",
            "supersedes": None,
            "superseded_by": None,
            "created": "2026-05-25",
        },
        content="Raw content",
    )

    class FakeStore:
        def get_user_by_session_token(self, token):
            return SimpleNamespace(id="user-1") if token == "token-123" else None

        def get_default_workspace(self, user_id):
            return SimpleNamespace(id="ws-1", root_path=str(tmp_path))

    monkeypatch.setattr("app.api.dependencies.build_control_store", lambda settings: FakeStore())

    unauthorized = await client.get("/api/wiki/raw/_general/raw-test")
    assert unauthorized.status_code == 401

    resp = await client.get("/api/wiki/raw/_general/raw-test?access_token=token-123")
    assert resp.status_code == 200
    data = resp.json()
    assert data["raw"].startswith("---")


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
async def test_ingest_accepts_txt_file(client):
    resp = await client.post(
        "/api/ingest",
        data={"project": "_general"},
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    # .txt files are now accepted (though may fail later due to missing API key)
    # but the initial validation should pass
    assert resp.status_code != 400  # Should not be rejected by file type validation


@pytest.mark.asyncio
async def test_ingest_rejects_invalid_project_name(client):
    resp = await client.post(
        "/api/ingest",
        data={"project": "../escape"},
        files={"file": ("ok.md", b"# hello", "text/markdown")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_rebuild_requires_confirm(client):
    resp = await client.post("/api/ingest/rebuild", json={"confirm": False})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_settings_get(client):
    resp = await client.get("/api/admin/settings")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_llm_startup_check_warns_when_not_configured(monkeypatch):
    s = Settings()
    s.wiki_data_path = "/tmp"
    s.llm.api_key = ""
    monkeypatch.setattr("app.api.main.get_settings", lambda: s)

    result = await _check_llm_connection()

    assert result["connected"] is False
    assert "warning" in result


@pytest.mark.asyncio
async def test_metrics_endpoint(client):
    resp = await client.get("/api/wiki/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_pages" in data
    assert "orphan_count" in data


@pytest.mark.asyncio
async def test_audit_lint_returns_string_items(client, test_settings):
    from app.core.wiki_fs import WikiFS

    fs = WikiFS(test_settings)
    fs.write_page("lint/source", meta={
        "title": "Lint Source", "project": "_general", "type": "entity",
        "tags": [], "confidence": 1.0, "sources": 1,
        "last_confirmed": "2026-05-25", "supersedes": None,
        "superseded_by": None, "created": "2026-05-25",
    }, content="See [[missing/page]]")

    resp = await client.get("/api/audit/lint")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["by_kind"]["broken_wikilink"][0], str)
    assert "[ERROR] lint/source:1" in data["by_kind"]["broken_wikilink"][0]


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


@pytest.mark.asyncio
async def test_drafts_list_empty(client):
    resp = await client.get("/api/ingest/drafts")
    assert resp.status_code == 200
    data = resp.json()
    assert "drafts" in data


@pytest.mark.asyncio
async def test_draft_reject_nonexistent(client):
    resp = await client.post("/api/ingest/drafts/nonexistent/reject")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_draft_apply_nonexistent(client):
    resp = await client.post("/api/ingest/drafts/nonexistent/apply")
    assert resp.status_code in (400, 404)
