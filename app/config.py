"""
config.py — Settings loaded from config/settings.yaml + env vars.
Single source of truth for all configuration.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("wiki")


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


@dataclass
class LimitsSettings:
    agents_md_chars: int = 4_200
    skills_md_chars: int = 8_750
    index_l0_chars: int = 10_500
    index_l1_chars: int = 5_250
    entity_page_chars: int = 3_500
    concept_page_chars: int = 5_250
    log_md_chars: int = 3_500
    conflicts_md_chars: int = 35_000


@dataclass
class IngestSettings:
    two_step: bool = True
    max_pages_per_source: int = 10        # kept for backward compat, superseded by new limits
    auto_lint_after_ingest: bool = True
    conflict_continue_on_detect: bool = True
    # Large source ingest settings (Phase 2)
    large_source_threshold_chars: int = 100_000
    chunk_min_chars: int = 8_000
    chunk_target_chars: int = 16_000
    chunk_max_chars: int = 25_000
    max_pages_per_batch: int = 10
    max_auto_write_pages: int = 15
    require_review_if_pages_gt: int = 25


@dataclass
class QuerySettings:
    context_budget_chars: int = 21_000
    max_wiki_pages_in_context: int = 6
    history_budget_chars: int = 7_000
    allow_code_execution: bool = False


@dataclass
class AuditSettings:
    confidence_warn_threshold: float = 0.4
    stale_days_threshold: int = 90
    run_llm_audit_default: bool = False


@dataclass
class Settings:
    language: str = "ru"
    llm: LLMSettings = field(default_factory=LLMSettings)
    limits: LimitsSettings = field(default_factory=LimitsSettings)
    ingest: IngestSettings = field(default_factory=IngestSettings)
    query: QuerySettings = field(default_factory=QuerySettings)
    audit: AuditSettings = field(default_factory=AuditSettings)
    wiki_data_path: str = "./wiki-data"

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
    }
    for key, value in data.items():
        if key == "language":
            settings.language = value
        elif key == "wiki_data_path":
            settings.wiki_data_path = value
        elif key in section_map and isinstance(value, dict):
            section = section_map[key]
            for k, v in value.items():
                if hasattr(section, k):
                    setattr(section, k, v)