"""Browser-level smoke test for multi-user auth flow."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/api/health", timeout=1.5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"Server did not become healthy in time: {last_error}")


@pytest.fixture
def chrome_path() -> str:
    chrome = shutil.which("google-chrome")
    if not chrome:
        pytest.skip("google-chrome is required for browser auth smoke test")
    return chrome


@pytest.fixture
def multi_user_server(tmp_path: Path) -> Iterator[str]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    control_db = tmp_path / "control.db"
    workspaces = tmp_path / "workspaces"
    wiki_root = tmp_path / "wiki-data"

    env = os.environ.copy()
    env.update(
        {
            "LANGUAGE": "ru",
            "APP_MODE": "multi_user",
            "WIKI_DATA_PATH": str(wiki_root),
            "CONTROL_DB_URL": f"sqlite:///{control_db}",
            "WIKI_WORKSPACES_ROOT": str(workspaces),
            "MULTI_USER_ADMIN_EMAILS": "admin@example.com",
            "REGISTRATION_ENABLED": "true",
            "LLM_API_KEY": "",
        }
    )

    process = subprocess.Popen(
        [
            "python3",
            "-m",
            "uvicorn",
            "app.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_for_health(base_url)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(
    shutil.which("google-chrome") is None,
    reason="google-chrome is required for browser auth smoke test",
)
def test_multi_user_browser_requires_login_and_allows_register(
    multi_user_server: str,
    chrome_path: str,
) -> None:
    playwright = pytest.importorskip("playwright.sync_api")

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=chrome_path,
            headless=True,
            args=["--no-sandbox"],
        )
        context = browser.new_context()
        page = context.new_page()

        page.goto(multi_user_server, wait_until="networkidle")
        page.wait_for_timeout(1500)

        assert page.get_by_text("Войдите или создайте аккаунт").is_visible()
        assert not page.get_by_text("📂 Загрузить").is_visible()

        page.get_by_text("Регистрация").click()
        page.locator('input[type="email"]').fill("browser-user@example.com")
        page.locator('input[type="password"]').fill("securepassword123")
        page.get_by_role("button", name="Создать аккаунт").click()

        page.wait_for_timeout(1500)
        assert page.get_by_text("📂 Загрузить").is_visible()
        assert page.get_by_text("browser-user@example.com").is_visible()

        context.close()
        browser.close()
