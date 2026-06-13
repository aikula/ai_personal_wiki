"""
query_search.py — Standalone search/retrieval helpers for the query pipeline.
"""

from __future__ import annotations

import logging
import re

from app.core.token_budget import ContextBudget
from app.core.utils import extract_wikilinks
from app.core.wiki_fs import WikiFS, WikiPage
from app.core.wiki_types import Claim

logger = logging.getLogger("wiki.query")


def expand_query(question: str) -> list[str]:
    """
    Deterministic query expansion: generate additional search terms
    without calling LLM. Uses keyword extraction, common synonyms,
    and project detection.
    """
    expansions = [question]

    words = question.lower().split()
    stop_words = {
        "что", "как", "где", "когда", "почему", "зачем", "какой",
        "какие", "кто", "чем", "ли", "или", "и", "в", "на", "с",
        "по", "для", "из", "у", "к", "о", "а", "но", "не",
        "what", "how", "where", "when", "why", "which", "who",
        "is", "are", "does", "do", "the", "a", "an", "in", "on",
        "of", "for", "to", "with", "from", "about",
    }
    key_terms = [
        w.strip("?.,;:!\"'()")
        for w in words
        if w.lower() not in stop_words and len(w) > 2
    ]

    if key_terms:
        expansions.append(" ".join(key_terms))

    tech_expansions = {
        "бд": ["database", "postgresql", "sqlite"],
        "database": ["бд", "postgresql", "sqlite"],
        "кеш": ["cache", "redis"],
        "cache": ["кеш", "redis"],
        "деплой": ["deploy", "deployment", "развёртывание"],
        "deploy": ["деплой", "deployment", "развёртывание"],
        "api": ["endpoint", "route", "маршрут"],
        "фронтенд": ["frontend", "ui", "react"],
        "frontend": ["фронтенд", "ui", "react"],
        "бэкенд": ["backend", "fastapi", "server"],
        "backend": ["бэкенд", "fastapi", "server"],
    }
    for term in key_terms:
        if term.lower() in tech_expansions:
            for expansion in tech_expansions[term.lower()]:
                expansions.append(expansion)

    seen = set()
    unique = []
    for q in expansions:
        if q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


def index_first_retrieve(
    fs: WikiFS,
    question: str,
    project: str | None,
) -> list[str]:
    """Read project L1 index, match keywords against sections, return prioritized slugs."""
    if not project:
        return []
    page = fs.read_page(f"{project}/index")
    if not page:
        return []

    question_words = set(
        w.lower() for w in re.split(r"\W+", question) if len(w) > 2
    )
    if not question_words:
        return []

    index_links = extract_wikilinks(page.raw)
    lines = page.raw.split("\n")
    current_section = ""
    scored: list[tuple[int, str]] = []
    scored_slugs: set[str] = set()

    for line in lines:
        if line.startswith("##"):
            current_section = line.lower()
        for link in index_links:
            if f"[[{link}" in line and link not in scored_slugs:
                s_score = sum(1 for w in question_words if w in current_section)
                l_score = sum(1 for w in question_words if w in line.lower())
                if s_score + l_score > 0:
                    scored.append((s_score + l_score, link))
                    scored_slugs.add(link)

    scored.sort(reverse=True)
    return [slug for _, slug in scored[:10]]


def retrieve_pages(
    fs: WikiFS,
    keywords: list[str],
    project: str | None,
    top_k: int = 20,
) -> list[WikiPage]:
    """Weighted search with query expansion fallback. Returns WikiPage list."""
    query = " ".join(keywords)

    results = fs.search_pages_weighted(query, project=project, top_k=top_k)

    if len(results) < 3:
        expansions = expand_query(query)
        for expanded in expansions[1:]:
            more = fs.search_pages_weighted(expanded, project=project, top_k=top_k)
            for r in more:
                if r["slug"] not in {x["slug"] for x in results}:
                    results.append(r)
            if len(results) >= 3:
                break

    slugs = [r["slug"] for r in results[:top_k]]
    pages = []
    for slug in slugs:
        page = fs.read_page(slug)
        if page:
            pages.append(page)
    return pages


def retrieve_claims(
    fs: WikiFS,
    question: str,
    project: str | None,
    top_k: int = 5,
) -> list[Claim]:
    """Search claims relevant to the question. Returns Claim list."""
    return fs.search_claims(question, project=project, top_k=top_k)


def format_claims_for_context(claims: list[Claim]) -> str:
    """Format claims as markdown with [[_claims/...]] slug citations."""
    if not claims:
        return ""
    parts = ["\n\n## Relevant Claims\n"]
    for c in claims:
        claim_slug = c.file_path.replace(".md", "")
        parts.append(
            f"- **{c.normalized}** [[{claim_slug}]] "
            f"(confidence: {c.confidence:.1f}, source: `{c.source_path}`)\n"
        )
    return "".join(parts)


def build_wiki_context(
    budget: ContextBudget,
    pages: list[WikiPage],
) -> str:
    """Build context string from wiki pages, respecting token budget."""
    parts = [p.raw for p in pages]
    fitted = budget.fit_wiki_pages(parts)
    return "\n\n---\n\n".join(fitted)


def compress_session_history(
    llm,
    budget: ContextBudget,
    session,
) -> None:
    """Compress old messages via LLM summary when session has >4 messages."""

    from app.core.utils import now_iso

    non_tool = [m for m in session.messages if m.role != "tool"]
    if len(non_tool) <= 4:
        return

    to_summarize = non_tool[:-2]
    summary_text = "\n".join(
        f"{m.role.upper()}: {m.content[:500]}" for m in to_summarize
    )
    try:
        compressed = llm.call(
            system="Summarize this conversation in 2-3 sentences, preserving key facts and cited [[slug]] references.",
            prompt=summary_text,
            temperature=0.0,
        )
        summary_content = f"[Conversation summary] {compressed.strip()}"
        preserved_slugs = list(dict.fromkeys(
            s for m in to_summarize for s in m.cited_slugs
        ))

        from app.agents.query_types import ChatMessage

        summary_msg = ChatMessage(
            role="assistant",
            content=summary_content,
            timestamp=to_summarize[-1].timestamp if to_summarize else now_iso(),
            cited_slugs=preserved_slugs,
        )
        keep_from = non_tool[-2]
        keep_idx = session.messages.index(keep_from)
        session.messages = [summary_msg, *session.messages[keep_idx:]]
        logger.debug("Compressed %d old messages into summary", len(to_summarize))
    except Exception as exc:
        logger.warning("History compression failed: %s", exc)


def read_skills_rules(fs: WikiFS) -> str:
    skills = fs.read_skills()
    match = re.search(
        r"## Query Formatting Rules\n(.*?)(?=\n## |\Z)", skills, re.DOTALL
    )
    return match.group(1).strip() if match else ""


def read_agents_md(fs: WikiFS) -> str:
    path = fs.root / "AGENTS.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def generate_answer(
    llm,
    fs: WikiFS,
    budget: ContextBudget,
    question: str,
    wiki_context: str,
    session,
) -> str:
    """Generate answer using LLM with wiki context."""
    from app.agents.query_prompts import ANSWER_PROMPT, ANSWER_SYSTEM

    skills = read_skills_rules(fs)
    agents_md = read_agents_md(fs)

    system = ANSWER_SYSTEM.format(skills_query_rules=skills or "None.")
    if agents_md:
        system = agents_md + "\n\n" + system

    history = session.to_llm_messages(budget.history)

    messages = [*history, {
        "role": "user",
        "content": ANSWER_PROMPT.format(
            question=question,
            wiki_context=budget.trim(wiki_context, "wiki_context"),
        )
    }]
    return llm.call(
        system=system,
        prompt=messages[-1]["content"],
        temperature=0.2,
    )


def is_meta_question(question: str) -> bool:
    meta_signals = [
        "сколько страниц", "how many pages", "список проектов",
        "list projects", "wiki stats", "статистика вики",
        "открытых конфликт", "open conflicts", "what projects",
    ]
    q = question.lower()
    return any(s in q for s in meta_signals)


def classify_question(llm, question: str) -> tuple[str, list[str]]:
    """Classify question type using LLM."""
    from app.agents.query_prompts import CLASSIFY_PROMPT
    from app.agents.query_types import QUESTION_TYPES
    from app.core.utils import parse_json_block

    raw = llm.call(
        system="You classify questions. Output only JSON.",
        prompt=CLASSIFY_PROMPT.format(question=question),
        temperature=0.0,
    )
    try:
        data = parse_json_block(raw)
        q_type = data.get("type", "exploratory")
        if q_type not in QUESTION_TYPES:
            q_type = "exploratory"
        keywords = data.get("keywords", question.split()[:5])
        return q_type, keywords
    except ValueError:
        logger.warning("Classification failed, defaulting to exploratory")
        return "exploratory", question.split()[:5]


def handle_meta(fs: WikiFS, question: str, session) -> dict:
    """Handle meta questions about wiki stats."""
    from app.agents.query_types import ChatMessage, QueryResult
    from app.core.utils import now_iso

    tree = fs.get_wiki_tree()
    projects = list(tree["projects"].keys())
    total = tree["total_pages"]
    open_c = tree["open_conflicts"]

    q = question.lower()
    if "проект" in q or "project" in q:
        answer = (
            f"В wiki {len(projects)} проектов: "
            f"{', '.join(f'**{p}**' for p in projects)}. "
            f"Всего страниц: {total}."
        )
    elif "конфликт" in q or "conflict" in q:
        answer = f"Открытых конфликтов: **{open_c}**."
    else:
        lines = [
            "**Wiki stats:**", f"- Страниц: {total}",
            f"- Проектов: {len(projects)}",
            f"- Открытых конфликтов: {open_c}",
        ]
        for proj, pg in sorted(tree["projects"].items()):
            lines.append(f"- {proj}: {len(pg)} страниц")
        answer = "\n".join(lines)

    session.messages.append(ChatMessage(
        role="user", content=question, timestamp=now_iso()
    ))
    session.messages.append(ChatMessage(
        role="assistant", content=answer, timestamp=now_iso()
    ))
    return QueryResult(
        answer=answer, cited_slugs=[], question_type="meta",
        pages_read=[], iterations=0, session_id=session.session_id,
    )


def crystallize_session(llm, fs: WikiFS, session) -> str | None:
    """Crystallize Q&A session as wiki page."""
    from datetime import date

    from app.agents.query_prompts import CRYSTALLIZE_PROMPT
    from app.core.utils import parse_json_block

    user_msgs = [m for m in session.messages if m.role == "user"]
    if len(user_msgs) < 3:
        return None

    session_text = "\n\n".join(
        f"{m.role.upper()}: {m.content}" for m in session.messages
    )
    today = date.today().isoformat()

    raw = llm.call(
        system="You distill Q&A sessions into reusable wiki pages.",
        prompt=CRYSTALLIZE_PROMPT.format(
            session_text=session_text[:8000],
            today=today,
        ),
        temperature=0.1,
    )

    try:
        data = parse_json_block(raw)
        meta = data.get("meta", {})
        content = data.get("content", "")
        if not content:
            return None

        short_id = session.session_id[:8]
        slug = f"_general/queries/session-{short_id}"

        if not meta.get("created"):
            meta["created"] = today

        fs.write_page(slug=slug, meta=meta, content=content)
        return slug
    except Exception:
        return None
