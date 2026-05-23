from app.config import Settings


def test_yaml_app_mode_section(tmp_path):
    config_path = tmp_path / "settings.yaml"
    config_path.write_text("app:\n  mode: multi_user\n", encoding="utf-8")

    settings = Settings.load(str(config_path))

    assert settings.app_mode == "multi_user"


def test_chunk_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_CHUNK_MIN_CHARS", "1000")
    monkeypatch.setenv("WIKI_CHUNK_TARGET_CHARS", "2000")
    monkeypatch.setenv("WIKI_CHUNK_MAX_CHARS", "3000")
    monkeypatch.setenv("WIKI_LARGE_SOURCE_THRESHOLD_CHARS", "4000")

    settings = Settings.load(str(tmp_path / "missing.yaml"))

    assert settings.ingest.chunk_min_chars == 1000
    assert settings.ingest.chunk_target_chars == 2000
    assert settings.ingest.chunk_max_chars == 3000
    assert settings.ingest.large_source_threshold_chars == 4000


def test_auth_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_AUTH_ENABLED", "true")
    monkeypatch.setenv("WIKI_AUTH_USERNAME", "admin")
    monkeypatch.setenv("WIKI_AUTH_PASSWORD", "secret")

    settings = Settings.load(str(tmp_path / "missing.yaml"))

    assert settings.auth.enabled is True
    assert settings.auth.username == "admin"
    assert settings.auth.password == "secret"


def test_multi_user_admin_emails_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MULTI_USER_ADMIN_EMAILS", "admin@example.com, root@example.com ")

    settings = Settings.load(str(tmp_path / "missing.yaml"))

    assert settings.multi_user.admin_emails == ["admin@example.com", "root@example.com"]
