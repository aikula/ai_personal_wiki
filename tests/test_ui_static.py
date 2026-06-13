"""Tests for offline-capable UI static assets."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import app as fastapi_app


@pytest.fixture(autouse=True)
def test_settings(monkeypatch, tmp_path):
    from app.api.dependencies import get_settings
    from app.config import Settings
    s = Settings()
    s.wiki_data_path = str(tmp_path)
    monkeypatch.setattr("app.config.Settings.load", lambda *a: s)
    get_settings.cache_clear()
    return s


@pytest.fixture
async def client(test_settings):
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def test_index_html_no_cdn_references():
    with open("app/ui/index.html") as f:
        content = f.read()
    assert "unpkg" not in content
    assert "cdnjs" not in content


def test_vendor_assets_exist():
    from pathlib import Path
    base = Path("app/ui/vendor")
    for name in ("react.production.min.js", "react-dom.production.min.js", "babel.min.js"):
        p = base / name
        assert p.exists(), f"Missing vendor asset: {name}"
        assert p.stat().st_size > 0, f"Empty vendor asset: {name}"


@pytest.mark.asyncio
async def test_vendor_route_returns_200(client):
    resp = await client.get("/vendor/react.production.min.js")
    assert resp.status_code == 200
    assert len(resp.content) > 0


def test_upload_modal_accepts_docx_pdf_pptx():
    with open("app/ui/index.html") as f:
        content = f.read()
    for ext in (".docx", ".pdf", ".pptx"):
        assert ext in content, f"Missing extension {ext} in upload modal"
