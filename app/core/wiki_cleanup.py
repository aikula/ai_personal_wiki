"""wiki_cleanup.py — Orphan cleanup and conflict archive operations.

Extracted from wiki_fs.py and wiki_conflicts.py. Standalone functions
that take 'fs' as first parameter.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from app.core.wiki_conflicts import read_conflicts_raw as _read_conflicts_raw

logger = logging.getLogger("wiki.cleanup")


def cleanup_orphan_conflicts(fs, existing_raw_files: list[Path]) -> int:
    """Remove OPEN conflicts whose source_file no longer exists in raw/.

    RESOLVED conflicts are kept (their skills are already in skills.md).
    Returns number of removed conflicts.
    """
    existing: set[str] = set()
    for p in existing_raw_files:
        rel = p.relative_to(fs.raw_dir)
        existing.add(str(rel).replace("\\", "/"))
        existing.add(str(rel))

    content = _read_conflicts_raw(fs)
    parts = re.split(r"\n---\n", content)

    removed = 0
    kept = []
    for part in parts:
        part = part.strip()
        if not part:
            continue

        is_open = "## [OPEN]" in part
        if not is_open:
            kept.append(part)
            continue

        match = re.search(r"- \*\*Source file:\*\* (.+)", part)
        if match:
            source_file = match.group(1).strip().replace("\\", "/")
            if source_file in existing:
                kept.append(part)
            else:
                removed += 1
                logger.info(
                    "Removing orphan conflict for missing raw file: %s",
                    source_file,
                )
        else:
            kept.append(part)

    if removed > 0:
        new_content = "\n\n---\n\n".join(kept) + "\n"
        path = fs.root / "conflicts.md"
        path.write_text(new_content, encoding="utf-8")
        logger.info("Removed %d orphan conflicts", removed)

    return removed


def archive_resolved_conflicts(fs) -> None:
    """Archive resolved conflicts to monthly archive files.

    Moved resolved blocks out of conflicts.md into archive/conflicts_YYYY_MM.md.
    Keeps only the 5 most recent archive files.
    """
    archive_dir = fs.root / "archive"
    archive_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y_%m")
    archive_path = archive_dir / f"conflicts_{ts}.md"
    content = _read_conflicts_raw(fs)
    resolved = re.findall(r"## \[RESOLVED\].*?(?=## \[|$)", content, re.DOTALL)
    if resolved:
        archive_text = "# Archived Conflicts\n\n" + "\n---\n".join(resolved)
        if archive_path.exists():
            archive_path.write_text(
                archive_path.read_text(encoding="utf-8") + "\n" + archive_text,
                encoding="utf-8",
            )
        else:
            archive_path.write_text(archive_text, encoding="utf-8")
        # Remove resolved from active file
        open_only = re.findall(r"## \[OPEN\].*?(?=## \[|$)", content, re.DOTALL)
        new_content = "# Conflicts\n\n" + "\n---\n\n".join(open_only)
        (fs.root / "conflicts.md").write_text(new_content, encoding="utf-8")
        # Keep only 5 most recent conflict archives
        archives = sorted(archive_dir.glob("conflicts_*.md"), reverse=True)
        for old in archives[5:]:
            old.unlink()
            logger.info("Removed old conflict archive: %s", old.name)
