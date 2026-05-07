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

    raise ValueError(f"No valid JSON found in LLM response: {text[:200]!r}")


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
        raise ValueError("Slug must not be empty")
    if slug.startswith("/"):
        raise ValueError("Slug must not start with /")
    if slug.endswith("/"):
        raise ValueError("Slug must not end with /")
    if ".." in slug:
        raise ValueError("Slug must not contain ..")
    if "\\" in slug:
        raise ValueError("Slug must not contain backslash")
    if not _SLUG_OK.fullmatch(slug):
        raise ValueError(
            f"Slug {slug!r} contains invalid characters — "
            f"only lowercase, digits, _, -, / allowed"
        )
    for segment in slug.split("/"):
        if not segment:
            raise ValueError(f"Slug {slug!r} contains empty segment")


# ═══════════════════════════════════════════════════════════════
# Conservative auto-linker
# ═══════════════════════════════════════════════════════════════

_SKIP_BLOCKS = re.compile(
    r"```.*?```|`[^`]+`|\[\[[^\]]+\]\]|\[[^]]+\]\([^)]+\)|^#{1,6}\s.*$",
    re.DOTALL | re.MULTILINE,
)
_AUTO_LINK_MAX = 6


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
