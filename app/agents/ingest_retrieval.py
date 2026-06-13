"""
ingest_retrieval.py — Standalone retrieval helpers for the ingest pipeline.
"""

from __future__ import annotations

import logging

from app.core.interpreter import CodeInterpreter
from app.core.token_budget import ContextBudget
from app.core.wiki_fs import WikiFS, WikiPage

logger = logging.getLogger("wiki.ingest")


def find_related_pages(
    interpreter: CodeInterpreter,
    fs: WikiFS,
    source_content: str,
    project: str,
) -> list[WikiPage]:
    code = f"""
import re
import json
from pathlib import Path
wiki_dir = Path({str(fs.wiki_dir)!r})
source = {source_content[:3000]!r}
stopwords = {{'this', 'that', 'with', 'from', 'have', 'will', 'been',
              'they', 'their', 'what', 'when', 'also', 'into', 'more'}}
words = set(
    w.lower() for w in re.findall(r'\\b[a-zA-Zа-яА-Я]{{5,}}\\b', source)
    if w.lower() not in stopwords
)
candidates = []
for md in wiki_dir.rglob("*.md"):
    try:
        text = md.read_text(encoding="utf-8").lower()
        overlap = sum(1 for w in words if w in text)
        rel = md.relative_to(wiki_dir).with_suffix("").as_posix()
        candidates.append((rel, overlap))
    except Exception:
        pass
result = [slug for slug, score in sorted(candidates, key=lambda x: -x[1])[:10] if score > 0]
print(json.dumps(result))
"""
    output = interpreter.execute(code)
    slugs: list[str] = output.result_json or []
    pages = []
    for slug in slugs:
        page = fs.read_page(slug)
        if page:
            pages.append(page)
    return pages


def build_wiki_context(pages: list[WikiPage], budget: ContextBudget) -> str:
    parts = [p.raw for p in pages]
    fitted = budget.fit_wiki_pages(parts)
    return "\n\n---\n\n".join(fitted)
