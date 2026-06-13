"""Raw source file helpers for ingest/rebuild.

This module intentionally keeps binary source handling outside ``WikiFS`` for now.
``wiki_fs.py`` is already large and owns many unrelated responsibilities; this
small adapter gives ingest a binary-safe path without rewriting the whole
filesystem layer.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

try:
    from markitdown import MarkItDown
    MARKITDOWN_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional runtime install
    MarkItDown = None  # type: ignore[assignment]
    MARKITDOWN_AVAILABLE = False

from app.core.utils import validate_project_name, validate_raw_filename

logger = logging.getLogger("wiki.raw_sources")

RAW_ALLOWED_EXTENSIONS = {".md", ".txt", ".py", ".pdf", ".docx", ".pptx"}
TEXT_RAW_EXTENSIONS = {".md", ".txt", ".py"}
DOCUMENT_RAW_EXTENSIONS = {".pdf", ".docx", ".pptx"}


class RawSourceError(Exception):
    """Raised when a raw source exists but cannot be read or converted."""


def infer_project_from_raw_relative_path(raw_relative_path: str) -> str:
    """Infer project name from raw_relative_path BEFORE any I/O.

    Used in error branches where file conversion/read failed and
    ``get_raw_project`` is unavailable.

    >>> infer_project_from_raw_relative_path("eywa-demo/bad.pdf")
    'eywa-demo'
    >>> infer_project_from_raw_relative_path("bad.pdf")
    '_general'
    >>> infer_project_from_raw_relative_path("a/b/c.md")
    'a'
    """
    normalized = raw_relative_path.replace("\\", "/").strip("/")
    if "/" not in normalized:
        return "_general"
    return normalized.split("/", 1)[0] or "_general"


def _resolve_in_dir(base: Path, relative: str) -> Path:
    candidate = (base / relative).resolve()
    base_resolved = base.resolve()
    if base_resolved != candidate and base_resolved not in candidate.parents:
        raise ValueError(f"Path escapes raw directory: {relative}")
    return candidate


def _manifest_path(state_dir: Path) -> Path:
    return state_dir / "source_manifest.json"


def _read_manifest(state_dir: Path) -> dict:
    path = _manifest_path(state_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _write_manifest(state_dir: Path, manifest: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    _manifest_path(state_dir).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def check_source_state_bytes(state_dir: Path, relative_path: str, content: bytes) -> dict:
    """Return source state using binary-safe SHA256 hashing."""
    manifest = _read_manifest(state_dir)
    sha = sha256_bytes(content)

    if relative_path in manifest:
        entry = manifest[relative_path]
        if entry.get("sha256") == sha:
            return {"status": "unchanged", "sha256": sha, "duplicate_of": None}
        return {"status": "changed", "sha256": sha, "duplicate_of": None}

    for path, entry in manifest.items():
        if entry.get("sha256") == sha:
            return {"status": "duplicate", "sha256": sha, "duplicate_of": path}

    return {"status": "new", "sha256": sha, "duplicate_of": None}


def update_source_manifest_bytes(state_dir: Path, relative_path: str, content: bytes) -> None:
    """Record or update a source file in the manifest after ingest/save."""
    manifest = _read_manifest(state_dir)
    sha = sha256_bytes(content)
    now = datetime.now().isoformat(timespec="seconds")

    if relative_path in manifest:
        manifest[relative_path].update({
            "sha256": sha,
            "last_seen": now,
            "last_ingested": now,
            "status": "active",
            "size": len(content),
        })
    else:
        manifest[relative_path] = {
            "sha256": sha,
            "size": len(content),
            "first_seen": now,
            "last_seen": now,
            "last_ingested": now,
            "status": "active",
        }

    _write_manifest(state_dir, manifest)


def save_raw_file_bytes(raw_dir: Path, state_dir: Path, project: str, filename: str, content: bytes) -> Path:
    """Save raw source bytes under ``raw/<project>/<filename>``."""
    validate_project_name(project)
    validate_raw_filename(filename)

    suffix = Path(filename).suffix.lower()
    if suffix not in RAW_ALLOWED_EXTENSIONS:
        raise ValueError(
            "Неподдерживаемый тип файла. Допустимые расширения: "
            + ", ".join(sorted(RAW_ALLOWED_EXTENSIONS))
        )

    target_dir = raw_dir / project
    target_dir.mkdir(parents=True, exist_ok=True)
    target = _resolve_in_dir(target_dir, filename)
    target.write_bytes(content)
    update_source_manifest_bytes(state_dir, f"{project}/{filename}", content)
    logger.info("Raw file saved: path=%s/%s bytes=%d", project, filename, len(content))
    return target


def list_raw_source_files(raw_dir: Path, project: str | None = None) -> list[Path]:
    """List all supported raw source files, not just markdown."""
    if project:
        validate_project_name(project)
        target = raw_dir / project
        if not target.exists():
            return []
        root = target
    else:
        root = raw_dir

    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in RAW_ALLOWED_EXTENSIONS
    )


def read_raw_source_file(raw_dir: Path, relative_path: str) -> str | None:
    """Read/convert a raw source file to text for LLM ingest.

    Text formats are read as UTF-8. Document formats are converted with
    MarkItDown. Missing files return ``None``; conversion failures raise a clear
    ``RawSourceError`` so the caller does not misreport them as missing files.
    """
    if not relative_path:
        raise ValueError("relative_path не может быть пустым")

    path = _resolve_in_dir(raw_dir, relative_path)
    if not path.exists():
        return None

    suffix = path.suffix.lower()
    if suffix in TEXT_RAW_EXTENSIONS:
        return path.read_text(encoding="utf-8")

    if suffix in DOCUMENT_RAW_EXTENSIONS:
        if not MARKITDOWN_AVAILABLE:
            raise RawSourceError(
                "Document conversion dependency is not installed: markitdown. "
                "Install project dependencies again, e.g. `pip install -e .[dev]`."
            )
        try:
            converter = MarkItDown()
            result = converter.convert(str(path))
            text = getattr(result, "text_content", None) or str(result)
            if not text.strip():
                raise RawSourceError(f"Converted document is empty: {relative_path}")
            return text
        except RawSourceError:
            raise
        except Exception as exc:  # pragma: no cover - converter-specific
            raise RawSourceError(f"Failed to convert {relative_path}: {exc}") from exc

    raise RawSourceError(f"Unsupported raw source format: {suffix}")
