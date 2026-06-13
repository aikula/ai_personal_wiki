"""
wiki_conflicts.py — Conflict operations for WikiFS.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from app.core.wiki_types import ConflictEntry

logger = logging.getLogger("wiki.conflicts")


def _render_conflict_block(entry: ConflictEntry) -> str:
    options_text = "\n".join(
        f"  {i+1}. {opt}"
        for i, opt in enumerate(entry.suggested_options)
    )
    description_line = (
        f"- **Description:** {entry.description}\n"
        if entry.description else ""
    )
    cross_project_line = (
        "- **Cross-project:** true\n"
        if entry.is_cross_project else ""
    )
    return (
        f"## [{entry.status}] {entry.id}\n\n"
        f"- **Date:** {entry.date}\n"
        f"- **Project:** {entry.project}\n"
        f"- **Source file:** {entry.source_file}\n"
        f"- **Conflict type:** {entry.conflict_type}\n"
        f"{cross_project_line}"
        f"- **Page A (wiki):** [[{entry.page_a_slug}]]\n"
        f"- **Page B (source):** {entry.page_b_ref}\n"
        f"{description_line}"
        f"- **Context A (wiki excerpt):**\n\n"
        f"  > {entry.context_a.replace(chr(10), chr(10) + '  > ')}\n\n"
        f"- **Context B (source excerpt):**\n\n"
        f"  > {entry.context_b.replace(chr(10), chr(10) + '  > ')}\n\n"
        f"- **Suggested options:**\n{options_text}\n"
        f"- **User comment:** {entry.user_comment or '_none_'}\n"
        f"- **Resolution:** {entry.resolution}\n"
        f"- **Skill extracted:** {entry.skill_extracted}\n"
        f"- **Resolved at:** {entry.resolved_at}\n"
    )


def _inject_after_conflict_id(
    raw: str,
    conflict_id: str,
    text_to_inject: str,
) -> str:
    pattern = rf"(## \[(?:OPEN|RESOLVED)\] {re.escape(conflict_id)}[^\n]*\n)"
    replacement = rf"\1{text_to_inject}\n"
    updated = re.sub(pattern, replacement, raw, count=1)
    if updated == raw:
        updated = raw.rstrip() + f"\n\n{text_to_inject}\n"
    return updated


def read_conflicts_raw(fs) -> str:
    path = fs.root / "conflicts.md"
    return path.read_text(encoding="utf-8")


def append_conflict(fs, entry: ConflictEntry) -> None:
    """
    Append new conflict entry to conflicts.md.
    Auto-archives resolved conflicts if file exceeds limit.
    """
    path = fs.root / "conflicts.md"
    current = path.read_text(encoding="utf-8")

    new_block = _render_conflict_block(entry)
    updated = current.rstrip() + "\n\n---\n\n" + new_block + "\n"

    if len(updated) > fs.limits.conflicts_md_chars:
        fs._archive_resolved_conflicts()
        current = path.read_text(encoding="utf-8")
        updated = current.rstrip() + "\n\n---\n\n" + new_block + "\n"

    path.write_text(updated, encoding="utf-8")


def resolve_conflict(
    fs,
    conflict_id: str,
    resolution: str,
    user_comment: str,
    skill_extracted: str = "",
) -> bool:
    """
    Mark conflict as resolved. Updates status in conflicts.md.
    Returns False if conflict_id not found.
    """
    path = fs.root / "conflicts.md"
    content = path.read_text(encoding="utf-8")

    old = f"## [OPEN] {conflict_id}"
    new = f"## [RESOLVED] {conflict_id}"
    if old not in content:
        return False

    content = content.replace(old, new)

    ts = datetime.now().isoformat(timespec="seconds")
    resolution_block = (
        f"- **User comment:** {user_comment}\n"
        f"- **Resolution:** {resolution}\n"
        f"- **Skill extracted:** {skill_extracted}\n"
        f"- **Resolved at:** {ts}\n"
    )
    content = _inject_after_conflict_id(content, conflict_id, resolution_block)

    path.write_text(content, encoding="utf-8")
    fs.rebuild_index()
    return True


def prepare_conflict_resolution_draft(
    fs,
    conflict_id: str,
    resolution: str,
    user_comment: str = "",
) -> dict | None:
    """
    Create a draft update for the wiki page affected by a resolved conflict.
    Returns draft metadata or None if conflict/page not found.
    """
    conflicts_raw = read_conflicts_raw(fs)
    pattern = rf"## \[(?:OPEN|RESOLVED)\] {re.escape(conflict_id)}(.*?)(?=\n---\n## |\Z)"
    match = re.search(pattern, conflicts_raw, re.DOTALL)
    if not match:
        return None

    block = match.group(1)
    slug_match = re.search(r"Page A.*?\[\[([^\]]+)\]\]", block)
    if not slug_match:
        return None
    page_a_slug = slug_match.group(1)

    context_source_match = re.search(r"Context B.*?>\s*(.+?)(?=\n\n|\n-)", block, re.DOTALL)
    context_source = context_source_match.group(1).strip() if context_source_match else ""

    existing_page = fs.read_page(page_a_slug)
    if existing_page is None:
        return None

    draft_id = f"conflict-{conflict_id}"
    draft_dir = fs.drafts_dir / draft_id
    draft_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "conflict_id": conflict_id,
        "resolution": resolution,
        "user_comment": user_comment,
        "affected_slug": page_a_slug,
        "source_context": context_source,
        "existing_content": existing_page.raw,
    }
    (draft_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (draft_dir / "existing.md").write_text(existing_page.raw, encoding="utf-8")

    logger.info("Conflict resolution draft created: %s for %s", draft_id, page_a_slug)
    return {
        "draft_id": draft_id,
        "affected_slug": page_a_slug,
        "resolution": resolution,
        "source_context": context_source,
    }


def count_open_conflicts(fs) -> int:
    content = read_conflicts_raw(fs)
    return content.count("## [OPEN]")


def clear_open_conflicts(fs) -> int:
    """Remove all OPEN conflicts and keep RESOLVED history."""
    path = fs.root / "conflicts.md"
    content = read_conflicts_raw(fs)
    parts = re.split(r"\n---\n", content)

    removed = 0
    kept: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "## [OPEN]" in part:
            removed += 1
            continue
        kept.append(part)

    if removed == 0:
        return 0

    if not kept:
        new_content = "# Conflicts\n\n_No conflicts recorded yet._\n"
    else:
        new_content = "\n\n---\n\n".join(kept) + "\n"
    path.write_text(new_content, encoding="utf-8")
    fs.rebuild_index()
    logger.info("Cleared %d OPEN conflicts before rebuild", removed)
    return removed



