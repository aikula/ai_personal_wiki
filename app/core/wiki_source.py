"""
wiki_source.py — Source card/manifest operations for wiki-data/.

Extracted from wiki_fs.py. Each function takes 'fs' as first parameter
(duck typing, no direct WikiFS import).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from pathlib import Path

import frontmatter

from app.core.wiki_types import SourceCard

logger = logging.getLogger("wiki.source")


def manifest_path(fs) -> Path:
    return fs.root / ".state" / "source_manifest.json"


def read_manifest(fs) -> dict:
    path = manifest_path(fs)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_manifest(fs, manifest: dict) -> None:
    manifest_path(fs).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def sha256(fs, content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def check_source_state(fs, relative_path: str, content: str) -> dict:
    """Check if a source file has changed since last ingest.

    Returns:
        {"status": "new"|"changed"|"unchanged"|"duplicate",
         "sha256": str, "duplicate_of": str | None}
    """
    manifest = read_manifest(fs)
    sha = sha256(fs, content)

    if relative_path in manifest:
        entry = manifest[relative_path]
        if entry["sha256"] == sha:
            return {"status": "unchanged", "sha256": sha, "duplicate_of": None}
        return {"status": "changed", "sha256": sha, "duplicate_of": None}

    # Check for duplicate (same hash, different path)
    for path, entry in manifest.items():
        if entry["sha256"] == sha:
            return {"status": "duplicate", "sha256": sha, "duplicate_of": path}

    return {"status": "new", "sha256": sha, "duplicate_of": None}


def update_source_manifest(fs, relative_path: str, content: str) -> None:
    """Record or update a source file in the manifest after ingest."""
    manifest = read_manifest(fs)
    sha = sha256(fs, content)
    now = datetime.now().isoformat(timespec="seconds")

    if relative_path in manifest:
        manifest[relative_path]["sha256"] = sha
        manifest[relative_path]["last_seen"] = now
        manifest[relative_path]["last_ingested"] = now
        manifest[relative_path]["status"] = "active"
        manifest[relative_path]["size"] = len(content)
    else:
        manifest[relative_path] = {
            "sha256": sha,
            "size": len(content),
            "first_seen": now,
            "last_seen": now,
            "last_ingested": now,
            "status": "active",
        }

    write_manifest(fs, manifest)


def mark_source_removed(fs, relative_path: str) -> None:
    """Mark a source file as removed (when raw file is deleted)."""
    manifest = read_manifest(fs)
    if relative_path in manifest:
        manifest[relative_path]["status"] = "removed"
        write_manifest(fs, manifest)


def compute_source_sha256(fs, content: str) -> str:
    """Compute SHA256 hash of source content."""
    return sha256(fs, content)


def source_card_path(fs, source_id: str) -> Path:
    """Return path for a Source Card: wiki/_sources/<project>/<source-slug>.md"""
    return fs._resolve_in_dir(fs.wiki_dir, f"_sources/{source_id}.md")


def write_source_card(fs, card: SourceCard) -> None:
    """Write or update a Source Card to wiki/_sources/."""
    path = source_card_path(fs, card.source_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    meta = {
        "title": card.title,
        "project": card.project,
        "type": "source",
        "tags": ["source", "ingest"],
        "confidence": 1.0,
        "sources": 1,
        "last_confirmed": card.last_confirmed,
        "supersedes": None,
        "superseded_by": None,
        "created": card.created,
        # Source Card specific fields
        "source_id": card.source_id,
        "source_path": card.source_path,
        "source_sha256": card.source_sha256,
        "ingest_status": card.ingest_status,
        "outline": card.outline,
        "chunk_count": card.chunk_count,
        "chunks_processed": card.chunks_processed,
        "chunks_failed": card.chunks_failed,
        "pages_planned": card.pages_planned,
        "pages_written": card.pages_written,
        "conflicts_opened": card.conflicts_opened,
        "claims_files": card.claims_files,
        "drift_status": card.drift_status,
    }

    # Build content section
    outline_lines = []
    for item in card.outline:
        indent = "  " * (item.get("level", 1) - 1)
        outline_lines.append(f"{indent}- {item['text']} ({item.get('char_count', 0)} chars)")

    content = (
        f"# {card.title}\n\n"
        f"**Source:** `{card.source_path}`\n"
        f"**SHA256:** `{card.source_sha256[:16]}...`\n"
        f"**Status:** {card.ingest_status}\n"
        f"**Drift:** {card.drift_status}\n\n"
        f"## Outline\n\n"
        + "\n".join(outline_lines)
        + f"\n\n## Stats\n\n"
        f"- Chunks: {card.chunks_processed}/{card.chunk_count} processed"
        f" ({card.chunks_failed} failed)\n"
        f"- Pages written: {len(card.pages_written)}\n"
        f"- Conflicts opened: {len(card.conflicts_opened)}\n"
        f"- Claims files: {len(card.claims_files)}\n"
    )

    post = frontmatter.Post(content, **meta)
    raw = frontmatter.dumps(post)
    path.write_text(raw, encoding="utf-8")
    logger.info("Source Card written: id=%s status=%s", card.source_id, card.ingest_status)


def read_source_card(fs, source_id: str) -> SourceCard | None:
    """Read a Source Card by source_id. Returns None if not found."""
    path = source_card_path(fs, source_id)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        post = frontmatter.loads(raw)
        meta = post.metadata

        def _date_or_today(field: str) -> str:
            val = meta.get(field, "")
            if isinstance(val, date):
                return val.isoformat()
            return val or date.today().isoformat()

        return SourceCard(
            source_id=meta.get("source_id", source_id),
            source_path=meta.get("source_path", ""),
            source_sha256=meta.get("source_sha256", ""),
            title=meta.get("title", f"Source: {source_id}"),
            project=meta.get("project", "_general"),
            ingest_status=meta.get("ingest_status", "unknown"),
            created=_date_or_today("created"),
            last_confirmed=_date_or_today("last_confirmed"),
            last_ingested=meta.get("last_ingested", ""),
            outline=meta.get("outline", []),
            chunk_count=meta.get("chunk_count", 0),
            chunks_processed=meta.get("chunks_processed", 0),
            chunks_failed=meta.get("chunks_failed", 0),
            pages_planned=meta.get("pages_planned", []),
            pages_written=meta.get("pages_written", []),
            conflicts_opened=meta.get("conflicts_opened", []),
            claims_files=meta.get("claims_files", []),
            drift_status=meta.get("drift_status", "unknown"),
        )
    except Exception as exc:
        logger.error("Source Card parse error: id=%s error=%s", source_id, exc)
        return None


def list_source_cards(fs, project: str | None = None) -> list[SourceCard]:
    """List all Source Cards, optionally filtered by project."""
    cards = []
    sources_dir = fs.wiki_dir / "_sources"
    if not sources_dir.exists():
        return cards

    for path in sorted(sources_dir.rglob("*.md")):
        slug = path.relative_to(fs.wiki_dir).with_suffix("").as_posix()
        # slug = "_sources/<project>/<source-slug>"
        parts = slug.split("/", 2)
        if len(parts) < 3:
            continue
        source_id = f"{parts[1]}/{parts[2]}"
        card = read_source_card(fs, source_id)
        if card is None:
            continue
        if project and card.project != project:
            continue
        cards.append(card)
    return cards


def check_source_drift(fs, relative_path: str) -> dict:
    """
    Check if a raw source file has drifted since last ingest.

    Returns:
        {"status": "unchanged"|"changed"|"missing_source"|"no_card",
         "old_sha256": str | None, "new_sha256": str | None}
    """
    manifest = read_manifest(fs)

    if relative_path not in manifest:
        raw_path = fs.raw_dir / relative_path
        if not raw_path.exists():
            return {
                "status": "missing_source",
                "old_sha256": None,
                "new_sha256": None,
            }
        return {"status": "no_card", "old_sha256": None, "new_sha256": None}

    entry = manifest[relative_path]
    old_sha = entry.get("sha256")

    content = fs.read_raw_file(relative_path)
    if content is None:
        return {
            "status": "missing_source",
            "old_sha256": old_sha,
            "new_sha256": None,
        }

    new_sha = compute_source_sha256(fs, content)

    if old_sha == new_sha:
        return {"status": "unchanged", "old_sha256": old_sha, "new_sha256": new_sha}

    return {"status": "changed", "old_sha256": old_sha, "new_sha256": new_sha}


def update_source_card_drift(fs, source_id: str, drift_status: str) -> None:
    """Update drift_status field on an existing Source Card."""
    card = read_source_card(fs, source_id)
    if card is None:
        logger.warning("Cannot update drift: Source Card not found: %s", source_id)
        return
    card.drift_status = drift_status
    if drift_status == "changed":
        card.ingest_status = "changed"
    write_source_card(fs, card)
