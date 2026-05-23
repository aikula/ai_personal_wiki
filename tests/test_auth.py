"""Tests for auth routes."""

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import app as fastapi_app
from app.config import Settings


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
async def test_configured_admin_gets_admin_flag(client, test_settings):
    test_settings.multi_user.admin_emails = ["admin@example.com"]

    resp = await client.post("/api/auth/register", json={
        "email": "admin@example.com",
        "password": "securepassword123",
    })

    assert resp.status_code == 200
    assert resp.json()["user"]["is_admin"] is True


@pytest.mark.asyncio
async def test_multi_user_wiki_tree_requires_token(client):
    resp = await client.get("/api/wiki/tree")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_multi_user_ingest_requires_token(client):
    resp = await client.post(
        "/api/ingest",
        data={"project": "_general"},
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_non_admin_cannot_read_global_settings(client):
    reg_resp = await client.post("/api/auth/register", json={
        "email": "user@example.com",
        "password": "securepassword123",
    })
    token = reg_resp.json()["token"]

    resp = await client.get("/api/admin/settings", headers={
        "Authorization": f"Bearer {token}",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_cannot_read_settings_language(client):
    reg_resp = await client.post("/api/auth/register", json={
        "email": "user2@example.com",
        "password": "securepassword123",
    })
    token = reg_resp.json()["token"]

    resp = await client.get("/api/admin/settings/language", headers={
        "Authorization": f"Bearer {token}",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_cannot_test_global_settings(client):
    reg_resp = await client.post("/api/auth/register", json={
        "email": "user3@example.com",
        "password": "securepassword123",
    })
    token = reg_resp.json()["token"]

    resp = await client.get("/api/admin/settings/test", headers={
        "Authorization": f"Bearer {token}",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_manage_global_settings(client, test_settings):
    test_settings.multi_user.admin_emails = ["admin@example.com"]
    reg_resp = await client.post("/api/auth/register", json={
        "email": "admin@example.com",
        "password": "securepassword123",
    })
    token = reg_resp.json()["token"]

    get_resp = await client.get("/api/admin/settings", headers={
        "Authorization": f"Bearer {token}",
    })
    assert get_resp.status_code == 200

    language_resp = await client.get("/api/admin/settings/language", headers={
        "Authorization": f"Bearer {token}",
    })
    assert language_resp.status_code == 200

    post_resp = await client.post(
        "/api/admin/settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"llm_model": "gpt-4.1-mini"},
    )
    assert post_resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_settings_test_does_not_consume_user_quota(client, test_settings, monkeypatch):
    test_settings.multi_user.admin_emails = ["admin@example.com"]
    reg_resp = await client.post("/api/auth/register", json={
        "email": "admin@example.com",
        "password": "securepassword123",
    })
    token = reg_resp.json()["token"]

    mock_llm = MagicMock()
    mock_llm.model = "fake-model"
    mock_llm.call.return_value = '{"status":"ok"}'
    monkeypatch.setattr("app.api.routes.settings.build_base_llm_client", lambda settings: mock_llm)

    usage_before = await client.get("/api/usage/me", headers={
        "Authorization": f"Bearer {token}",
    })
    assert usage_before.status_code == 200
    before = usage_before.json()

    resp = await client.get("/api/admin/settings/test", headers={
        "Authorization": f"Bearer {token}",
    })
    assert resp.status_code == 200
    assert resp.json()["connected"] is True

    usage_after = await client.get("/api/usage/me", headers={
        "Authorization": f"Bearer {token}",
    })
    assert usage_after.status_code == 200
    after = usage_after.json()

    assert after["daily"]["used"] == before["daily"]["used"]
    assert after["welcome"]["used"] == before["welcome"]["used"]


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
