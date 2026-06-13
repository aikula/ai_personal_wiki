"""wiki_index.py — Index operations for wiki-data/.

Extracted from wiki_fs.py. Each function takes 'fs' as first parameter
(duck typing, no direct WikiFS import).
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import date

import frontmatter

logger = logging.getLogger("wiki.index")


def bootstrap_index(fs) -> None:
    today = date.today().isoformat()
    content = f"""---
title: Wiki Index
project: _general
type: index
tags: []
confidence: 1.0
sources: 0
last_confirmed: {today}
supersedes: null
superseded_by: null
created: {today}
---

# Wiki Index

Last updated: {today}
Pages: 0 | Projects: 0 | Open conflicts: 0
"""
    (fs.wiki_dir / "index.md").write_text(content, encoding="utf-8")


def defer_index(fs) -> None:
    fs._defer_index = True


def resume_index(fs) -> None:
    fs._defer_index = False


def rebuild_index(fs) -> None:
    pages = fs.list_pages()
    projects = {p.project for p in pages}
    open_conf = fs.count_open_conflicts()
    today = date.today().isoformat()

    by_project: dict[str, list] = {}
    for p in pages:
        by_project.setdefault(p.project, []).append(p)

    for proj, proj_pages in by_project.items():
        write_project_index(fs, proj, proj_pages, today)

    index_path = fs.wiki_dir / "index.md"
    index_page = fs.read_page("index")

    lines = [
        f"Last updated: {today}",
        f"Pages: {len(pages)} | "
        f"Projects: {len(projects)} | "
        f"Open conflicts: {open_conf}",
        "",
    ]

    for proj in sorted(projects):
        proj_pages = by_project.get(proj, [])
        lines.append(f"## {proj} ({len(proj_pages)} pages)")
        lines.append(f"[[{proj}/index]] — project {proj}")
        lines.append("")

    new_content = "\n".join(lines).rstrip() + "\n"

    if index_page is None:
        meta = {
            "title": "Wiki Index",
            "project": "_general",
            "type": "index",
            "tags": [],
            "confidence": 1.0,
            "sources": 0,
            "last_confirmed": today,
            "supersedes": None,
            "superseded_by": None,
            "created": today,
        }
        index_path.write_text(
            frontmatter.dumps(frontmatter.Post(new_content, **meta)),
            encoding="utf-8",
        )
    else:
        index_path.write_text(
            frontmatter.dumps(frontmatter.Post(new_content, **index_page.meta)),
            encoding="utf-8",
        )

    logger.info("Index rebuilt: pages=%d projects=%d", len(pages), len(projects))


def write_project_index(fs, project: str, pages: list, today: str) -> None:
    content_pages = [
        p for p in pages
        if not p.slug.startswith(("_claims/", "_sources/"))
    ]
    by_letter: dict[str, list] = {}
    for p in content_pages:
        first = (p.title or "?")[0].upper()
        by_letter.setdefault(first, []).append(p)

    lines = [
        f"# {project} Wiki",
        "",
        f"Last updated: {today}",
        f"Pages: {len(content_pages)}",
        "",
    ]

    for letter in sorted(by_letter.keys()):
        lines.append(f"## {letter}")
        for p in sorted(by_letter[letter], key=lambda x: x.title):
            lines.append(f"[[{p.slug}]] — {p.title}")
        lines.append("")

    content = "\n".join(lines).rstrip() + "\n"

    if len(content) > fs.limits.index_l1_chars:
        logger.warning("Project index %s exceeds char limit (%d > %d), trimming",
                       project, len(content), fs.limits.index_l1_chars)
        trimmed = list(lines[:4])
        for letter in sorted(by_letter.keys()):
            page_list = sorted(by_letter[letter], key=lambda x: x.title)
            trimmed.append(f"## {letter}")
            for p in page_list:
                candidate = [*trimmed, f"[[{p.slug}]] — {p.title}", ""]
                if len("\n".join(candidate)) <= fs.limits.index_l1_chars:
                    trimmed.append(f"[[{p.slug}]] — {p.title}")
                else:
                    trimmed.append(f"(({len(page_list)} страниц, достигнут лимит))")
                    break
            trimmed.append("")
        content = "\n".join(trimmed).rstrip() + "\n"
        logger.info("Project index %s trimmed to %d chars", project, len(content))

    meta = {
        "title": f"{project} Wiki",
        "project": project,
        "type": "index",
        "tags": [],
        "confidence": 1.0,
        "sources": 0,
        "last_confirmed": today,
        "supersedes": None,
        "superseded_by": None,
        "created": today,
    }

    index_path = fs.wiki_dir / project / "index.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(content, **meta)
    index_path.write_text(frontmatter.dumps(post), encoding="utf-8")
    logger.debug("Wrote project index: %s", project)


def update_index_entry(fs, slug: str, meta: dict) -> None:
    if fs._defer_index:
        return
    pages = fs.list_pages()
    projects = {p.project for p in pages}
    open_conf = fs.count_open_conflicts()
    today = date.today().isoformat()

    index_path = fs.wiki_dir / "index.md"
    index_page = fs.read_page("index")
    if index_page is None:
        return

    stats_block = (
        f"Last updated: {today}\n"
        f"Pages: {len(pages)} | "
        f"Projects: {len(projects)} | "
        f"Open conflicts: {open_conf}"
    )
    new_content = re.sub(
        r"Last updated:.*?Open conflicts: \d+",
        stats_block,
        index_page.content,
        flags=re.DOTALL,
    )

    project = meta.get("project", "_general")
    if f"## {project}" not in new_content:
        new_content += (
            f"\n## {project}\n"
            f"[[{project}/index]] — project {project}\n"
        )

    index_path.write_text(
        frontmatter.dumps(frontmatter.Post(new_content, **index_page.meta)),
        encoding="utf-8"
    )

    proj_pages = [p for p in pages if p.project == project]
    write_project_index(fs, project, proj_pages, today)


def remove_index_entry(fs, slug: str) -> None:
    index_path = fs.wiki_dir / "index.md"
    if not index_path.exists():
        return
    content = index_path.read_text(encoding="utf-8")
    content = re.sub(rf"\[\[{re.escape(slug)}[^\]]*\]\][^\n]*\n?", "", content)
    index_path.write_text(content, encoding="utf-8")


def full_reset_wiki(fs) -> None:
    """Remove and re-bootstrap the entire wiki directory."""
    if fs.wiki_dir.exists():
        shutil.rmtree(fs.wiki_dir)
        logger.info("Wiki directory removed: %s", fs.wiki_dir)
    fs.wiki_dir.mkdir()
    bootstrap_index(fs)
    today = date.today().isoformat()
    log_content = f"""---
title: Change Log
project: _general
type: log
tags: []
confidence: 1.0
sources: 0
last_confirmed: {today}
supersedes: null
superseded_by: null
created: {today}
---

# Change Log

"""
    (fs.wiki_dir / "log.md").write_text(log_content, encoding="utf-8")
    logger.info("Wiki directory re-bootstrapped: %s", fs.wiki_dir)
