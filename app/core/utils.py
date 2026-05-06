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
