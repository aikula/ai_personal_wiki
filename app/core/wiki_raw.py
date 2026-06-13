"""wiki_raw.py — Raw source file operations for wiki-data/.

Extracted from wiki_fs.py. Each function takes 'fs' as first parameter
(duck typing, no direct WikiFS import).
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.utils import (
    validate_project_name,
    validate_raw_filename,
)
from app.core.wiki_source import update_source_manifest as _update_source_manifest

try:
    from markitdown import MarkItDown
    MARKITDOWN_AVAILABLE = True
except ImportError:
    MARKITDOWN_AVAILABLE = False

logger = logging.getLogger("wiki.raw")


def list_raw_files(fs, project: str | None = None) -> list[Path]:
    if project:
        validate_project_name(project)
        target = fs.raw_dir / project
        if not target.exists():
            return []
        return sorted(target.rglob("*.md"))
    return sorted(fs.raw_dir.rglob("*.md"))


def read_raw_file(fs, relative_path: str) -> str | None:
    if not relative_path:
        raise ValueError("relative_path не может быть пустым")
    path = fs._resolve_in_dir(fs.raw_dir, relative_path)
    if not path.exists():
        return None

    if path.suffix.lower() in {'.md', '.txt', '.py'}:
        return path.read_text(encoding="utf-8")

    if path.suffix.lower() in {'.pdf', '.docx', '.pptx'}:
        if not MARKITDOWN_AVAILABLE:
            logger.warning("markitdown not available, cannot read %s", path.suffix)
            return None
        try:
            converter = MarkItDown()
            result = converter.convert(str(path))
            text = getattr(result, "text_content", None) or str(result)
            return text if text.strip() else None
        except Exception as e:
            logger.error("Failed to convert %s with markitdown: %s", path, e)
            return None

    logger.warning("Unsupported file format: %s", path.suffix)
    return None


def save_raw_file(fs, project: str, filename: str, content: str) -> Path:
    validate_project_name(project)
    validate_raw_filename(filename)
    target_dir = fs.raw_dir / project
    target_dir.mkdir(parents=True, exist_ok=True)
    target = fs._resolve_in_dir(target_dir, filename)
    target.write_text(content, encoding="utf-8")
    logger.info("Raw file saved: path=%s/%s chars=%d", project, filename, len(content))
    _update_source_manifest(fs, f"{project}/{filename}", content)
    return target


def get_raw_project(fs, raw_path: Path) -> str:
    rel = raw_path.relative_to(fs.raw_dir)
    parts = rel.parts
    if len(parts) >= 2:
        return parts[0]
    return "_general"
