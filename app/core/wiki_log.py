"""wiki_log.py — Skills and log operations for wiki-data/.

Extracted from wiki_fs.py. Each function takes 'fs' as first parameter
(duck typing, no direct WikiFS import).
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from app.core.wiki_types import CharLimitExceededError, IngestLog

logger = logging.getLogger("wiki.log")

_LOG_FRONTMATTER = """---
title: Change Log
project: _general
type: log
tags: []
confidence: 1.0
sources: 0
last_confirmed: {today}
supersedes: null
superseded_by: null
created: {today}
---

"""


def _render_log_entry(entry: IngestLog) -> str:
    pages_c = ", ".join(entry.pages_created) or "—"
    pages_u = ", ".join(entry.pages_updated) or "—"
    conflicts = ", ".join(entry.conflicts_detected) or "—"
    return (
        f"- **[{entry.timestamp}]** `ingest` "
        f"| project: `{entry.project}` "
        f"| file: `{entry.source_file}` "
        f"| created: {pages_c} "
        f"| updated: {pages_u} "
        f"| conflicts: {conflicts}\n"
    )


def read_skills(fs) -> str:
    path = fs.root / "skills.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def append_skill(fs, section: str, skill_text: str) -> None:
    path = fs.root / "skills.md"
    content = path.read_text(encoding="utf-8")

    entry = f"- {skill_text.strip()}\n"
    section_header = f"## {section}"

    if section_header in content:
        content = content.replace(
            section_header,
            f"{section_header}\n{entry}",
            1
        )
    else:
        content += f"\n{section_header}\n{entry}"

    if len(content) > fs.limits.skills_md_chars:
        raise CharLimitExceededError(path, len(content), fs.limits.skills_md_chars)

    path.write_text(content, encoding="utf-8")


def append_log(fs, entry: IngestLog) -> None:
    path = fs.wiki_dir / "log.md"
    current = path.read_text(encoding="utf-8")

    new_entry = _render_log_entry(entry)
    updated = current.rstrip() + "\n\n" + new_entry + "\n"

    if len(updated) > fs.limits.log_md_chars:
        rotate_log(fs, current)
        today = date.today().isoformat()
        updated = _LOG_FRONTMATTER.format(today=today) + new_entry + "\n"

    path.write_text(updated, encoding="utf-8")


def rotate_log(fs, content: str) -> None:
    archive_dir = fs.root / "archive"
    archive_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y_%m")
    archive_path = archive_dir / f"log_{ts}.md"
    if archive_path.exists():
        existing = archive_path.read_text(encoding="utf-8")
        archive_path.write_text(existing + "\n" + content, encoding="utf-8")
    else:
        archive_path.write_text(content, encoding="utf-8")
    archives = sorted(archive_dir.glob("log_*.md"), reverse=True)
    for old in archives[5:]:
        old.unlink()
        logger.info("Removed old log archive: %s", old.name)
