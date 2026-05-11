"""
utils.py — Shared utilities for agents.

Extracted from query_agent.py and audit_agent.py to avoid duplication.
"""

from __future__ import annotations

import json
import re
from datetime import datetime


def parse_json_block(text: str) -> dict:
    """
    Extract and parse JSON from LLM response.
    Handles ```json ... ``` fences and bare JSON objects.
    Raises ValueError if no valid JSON found.
    """
    fence = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))

    obj = re.search(r"\{.*\}", text, re.DOTALL)
    if obj:
        return json.loads(obj.group(0))

    arr = re.search(r"\[.*\]", text, re.DOTALL)
    if arr:
        return json.loads(arr.group(0))

    raise ValueError(f"Не удалось найти валидный JSON в ответе LLM: {text[:200]!r}")


def extract_wikilinks(text: str) -> list[str]:
    """
    Extract all [[slug]] and [[slug|text]] references from text.
    Returns list of unique slugs (without display text or anchors).
    """
    raw = re.findall(r"\[\[([^\]]+)\]\]", text)
    slugs = []
    seen = set()
    for item in raw:
        slug = item.split("|")[0].strip()
        slug = slug.split("#")[0].strip()
        if slug and slug not in seen:
            slugs.append(slug)
            seen.add(slug)
    return slugs


def now_iso() -> str:
    """Current datetime as ISO-8601 string, seconds precision."""
    return datetime.now().isoformat(timespec="seconds")


def heading_to_anchor(heading: str) -> str:
    """Convert markdown heading text to GitHub-style anchor slug."""
    anchor = heading.lower().strip()
    anchor = re.sub(r"[`*_\[\]()]", "", anchor)
    anchor = re.sub(r"[^\w\s-]", "", anchor)
    anchor = re.sub(r"\s+", "-", anchor)
    return anchor.strip("-")


# ═══════════════════════════════════════════════════════════════
# Slug validation
# ═══════════════════════════════════════════════════════════════

_SLUG_OK = re.compile(r"^[a-z0-9_/-]+$")
_PROJECT_OK = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_slug(slug: str) -> None:
    """Validate a wiki slug (e.g. ``myapp/storage/redis-cache``).

    Rules:
    - no absolute paths (no leading ``/``);
    - no ``..``;
    - no backslashes;
    - allowed characters: lowercase letters, numbers, ``_``, ``-``, ``/``;
    - no empty segments;
    - no leading/trailing slash.

    Raises ``ValueError`` on first violation.
    """
    if not slug:
        raise ValueError("Slug не может быть пустым")
    if slug.startswith("/"):
        raise ValueError("Slug не должен начинаться с /")
    if slug.endswith("/"):
        raise ValueError("Slug не должен заканчиваться на /")
    if ".." in slug:
        raise ValueError("Slug не должен содержать ..")
    if "\\" in slug:
        raise ValueError("Slug не должен содержать обратную косую черту")
    if not _SLUG_OK.fullmatch(slug):
        raise ValueError(
            f"Slug {slug!r} содержит недопустимые символы — "
            f"допустимы только строчные буквы, цифры, _, -, /"
        )
    for segment in slug.split("/"):
        if not segment:
            raise ValueError(f"Slug {slug!r} содержит пустой сегмент")


def is_safe_slug(slug: str) -> bool:
    """Return True if slug passes all validation rules, False otherwise."""
    try:
        validate_slug(slug)
        return True
    except ValueError:
        return False


def sanitize_to_slug(text: str) -> str:
    """Convert arbitrary text into a safe wiki slug.

    - lowercase
    - replace non-alphanumeric chars (except - and _) with hyphens
    - collapse multiple hyphens
    - strip leading/trailing hyphens and slashes
    """
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9/_-]", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    text = text.strip("/-")
    return text or "untitled"


def validate_project_name(project: str) -> None:
    """Validate project folder name used under raw/ and wiki/."""
    if not project:
        raise ValueError("Название проекта не может быть пустым")
    if project in (".", ".."):
        raise ValueError("Название проекта не должно быть . или ..")
    if "/" in project or "\\" in project:
        raise ValueError("Название проекта не должно содержать разделители пути")
    if ":" in project:
        raise ValueError("Название проекта не должно содержать ':'")
    if not _PROJECT_OK.fullmatch(project):
        raise ValueError(
            f"Название проекта {project!r} содержит недопустимые символы — "
            "допустимы только буквы, цифры, _, -"
        )


def validate_raw_filename(filename: str) -> None:
    """Validate raw source filename.
    Supports markdown (.md), text (.txt), python (.py), 
    and document formats (.pdf, .docx, .pptx) via mrkitdown.
    """
    if not filename:
        raise ValueError("Имя файла не может быть пустым")
    if filename in (".", ".."):
        raise ValueError("Имя файла не должно быть . или ..")
    if "/" in filename or "\\" in filename:
        raise ValueError("Имя файла не должно содержать разделители пути")
    if ".." in filename:
        raise ValueError("Имя файла не должно содержать '..'")
    if ":" in filename:
        raise ValueError("Имя файла не должно содержать ':'")
    
    # Allowed extensions
    allowed_extensions = {'.md', '.txt', '.py', '.pdf', '.docx', '.pptx'}
    if not any(filename.lower().endswith(ext) for ext in allowed_extensions):
        raise ValueError(
            f"Неподдерживаемый тип файла. "
            f"Допустимые расширения: {', '.join(sorted(allowed_extensions))}"
        )


# ═══════════════════════════════════════════════════════════════
# Conservative auto-linker
# ═══════════════════════════════════════════════════════════════

_SKIP_BLOCKS = re.compile(
    r"```.*?```|`[^`]+`|\[\[[^\]]+\]\]|\[[^]]+\]\([^)]+\)|^#{1,6}\s.*$",
    re.DOTALL | re.MULTILINE,
)
_AUTO_LINK_MAX = 10


def auto_link(
    content: str,
    link_candidates: list[dict],
    current_slug: str = "",
) -> str:
    """Post-process markdown content: add ``[[slug|text]]`` for known aliases.

    Rules:
    - Link first meaningful mention of each known alias (longest alias first).
    - Skip headings, code blocks, existing wikilinks, URLs.
    - Skip the current page's own slug and title.
    - Cap total additions at ``_AUTO_LINK_MAX``.

    Returns modified content.
    """
    # Build alias → slug map, longest alias first to match specific before general
    alias_map: list[tuple[str, str]] = []
    for c in link_candidates:
        if c["slug"] == current_slug:
            continue
        for alias in c.get("aliases", []):
            if len(alias) < 4:
                continue
            alias_map.append((alias.strip(), c["slug"]))
    alias_map.sort(key=lambda x: -len(x[0]))

    added = 0
    result_lines: list[str] = []
    in_code_block = False

    for line in content.split("\n"):
        if line.startswith("```"):
            in_code_block = not in_code_block
            result_lines.append(line)
            continue
        if in_code_block or line.startswith("#") or "[[]]" in line:
            result_lines.append(line)
            continue
        if "[" in line and "]" in line and "(http" in line:
            result_lines.append(line)
            continue

        modified = line
        for alias, slug in alias_map:
            if added >= _AUTO_LINK_MAX:
                break
            # Skip if already linked or in a code span
            if f"[[{slug}" in modified or f"]]{alias}" in modified:
                continue
            # Replace first occurrence of alias that is not already linked
            pattern = re.compile(
                rf"(?<!\[\[)\b{re.escape(alias)}\b(?!\]\])", re.IGNORECASE
            )
            match = pattern.search(modified)
            if not match:
                continue
            start = match.start()
            # Skip if inside an existing markdown link or code span
            prefix = modified[max(0, start - 1):start]
            if prefix in ("[", "`"):
                continue
            modified = pattern.sub(f"[[{slug}|{alias}]]", modified, count=1)
            added += 1

        result_lines.append(modified)

    return "\n".join(result_lines)


def validate_wikilinks(content: str, existing_slugs: set[str]) -> list[str]:
    """Check all [[slug]] references in content against existing slugs.

    Returns list of broken slugs that don't exist.
    Handles [[slug]], [[slug|text]], and [[slug#anchor]] formats.
    """
    broken = []
    for match in re.finditer(r"\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]", content):
        slug = match.group(1).strip()
        if slug and slug not in existing_slugs:
            broken.append(slug)
    return list(set(broken))
