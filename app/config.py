"""
config.py — Settings loaded from config/settings.yaml + env vars.
Single source of truth for all configuration.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("wiki")

# ── App Mode ─────────────────────────────────────────────────────

AppMode = Literal["personal_local", "personal_server", "multi_user"]


def setup_logging(level: str = "INFO") -> None:
    """Configure root wiki logger. Call once at startup."""
    root = logging.getLogger("wiki")
    if root.handlers:
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        return

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)


@dataclass
class LLMSettings:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o"
    temperature: float = 0.1
    timeout_seconds: int = 60
    context_window_tokens: int = 0  # 0 = auto-detect, fallback 128K
    max_completion_tokens: int = 16000


@dataclass
class LimitsSettings:
    agents_md_chars: int = 4_200
    skills_md_chars: int = 8_750
    index_l0_chars: int = 10_500
    index_l1_chars: int = 10_000
    entity_page_chars: int = 10_000
    concept_page_chars: int = 14_000
    log_md_chars: int = 3_500
    conflicts_md_chars: int = 35_000


@dataclass
class IngestSettings:
    two_step: bool = True
    max_pages_per_source: int = 10        # kept for backward compat, superseded by new limits
    auto_lint_after_ingest: bool = True
    conflict_continue_on_detect: bool = True
    # Large source ingest settings (Phase 2)
    large_source_threshold_chars: int = 25_000
    chunk_min_chars: int = 8_000
    chunk_target_chars: int = 16_000
    chunk_max_chars: int = 25_000
    chunk_overlap_chars: int = 750
    max_pages_per_batch: int = 10
    max_auto_write_pages: int = 150
    require_review_if_pages_gt: int = 150
    # Thresholds (extracted from hardcoded values)
    max_completion_tokens: int = 8000
    existing_content_limit: int = 2000
    link_candidates_limit: int = 15
    link_aliases_per_candidate: int = 3
    keyword_min_length: int = 5
    keyword_source_limit: int = 3000
    related_pages_limit: int = 10
    retry_temperature: float = 0.0
    default_confidence: float = 0.8
    conflict_context_limit: int = 600
    skill_extraction_limit: int = 800


@dataclass
class QuerySettings:
    context_budget_chars: int = 35_000
    max_wiki_pages_in_context: int = 6
    history_budget_chars: int = 10_000
    allow_code_execution: bool = False


@dataclass
class AuditSettings:
    confidence_warn_threshold: float = 0.4
    stale_days_threshold: int = 90
    run_llm_audit_default: bool = False


@dataclass
class AuthSettings:
    enabled: bool = False
    username: str = ""
    password: str = ""


@dataclass
class ControlSettings:
    db_url: str = "sqlite:///data/control.db"
    workspaces_root: str = "data/workspaces"


@dataclass
class MultiUserSettings:
    registration_enabled: bool = True
    default_daily_tokens: int = 30_000
    default_welcome_tokens: int = 200_000
    daily_reset_timezone: str = "UTC"
    admin_emails: list[str] = field(default_factory=list)
    reasoning_model_budget: int = 500_000  # reserved tokens for reasoning models


@dataclass
class Settings:
    language: str = "ru"
    llm: LLMSettings = field(default_factory=LLMSettings)
    limits: LimitsSettings = field(default_factory=LimitsSettings)
    ingest: IngestSettings = field(default_factory=IngestSettings)
    query: QuerySettings = field(default_factory=QuerySettings)
    audit: AuditSettings = field(default_factory=AuditSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    wiki_data_path: str = "./wiki-data"
    app_mode: AppMode = "personal_local"
    control: ControlSettings = field(default_factory=ControlSettings)
    multi_user: MultiUserSettings = field(default_factory=MultiUserSettings)

    @classmethod
    def load(cls, config_path: str = "config/settings.yaml") -> Settings:
        settings = cls()
        path = Path(config_path)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            _apply_dict(settings, data)

        # Env vars override yaml
        lang = os.environ.get("LANGUAGE", "")
        if lang:
            settings.language = lang
        api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            settings.llm.api_key = api_key
        base_url = os.environ.get("LLM_BASE_URL", "")
        if base_url:
            settings.llm.base_url = base_url
        model = os.environ.get("LLM_MODEL", "")
        if model:
            settings.llm.model = model
        wiki_path = os.environ.get("WIKI_DATA_PATH", "")
        if wiki_path:
            settings.wiki_data_path = wiki_path

        _apply_env_bool(os.environ.get("WIKI_AUTH_ENABLED"), lambda v: setattr(settings.auth, "enabled", v))
        _apply_env_str(os.environ.get("WIKI_AUTH_USERNAME"), lambda v: setattr(settings.auth, "username", v))
        _apply_env_str(os.environ.get("WIKI_AUTH_PASSWORD"), lambda v: setattr(settings.auth, "password", v))
        _apply_env_int(os.environ.get("WIKI_CHUNK_MIN_CHARS"), lambda v: setattr(settings.ingest, "chunk_min_chars", v))
        _apply_env_int(os.environ.get("WIKI_CHUNK_TARGET_CHARS"), lambda v: setattr(settings.ingest, "chunk_target_chars", v))
        _apply_env_int(os.environ.get("WIKI_CHUNK_MAX_CHARS"), lambda v: setattr(settings.ingest, "chunk_max_chars", v))
        _apply_env_int(
            os.environ.get("WIKI_LARGE_SOURCE_THRESHOLD_CHARS"),
            lambda v: setattr(settings.ingest, "large_source_threshold_chars", v),
        )

        # App mode and control plane env overrides
        app_mode = os.environ.get("APP_MODE", "")
        if app_mode in ("personal_local", "personal_server", "multi_user"):
            settings.app_mode = app_mode
        control_db = os.environ.get("CONTROL_DB_URL", "")
        if control_db:
            settings.control.db_url = control_db
        workspaces_root = os.environ.get("WIKI_WORKSPACES_ROOT", "")
        if workspaces_root:
            settings.control.workspaces_root = workspaces_root
        _apply_env_bool(
            os.environ.get("REGISTRATION_ENABLED"),
            lambda v: setattr(settings.multi_user, "registration_enabled", v),
        )
        _apply_env_int(
            os.environ.get("DEFAULT_DAILY_TOKENS"),
            lambda v: setattr(settings.multi_user, "default_daily_tokens", v),
        )
        _apply_env_int(
            os.environ.get("DEFAULT_WELCOME_TOKENS"),
            lambda v: setattr(settings.multi_user, "default_welcome_tokens", v),
        )
        daily_tz = os.environ.get("DAILY_RESET_TIMEZONE", "")
        if daily_tz:
            settings.multi_user.daily_reset_timezone = daily_tz
        admin_emails = os.environ.get("MULTI_USER_ADMIN_EMAILS", "")
        admin_email = os.environ.get("MULTI_USER_ADMIN_EMAIL", "")
        collected: list[str] = []
        if admin_emails:
            collected.extend(
                email.strip().lower()
                for email in admin_emails.split(",")
                if email.strip()
            )
        if admin_email:
            collected.append(admin_email.strip().lower())
        if collected:
            settings.multi_user.admin_emails = sorted(set(collected))

        return settings


def language_instruction(settings: Settings) -> str:
    """Return a binding language rule for LLM prompts based on settings.language."""
    lang = settings.language.lower()
    if lang == "ru":
        return (
            "LANGUAGE RULE (BINDING):\n"
            "- All user-facing JSON string fields MUST be written in Russian.\n"
            "- This includes description, suggested_options, analysis_notes, planned page titles, and tags.\n"
            "- Keep technical terms, product names, slugs, filenames, code, and acronyms in English.\n"
            "- Verbatim source/context quotes MUST remain in the original source language — do NOT translate them.\n"
        )
    elif lang == "en":
        return (
            "LANGUAGE RULE (BINDING):\n"
            "- All user-facing JSON string fields MUST be written in English.\n"
            "- Keep technical terms, product names, slugs, filenames, code, and acronisms in their original form.\n"
            "- Verbatim source/context quotes MUST remain in the original source language — do NOT translate them.\n"
        )
    else:
        return (
            f"LANGUAGE RULE (BINDING):\n"
            f"- All user-facing JSON string fields MUST be written in {settings.language}.\n"
            "- Keep technical terms, product names, slugs, filenames, code, and acronyms in English.\n"
            "- Verbatim source/context quotes MUST remain in the original source language — do NOT translate them.\n"
        )


def _apply_dict(settings: Settings, data: dict) -> None:
    """Recursively apply yaml dict to settings dataclass."""
    section_map = {
        "llm": settings.llm,
        "limits": settings.limits,
        "ingest": settings.ingest,
        "query": settings.query,
        "audit": settings.audit,
        "auth": settings.auth,
        "control": settings.control,
        "multi_user": settings.multi_user,
    }
    for key, value in data.items():
        if key == "language":
            settings.language = value
        elif key == "app" and isinstance(value, dict):
            mode = value.get("mode")
            if mode in ("personal_local", "personal_server", "multi_user"):
                settings.app_mode = mode
        elif key == "wiki_data_path":
            settings.wiki_data_path = value
        elif key == "app_mode":
            if value in ("personal_local", "personal_server", "multi_user"):
                settings.app_mode = value
        elif key in section_map and isinstance(value, dict):
            section = section_map[key]
            for k, v in value.items():
                if hasattr(section, k):
                    setattr(section, k, v)


def _apply_env_str(value: str | None, setter) -> None:
    if value:
        setter(value)


def _apply_env_int(value: str | None, setter) -> None:
    if not value:
        return
    try:
        setter(int(value))
    except ValueError:
        logger.warning("Ignoring invalid integer env value: %s", value)


def _apply_env_bool(value: str | None, setter) -> None:
    if value is None or value == "":
        return
    setter(value.lower() in {"1", "true", "yes", "on"})
