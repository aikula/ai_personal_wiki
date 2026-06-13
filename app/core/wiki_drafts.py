"""
wiki_drafts.py — Draft artifact operations for wiki-data/.

Extracted from wiki_fs.py. Each function takes 'fs' as first parameter
(duck typing, no direct WikiFS import).
"""

from __future__ import annotations

import difflib
import json
import logging
import shutil
from pathlib import Path

import frontmatter

from app.core.wiki_types import WikiFSError

logger = logging.getLogger("wiki.drafts")


def drafts_dir(fs) -> Path:
    return fs.root / "drafts"


def create_draft(
    fs,
    draft_id: str,
    plan: dict,
    pages: dict[str, str],
    conflicts: list[dict],
) -> None:
    """Create a draft artifact for human review.

    Args:
        draft_id:  e.g. ``ingest-20260507-120000``
        plan:      dict with analysis plan summary
        pages:     ``{slug: new_content_markdown}`` for each candidate
        conflicts: list of conflict dicts

    Writes to ``drafts/{draft_id}/``.
    """
    d = drafts_dir(fs) / draft_id
    d.mkdir(parents=True, exist_ok=True)

    (d / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (d / "conflicts.json").write_text(
        json.dumps(conflicts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    pages_dir = d / "pages"
    diffs_dir = d / "diffs"
    pages_dir.mkdir(exist_ok=True)
    diffs_dir.mkdir(exist_ok=True)

    for slug, content in pages.items():
        safe = slug.replace("/", "__")
        (pages_dir / f"{safe}.md").write_text(content, encoding="utf-8")

        old = fs.read_page(slug)
        old_text = old.raw if old else ""
        diff = list(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"wiki/{slug}",
                tofile=f"draft/{slug}",
            )
        )
        if diff:
            (diffs_dir / f"{safe}.diff.md").write_text(
                "".join(diff), encoding="utf-8"
            )

    logger.info("Draft created: id=%s pages=%d", draft_id, len(pages))


def list_drafts(fs) -> list[dict]:
    """List pending drafts with metadata."""
    if not drafts_dir(fs).exists():
        return []
    drafts = []
    for d in sorted(drafts_dir(fs).iterdir()):
        if not d.is_dir():
            continue
        plan_path = d / "plan.json"
        conflicts_path = d / "conflicts.json"
        pages = sorted(
            p.stem.replace("__", "/")
            for p in (d / "pages").glob("*.md")
        ) if (d / "pages").exists() else []
        diffs = sorted(
            p.name for p in (d / "diffs").glob("*.diff.md")
        ) if (d / "diffs").exists() else []
        plan = {}
        if plan_path.exists():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        conflicts = []
        if conflicts_path.exists():
            conflicts = json.loads(conflicts_path.read_text(encoding="utf-8"))
        drafts.append({
            "id": d.name,
            "created": d.stat().st_mtime,
            "pages": pages,
            "diff_count": len(diffs),
            "conflict_count": len(conflicts),
            "plan_summary": plan.get("summary", ""),
        })
    return drafts


def read_draft(fs, draft_id: str) -> dict | None:
    """Read full draft details, returns None if not found."""
    d = drafts_dir(fs) / draft_id
    if not d.exists():
        return None

    pages: list[dict] = []
    diffs: list[dict] = []
    pages_dir = d / "pages"
    diffs_dir = d / "diffs"

    if pages_dir.exists():
        for p in sorted(pages_dir.glob("*.md")):
            slug = p.stem.replace("__", "/")
            pages.append({
                "slug": slug,
                "content": p.read_text(encoding="utf-8"),
            })
    if diffs_dir.exists():
        for p in sorted(diffs_dir.glob("*.diff.md")):
            diffs.append({
                "filename": p.name,
                "content": p.read_text(encoding="utf-8"),
            })

    plan = {}
    plan_path = d / "plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))

    conflicts = []
    conflicts_path = d / "conflicts.json"
    if conflicts_path.exists():
        conflicts = json.loads(conflicts_path.read_text(encoding="utf-8"))

    return {
        "id": draft_id,
        "plan": plan,
        "pages": pages,
        "diffs": diffs,
        "conflicts": conflicts,
    }


def apply_draft(fs, draft_id: str) -> list[str]:
    """Apply a draft: write all candidate pages to the wiki.
    Returns list of applied slugs. Removes draft on success."""
    draft = read_draft(fs, draft_id)
    if draft is None:
        raise WikiFSError(f"Черновик не найден: {draft_id}")

    applied = []
    errors: list[str] = []
    for page in draft["pages"]:
        try:
            post = frontmatter.loads(page["content"])
            meta = dict(post.metadata)
            content = post.content
        except Exception as exc:
            msg = f"{page['slug']}: parse error ({exc})"
            errors.append(msg)
            logger.warning("Draft apply parse error: %s", msg)
            continue

        try:
            fs.write_page(
                slug=page["slug"],
                meta=meta,
                content=content,
                allow_overwrite=True,
            )
            applied.append(page["slug"])
        except Exception as exc:
            msg = f"{page['slug']}: write error ({exc})"
            errors.append(msg)
            logger.warning("Draft apply write error: %s", msg)

    if errors:
        raise WikiFSError("Применение черновика завершено с ошибками: " + "; ".join(errors))
    if not applied:
        raise WikiFSError("Применение черновика: нет валидных страниц для применения")

    shutil.rmtree(drafts_dir(fs) / draft_id)
    logger.info("Draft applied: id=%s pages=%s", draft_id, applied)
    return applied


def reject_draft(fs, draft_id: str) -> bool:
    """Reject a draft: remove the draft directory. Returns True if removed."""
    d = drafts_dir(fs) / draft_id
    if not d.exists():
        return False
    shutil.rmtree(d)
    logger.info("Draft rejected: id=%s", draft_id)
    return True


def clear_all_drafts(fs) -> int:
    """Remove all pending drafts. Called before rebuild to avoid stale drafts."""
    if not drafts_dir(fs).exists():
        return 0
    removed = 0
    for d in drafts_dir(fs).iterdir():
        if d.is_dir():
            shutil.rmtree(d)
            removed += 1
    logger.info("Cleared %d stale drafts before rebuild", removed)
    return removed
