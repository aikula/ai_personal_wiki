"""Tests for MeteredLLMClient."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.api.dependencies import get_llm_client
from app.config import Settings
from app.core.context import WorkspaceContext
from app.core.control_store_noop import NoopControlStore
from app.core.control_store_sqlite import SQLiteControlStore
from app.core.metered_llm_client import MeteredLLMClient, QuotaExceededError
from app.core.migrations.runner import run_migrations


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.model = "gpt-4o"
    llm.call.return_value = "This is a test response from the LLM."
    llm.stream.return_value = iter(["Hello ", "world", "!"])
    return llm


@pytest.fixture
def personal_settings():
    s = Settings()
    s.app_mode = "personal_local"
    return s


@pytest.fixture
def multi_user_settings(tmp_path):
    s = Settings()
    s.app_mode = "multi_user"
    s.control.db_url = f"sqlite:///{tmp_path / 'control.db'}"
    s.control.workspaces_root = str(tmp_path / "workspaces")
    s.multi_user.default_daily_tokens = 10_000
    s.multi_user.default_welcome_tokens = 50_000
    run_migrations(tmp_path / "control.db")
    return s


# ── Personal mode tests ───────────────────────────────────────────

def test_personal_mode_passes_through(mock_llm, personal_settings):
    client = MeteredLLMClient(
        llm_client=mock_llm,
        settings=personal_settings,
        control_store=NoopControlStore(),
    )
    result = client.call(system="test", prompt="hello")
    assert result == "This is a test response from the LLM."
    mock_llm.call.assert_called_once()


def test_personal_mode_no_quota_check(mock_llm, personal_settings):
    """Personal mode should never check quota."""
    store = NoopControlStore()
    store.consume_tokens = MagicMock()

    client = MeteredLLMClient(
        llm_client=mock_llm, settings=personal_settings, control_store=store,
    )
    client.call(system="test", prompt="hello")
    store.consume_tokens.assert_not_called()


# ── Multi-user mode tests ─────────────────────────────────────────

def test_multi_user_consumes_tokens(mock_llm, multi_user_settings, tmp_path):
    store = SQLiteControlStore(tmp_path / "control.db")
    user = store.create_user("meter@example.com", "hash")
    ws = store.create_default_workspace(
        user_id=user.id, name="WS", slug="ws", root_path="/tmp/ws",
    )
    store.create_credit_buckets(user.id, daily_limit=10_000, welcome_limit=50_000)

    client = MeteredLLMClient(
        llm_client=mock_llm,
        settings=multi_user_settings,
        control_store=store,
        user_id=user.id,
        workspace_id=ws.id,
        operation="chat",
    )
    result = client.call(system="test", prompt="hello")
    assert result is not None

    state = store.get_credit_state(user.id)
    assert state.daily_used > 0


def test_multi_user_quota_exceeded(mock_llm, multi_user_settings, tmp_path):
    store = SQLiteControlStore(tmp_path / "control.db")
    user = store.create_user("quota@example.com", "hash")
    ws = store.create_default_workspace(
        user_id=user.id, name="WS", slug="ws", root_path="/tmp/ws",
    )
    # Very small bucket
    store.create_credit_buckets(user.id, daily_limit=10, welcome_limit=0)

    client = MeteredLLMClient(
        llm_client=mock_llm,
        settings=multi_user_settings,
        control_store=store,
        user_id=user.id,
        workspace_id=ws.id,
    )

    with pytest.raises(QuotaExceededError):
        client.call(system="long system prompt " * 100, prompt="long user prompt " * 100)

    # LLM should not have been called
    mock_llm.call.assert_not_called()


def test_multi_user_records_usage(mock_llm, multi_user_settings, tmp_path):
    store = SQLiteControlStore(tmp_path / "control.db")
    user = store.create_user("usage@example.com", "hash")
    ws = store.create_default_workspace(
        user_id=user.id, name="WS", slug="ws", root_path="/tmp/ws",
    )
    store.create_credit_buckets(user.id, daily_limit=10_000, welcome_limit=50_000)

    client = MeteredLLMClient(
        llm_client=mock_llm,
        settings=multi_user_settings,
        control_store=store,
        user_id=user.id,
        workspace_id=ws.id,
        operation="ingest",
    )
    client.call(system="test", prompt="hello")

    recent = store.get_recent_usage(user.id)
    assert len(recent) == 1
    assert recent[0]["operation"] == "ingest"
    assert recent[0]["model"] == "gpt-4o"
    assert recent[0]["total_tokens"] > 0


def test_multi_user_failed_call_no_consumption(multi_user_settings, tmp_path):
    store = SQLiteControlStore(tmp_path / "control.db")
    user = store.create_user("fail@example.com", "hash")
    ws = store.create_default_workspace(
        user_id=user.id, name="WS", slug="ws", root_path="/tmp/ws",
    )
    store.create_credit_buckets(user.id, daily_limit=10_000, welcome_limit=50_000)

    mock_llm = MagicMock()
    mock_llm.model = "gpt-4o"
    mock_llm.call.side_effect = RuntimeError("LLM failed")

    client = MeteredLLMClient(
        llm_client=mock_llm,
        settings=multi_user_settings,
        control_store=store,
        user_id=user.id,
        workspace_id=ws.id,
    )

    with pytest.raises(RuntimeError):
        client.call(system="test", prompt="hello")

    state = store.get_credit_state(user.id)
    assert state.daily_used == 0
    assert state.welcome_used == 0


def test_multi_user_refunds_estimation_surplus(multi_user_settings, tmp_path):
    store = SQLiteControlStore(tmp_path / "control.db")
    user = store.create_user("surplus@example.com", "hash")
    ws = store.create_default_workspace(
        user_id=user.id, name="WS", slug="ws", root_path="/tmp/ws",
    )
    store.create_credit_buckets(user.id, daily_limit=10_000, welcome_limit=50_000)

    mock_llm = MagicMock()
    mock_llm.model = "gpt-4o"
    mock_llm.call.return_value = "ok"

    client = MeteredLLMClient(
        llm_client=mock_llm,
        settings=multi_user_settings,
        control_store=store,
        user_id=user.id,
        workspace_id=ws.id,
    )
    client.call(system="system " * 20, prompt="prompt " * 20)

    state = store.get_credit_state(user.id)
    assert state.daily_used > 0
    assert state.daily_used < 100


def test_dependency_returns_metered_client_in_multi_user(multi_user_settings):
    multi_user_settings.llm.api_key = "test-key"
    ctx = WorkspaceContext(
        workspace_id="ws-1",
        owner_user_id="user-1",
        mode="multi_user",
        wiki_data_path=Path(multi_user_settings.wiki_data_path),
        quota_subject_id="user-1",
    )

    client = get_llm_client(multi_user_settings, ctx)

    assert isinstance(client, MeteredLLMClient)
