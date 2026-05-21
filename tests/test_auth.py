"""Tests for auth routes."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import app as fastapi_app
from app.config import Settings
from app.core.control_store import SQLiteControlStore
from app.core.migrations.runner import run_migrations


@pytest.fixture(autouse=True)
def test_settings(monkeypatch, tmp_path):
    s = Settings()
    s.wiki_data_path = str(tmp_path)
    s.app_mode = "multi_user"
    s.control.db_url = f"sqlite:///{tmp_path / 'control.db'}"
    s.control.workspaces_root = str(tmp_path / "workspaces")
    s.multi_user.registration_enabled = True
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
async def test_register_creates_user(client):
    resp = await client.post("/api/auth/register", json={
        "email": "alice@example.com",
        "password": "securepassword123",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["email"] == "alice@example.com"
    assert len(data["token"]) > 0
    assert data["workspace"]["slug"] == "alice"


@pytest.mark.asyncio
async def test_register_duplicate_email(client):
    await client.post("/api/auth/register", json={
        "email": "dup@example.com",
        "password": "securepassword123",
    })
    resp = await client.post("/api/auth/register", json={
        "email": "dup@example.com",
        "password": "anotherpassword",
    })
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_register_short_password(client):
    resp = await client.post("/api/auth/register", json={
        "email": "short@example.com",
        "password": "short",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_success(client):
    await client.post("/api/auth/register", json={
        "email": "login@example.com",
        "password": "securepassword123",
    })
    resp = await client.post("/api/auth/login", json={
        "email": "login@example.com",
        "password": "securepassword123",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["email"] == "login@example.com"
    assert len(data["token"]) > 0


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await client.post("/api/auth/register", json={
        "email": "wrong@example.com",
        "password": "securepassword123",
    })
    resp = await client.post("/api/auth/login", json={
        "email": "wrong@example.com",
        "password": "wrongpassword",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(client):
    resp = await client.post("/api/auth/login", json={
        "email": "nobody@example.com",
        "password": "securepassword123",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_with_token(client):
    reg_resp = await client.post("/api/auth/register", json={
        "email": "me@example.com",
        "password": "securepassword123",
    })
    token = reg_resp.json()["token"]

    resp = await client.get("/api/auth/me", headers={
        "Authorization": f"Bearer {token}",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["email"] == "me@example.com"
    assert data["workspace"] is not None
    assert data["credits"] is not None
    assert data["credits"]["daily"]["limit"] == 30_000
    assert data["credits"]["welcome"]["limit"] == 200_000


@pytest.mark.asyncio
async def test_auth_me_without_token(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_invalid_token(client):
    resp = await client.get("/api/auth/me", headers={
        "Authorization": "Bearer invalid-token-here",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout(client):
    reg_resp = await client.post("/api/auth/register", json={
        "email": "logout@example.com",
        "password": "securepassword123",
    })
    token = reg_resp.json()["token"]

    resp = await client.post("/api/auth/logout", headers={
        "Authorization": f"Bearer {token}",
    })
    assert resp.status_code == 200

    # Token should be invalid after logout
    resp = await client.get("/api/auth/me", headers={
        "Authorization": f"Bearer {token}",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_registration_disabled(monkeypatch, tmp_path):
    s = Settings()
    s.wiki_data_path = str(tmp_path)
    s.app_mode = "multi_user"
    s.control.db_url = f"sqlite:///{tmp_path / 'control.db'}"
    s.control.workspaces_root = str(tmp_path / "workspaces")
    s.multi_user.registration_enabled = False
    monkeypatch.setattr("app.config.Settings.load", lambda *a: s)
    from app.api.dependencies import get_settings
    get_settings.cache_clear()

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/auth/register", json={
            "email": "test@example.com",
            "password": "securepassword123",
        })
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_usage_endpoint(client):
    reg_resp = await client.post("/api/auth/register", json={
        "email": "usage@example.com",
        "password": "securepassword123",
    })
    token = reg_resp.json()["token"]

    resp = await client.get("/api/usage/me", headers={
        "Authorization": f"Bearer {token}",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["daily"]["limit"] == 30_000
    assert data["daily"]["used"] == 0
    assert data["welcome"]["limit"] == 200_000
    assert data["recent_events"] == []


@pytest.mark.asyncio
async def test_workspace_isolation(monkeypatch, tmp_path):
    """Two users get separate workspace directories."""
    s = Settings()
    s.wiki_data_path = str(tmp_path)
    s.app_mode = "multi_user"
    s.control.db_url = f"sqlite:///{tmp_path / 'control.db'}"
    s.control.workspaces_root = str(tmp_path / "workspaces")
    s.multi_user.registration_enabled = True
    monkeypatch.setattr("app.config.Settings.load", lambda *a: s)
    from app.api.dependencies import get_settings
    get_settings.cache_clear()

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        reg_a = await ac.post("/api/auth/register", json={
            "email": "alice@example.com",
            "password": "securepassword123",
        })
        reg_b = await ac.post("/api/auth/register", json={
            "email": "bob@example.com",
            "password": "securepassword123",
        })

        user_a = reg_a.json()["user"]["id"]
        user_b = reg_b.json()["user"]["id"]
        assert user_a != user_b

        # Workspace directories are named after user_id
        ws_root = tmp_path / "workspaces"
        assert (ws_root / user_a).exists()
        assert (ws_root / user_b).exists()
        assert (ws_root / user_a) != (ws_root / user_b)

