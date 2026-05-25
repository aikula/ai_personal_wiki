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


def strip_trailing_json_artifact(text: str) -> str:
    """Remove a standalone JSON object/array accidentally appended at the end.

    This is intentionally conservative: it only strips a valid JSON block that
    starts at a line boundary near the end of the text and leaves the prefix in
    place.
    """
    stripped = text.rstrip()
    if not stripped:
        return text

    window_start = max(0, len(stripped) - 4096)
    tail = stripped[window_start:]

    for match in re.finditer(r"[\{\[]", tail):
        start = window_start + match.start()
        if start > 0 and stripped[start - 1] not in "\r\n \t":
            continue

        candidate = stripped[start:]
        try:
            json.loads(candidate)
        except Exception:
            continue

        prefix = stripped[:start].rstrip()
        if prefix:
            return prefix

    return text


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

_SLUG_OK = re.compile(r"^[\w/-]+$")
_PROJECT_OK = re.compile(r"^[A-Za-z0-9_-]+$")


def slugify(text: str) -> str:
    """Convert arbitrary text to a valid wiki slug.

    - Lowercase
    - Non-breaking hyphen (U+2011) → regular hyphen
    - Spaces → _
    - Any char not in [\\w/-] → _
    - Collapse consecutive _
    - Strip trailing _, preserve leading _ (e.g. _general)
    """
    text = text.lower()
    text = text.replace("\u2011", "-")
    text = re.sub(r"[^\w/-]+", "_", text)
    text = re.sub(r"_+", "_", text)
    text = text.rstrip("_")
    return text


def validate_slug(slug: str) -> None:
    """Validate a wiki slug (e.g. ``myapp/storage/redis-cache``).

    Rules:
    - no absolute paths (no leading ``/``);
    - no ``..``;
    - no backslashes;
    - allowed characters: Unicode letters, digits, ``_``, ``-``, ``/``;
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
            f"допустимы Unicode буквы, цифры, _, -, /"
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
        # Skip lines with existing wikilinks or URLs to avoid nested links
        if "[[" in line and "]]" in line:
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
                rf"(?<!\[)\b{re.escape(alias)}\b(?!\])", re.IGNORECASE
            )
            match = pattern.search(modified)
            if not match:
                continue
            start = match.start()
            # Skip if inside a code span
            prefix = modified[max(0, start - 1):start]
            if prefix == "`":
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


def normalize_wikilinks(content: str, existing_slugs: set[str] | None = None) -> str:
    """Post-process wikilinks and provenance markers in generated content.

    - Fix nested wikilinks: [[prefix/[[slug]] -> [[slug]]
    - Replace backslashes with forward slashes in [[...]] and ^[...]
    - Strip file extensions (.md, .pdf, .docx) from [[...]] slugs
    - Convert raw source file paths to plain text
    - If existing_slugs provided: unlink single-segment [[slug]] that don't exist (e.g. [[pgvector]])
    """
    # Fix nested wikilinks: [[prefix/[[slug]] -> [[slug]]
    # Run in a loop to handle arbitrarily deep nesting (e.g. [[a/[[b/[[c|txt]]]])
    while True:
        new = re.sub(
            r'\[\[([^\[\]]*?)\[\[([^\[\]]+?)(?:\|([^\[\]]+?))?\]\]',
            lambda m: '[[' + m.group(2) + ('|' + m.group(3) if m.group(3) else '') + ']]',
            content,
        )
        if new == content:
            break
        content = new
    # Replace backslashes with forward slashes inside [[...]]
    content = re.sub(r'\[\[([^\]]+)\]\]', lambda m: '[[' + m.group(1).replace('\\', '/') + ']]', content)
    # Strip file extensions from slugs inside [[...]]
    content = re.sub(
        r'\[\[([^\]]+?)\.(?:md|pdf|docx|pptx|txt)(\|[^\]]+)?\]\]',
        lambda m: '[[' + m.group(1) + (m.group(2) or '') + ']]',
        content,
        flags=re.IGNORECASE,
    )
    # Convert [[raw_file_path]] to plain text when the slug looks like a raw source file
    # (has a segment starting with digits, e.g. _general/01_eywa_baseline_source)
    content = re.sub(
        r'\[\[([a-z_]+/(?:\d+[-_])[^\]|]*?)(?:\|[^\]]+)?\]\]',
        lambda m: m.group(1),
        content,
    )
    # Normalize provenance markers without raw/ prefix
    # ^[_general/File.docx] → ^[raw/_general/File.docx]
    content = re.sub(
        r'\^\[(?!raw/)([^\]]+)\]',
        lambda m: f'^[raw/{m.group(1)}]',
        content,
    )
    # Remove wikilinks nested inside provenance markers (LLM artifact)
    # ^[raw/_general/[[slug|text]]‑file.pdf] → ^[raw/_general/text‑file.pdf]
    while re.search(r'\^\[raw/[^\]]*?\[\[', content):
        content = re.sub(
            r'(\^\[raw/[^\]]*?)\[\[[^\]]*?\|([^\]]+?)\]\]([^\]]*\])',
            lambda m: m.group(1) + m.group(2) + m.group(3),
            content,
        )
        content = re.sub(
            r'(\^\[raw/[^\]]*?)\[\[([^\]]+?)\]\]([^\]]*\])',
            lambda m: m.group(1) + m.group(2).split('/')[-1].replace('-', ' ').title() + m.group(3),
            content,
        )
    # Unlink non-existing wikilinks:
    # - single-segment (no /): common tech terms (pgvector, fastapi, etc.)
    # - first segment not a known project: made-up slugs (fastapi/backend, etc.)
    # - ends with - or _: truncated slugs (knowledge-, etc.)
    # - fails validate_slug: invalid characters (spaces, Cyrillic as page ref, etc.)
    if existing_slugs is not None:
        known_projects = {s.split("/")[0] for s in existing_slugs if "/" in s}
        def _unlink_broken(m: re.Match) -> str:
            slug = m.group(1).strip()
            display = m.group(2)
            if slug in existing_slugs:
                return m.group(0)
            # Invalid slug chars (spaces, etc.) — LLM artifact, not a page ref
            if not is_safe_slug(slug):
                return display if display else slug
            if "/" not in slug:
                return display if display else slug
            # Multi-segment: check first segment is a known project
            first_seg = slug.split("/")[0]
            if first_seg not in known_projects:
                return display if display else slug
            # Trailing hyphen/underscore: truncated slug
            if slug.endswith("-") or slug.endswith("_"):
                return display if display else slug
            return m.group(0)
        content = re.sub(
            r'\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]',
            _unlink_broken,
            content,
        )
    # Fix backslashes in provenance markers ^[...]
    content = re.sub(r'\^\[([^\]]+)\]', lambda m: '^[' + m.group(1).replace('\\', '/') + ']', content)
    return content
