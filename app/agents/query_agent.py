"""
query_agent.py — Policy-driven ReAct agent for wiki queries.

Flow:
  1. CLASSIFY:  Determine question type → select policy (fast path vs ReAct)
  2. RETRIEVE:  Fetch relevant wiki pages (grep + frontmatter filter)
  3. ANSWER:    Generate response with [[slug]] citations
  4. CRYSTALLIZE (optional): Save Q&A session as wiki page

Policies:
  factual      — single fact lookup: classify → grep → answer (2 LLM calls)
  comparison   — compare across pages/projects: multi-read → synthesize
  exploratory  — open-ended: full ReAct loop (up to max_iterations)
  meta         — questions about wiki itself (stats, structure): no LLM needed

Rules:
  - Answers MUST cite sources as [[slug]] inline
  - Skills.md Query Formatting Rules are BINDING
  - Cross-project answers show both implementations, never pick a winner
  - History is included in context (budget permitting)
  - ReAct loop hard-stops at max_iterations (default 5)
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Generator
from datetime import date

from app.agents.query_prompts import (
    ANSWER_PROMPT,
    ANSWER_SYSTEM,
    CLASSIFY_PROMPT,
    CRYSTALLIZE_PROMPT,
    REACT_PROMPT,
    REACT_SYSTEM,
)
from app.agents.query_types import (
    QUESTION_TYPES,
    ChatMessage,
    ChatSession,
    QueryResult,
)
from app.config import Settings
from app.core.interpreter import CodeInterpreter
from app.core.llm_client import LLMGateway
from app.core.token_budget import ContextBudget
from app.core.utils import extract_wikilinks, now_iso, parse_json_block
from app.core.wiki_fs import WikiFS, WikiPage

logger = logging.getLogger("wiki.query")


class QueryAgent:
    """
    Handles user questions against wiki.

    Usage (non-streaming):
        agent = QueryAgent(fs, llm, interpreter, settings)
        result = agent.run(question="...", session=session)

    Usage (streaming for UI):
        for chunk in agent.stream(question="...", session=session):
            yield chunk   # SSE text chunks
    """

    def __init__(
        self,
        fs: WikiFS,
        llm: LLMGateway,
        interpreter: CodeInterpreter,
        settings: Settings,
    ):
        self.fs = fs
        self.llm = llm
        self.interpreter = interpreter
        self.settings = settings
        self.budget = ContextBudget(settings)
        self.max_iterations = 5

    # ── Public: non-streaming ────────────────────────────────────

    def run(
        self,
        question: str,
        session: ChatSession,
    ) -> QueryResult:
        if self._is_meta_question(question):
            return self._handle_meta(question, session)

        question_type, keywords = self._classify(question)
        logger.info("Query: type=%s question=%s", question_type, question[:100])

        if question_type == "factual":
            answer, pages_read, iters = self._policy_factual(question, keywords, session)
        elif question_type == "comparison":
            answer, pages_read, iters = self._policy_comparison(question, keywords, session)
        else:
            answer, pages_read, iters = self._policy_react(question, session)

        cited = extract_wikilinks(answer)

        session.messages.append(ChatMessage(
            role="user",
            content=question,
            timestamp=now_iso(),
        ))
        session.messages.append(ChatMessage(
            role="assistant",
            content=answer,
            timestamp=now_iso(),
            cited_slugs=cited,
        ))

        return QueryResult(
            answer=answer,
            cited_slugs=cited,
            question_type=question_type,
            pages_read=pages_read,
            iterations=iters,
            session_id=session.session_id,
        )

    # ── Public: streaming ────────────────────────────────────────

    def stream(
        self,
        question: str,
        session: ChatSession,
    ) -> Generator[str, None, None]:
        if self._is_meta_question(question):
            result = self._handle_meta(question, session)
            yield result.answer
            yield "[DONE]"
            return

        question_type, keywords = self._classify(question)

        if question_type in ("factual", "comparison"):
            pages_read = self._retrieve_pages(keywords, session.project_filter, top_k=20)
            wiki_context = self._build_wiki_context(pages_read)
            skills = self._skills_query_rules()
            agents_md = self._read_agents_md()

            history = session.to_llm_messages(self.budget.history)
            messages = [*history, {"role": "user", "content": question}]

            system = ANSWER_SYSTEM.format(skills_query_rules=skills)
            if agents_md:
                system = agents_md + "\n\n" + system

            full_prompt = ANSWER_PROMPT.format(
                question=question,
                wiki_context=self.budget.trim(wiki_context, "wiki_context"),
            )
            messages[-1]["content"] = full_prompt

            full_answer = ""
            for chunk in self.llm.stream(system=system, messages=messages):
                full_answer += chunk
                yield chunk

            cited = extract_wikilinks(full_answer)
            for slug in cited:
                yield f"[CITED:{slug}]"

            session.messages.append(ChatMessage(
                role="user", content=question, timestamp=now_iso()
            ))
            session.messages.append(ChatMessage(
                role="assistant", content=full_answer,
                timestamp=now_iso(), cited_slugs=cited,
            ))

        else:
            answer, _, _ = self._policy_react(question, session)
            yield answer
            for slug in extract_wikilinks(answer):
                yield f"[CITED:{slug}]"
            session.messages.append(ChatMessage(
                role="user", content=question, timestamp=now_iso()
            ))
            session.messages.append(ChatMessage(
                role="assistant", content=answer, timestamp=now_iso()
            ))

        yield "[DONE]"

    # ── Policy: factual ──────────────────────────────────────────

    def _policy_factual(
        self,
        question: str,
        keywords: list[str],
        session: ChatSession,
    ) -> tuple[str, list[str], int]:
        pages = self._retrieve_pages(keywords, session.project_filter, top_k=20)

        # Index-first: read outlines to select best pages before reading full content
        selected_pages = []
        for page in pages[:10]:
            outline = self.fs.read_page_outline(page.slug)
            if outline:
                # Check if outline headings or synopsis match question keywords
                kw_lower = [k.lower() for k in keywords]
                heading_match = any(
                    any(kw in h["text"].lower() for kw in kw_lower)
                    for h in outline.headings
                )
                synopsis_match = any(
                    kw in outline.synopsis.lower() for kw in kw_lower
                )
                if heading_match or synopsis_match:
                    selected_pages.append(page)
                elif len(selected_pages) < 3:
                    selected_pages.append(page)
            else:
                selected_pages.append(page)

        if not selected_pages:
            selected_pages = pages[:4]

        wiki_context = self._build_wiki_context(selected_pages)
        answer = self._generate_answer(question, wiki_context, session)
        return answer, [p.slug for p in selected_pages], 1

    def _policy_comparison(
        self,
        question: str,
        keywords: list[str],
        session: ChatSession,
    ) -> tuple[str, list[str], int]:
        pages = self._retrieve_pages(keywords, project=None, top_k=20)

        by_project: dict[str, list[WikiPage]] = {}
        for p in pages:
            by_project.setdefault(p.project, []).append(p)

        sections = []
        for proj, proj_pages in sorted(by_project.items()):
            # Index-first: select best pages per project via outlines
            best = []
            for page in proj_pages[:5]:
                outline = self.fs.read_page_outline(page.slug)
                if outline:
                    kw_lower = [k.lower() for k in keywords]
                    match = any(
                        any(kw in h["text"].lower() for kw in kw_lower)
                        for h in outline.headings
                    ) or any(kw in outline.synopsis.lower() for kw in kw_lower)
                    if match or len(best) < 2:
                        best.append(page)
                else:
                    best.append(page)

            content = "\n---\n".join(pp.raw for pp in best)
            sections.append(f"### Project: {proj}\n{content}")
        wiki_context = "\n\n".join(sections)

        answer = self._generate_answer(question, wiki_context, session)
        return answer, [p.slug for p in pages], 1

    # ── Policy: comparison ───────────────────────────────────────

    def _policy_comparison(
        self,
        question: str,
        keywords: list[str],
        session: ChatSession,
    ) -> tuple[str, list[str], int]:
        pages = self._retrieve_pages(keywords, project=None, top_k=6)

        by_project: dict[str, list[WikiPage]] = {}
        for p in pages:
            by_project.setdefault(p.project, []).append(p)

        sections = []
        for proj, proj_pages in sorted(by_project.items()):
            content = "\n---\n".join(pp.raw for pp in proj_pages)
            sections.append(f"### Project: {proj}\n{content}")
        wiki_context = "\n\n".join(sections)

        answer = self._generate_answer(question, wiki_context, session)
        return answer, [p.slug for p in pages], 1

    # ── Policy: ReAct ────────────────────────────────────────────

    def _policy_react(
        self,
        question: str,
        session: ChatSession,
    ) -> tuple[str, list[str], int]:
        agents_md = self._read_agents_md()
        skills = self.fs.read_skills()
        history_msgs = session.to_llm_messages(self.budget.history)
        history_text = "\n".join(
            f"{m['role'].upper()}: {m['content'][:200]}" for m in history_msgs
        )

        scratchpad: list[str] = []
        pages_read: list[str] = []

        system = REACT_SYSTEM.format(max_iterations=self.max_iterations)
        if agents_md:
            system = agents_md + "\n\n" + system
        if skills:
            system += f"\n\n## Skills (BINDING)\n{skills}"

        for iteration in range(self.max_iterations):
            prompt = REACT_PROMPT.format(
                question=question,
                history=history_text,
                scratchpad="\n".join(scratchpad) or "None yet.",
            )

            raw = self.llm.call(system=system, prompt=prompt, temperature=0.1)

            try:
                action_data = parse_json_block(raw)
            except ValueError:
                return raw, pages_read, iteration + 1

            action = action_data.get("action")
            inp = action_data.get("input", {})

            if action == "answer":
                answer = action_data.get("content", "")
                logger.info("ReAct answered: iterations=%d pages=%d",
                            iteration + 1, len(pages_read))
                return answer, pages_read, iteration + 1

            elif action == "search_wiki":
                query = inp.get("query", "")
                project = inp.get("project") or session.project_filter
                results = self.fs.search_pages(query, project=project)
                tool_result = json.dumps(results[:5], ensure_ascii=False)
                scratchpad.append(
                    f"[iter {iteration+1}] search_wiki({query!r}) → {tool_result}"
                )
                logger.debug("ReAct iter=%d: search_wiki(%s) → %d results",
                             iteration + 1, query, len(results))

            elif action == "read_page":
                slug = inp.get("slug", "")
                page = self.fs.read_page(slug)
                if page:
                    pages_read.append(slug)
                    outline = self.fs.read_page_outline(slug)
                    if outline:
                        preview = (
                            f"Title: {outline.title}\n"
                            f"Synopsis: {outline.synopsis}\n"
                            f"Headings: {', '.join(h['text'] for h in outline.headings)}\n"
                            f"Tags: {', '.join(outline.tags)}\n"
                        )
                    else:
                        preview = page.raw[:1500]
                    scratchpad.append(
                        f"[iter {iteration+1}] read_page({slug!r}) → {preview}"
                    )
                else:
                    scratchpad.append(
                        f"[iter {iteration+1}] read_page({slug!r}) → NOT FOUND"
                    )

            elif action == "read_section":
                slug = inp.get("slug", "")
                heading = inp.get("heading", "")
                section = self.fs.read_page_section(slug, heading)
                if section:
                    pages_read.append(slug)
                    scratchpad.append(
                        f"[iter {iteration+1}] read_section({slug!r}, {heading!r}) → "
                        f"{section.content[:1500]}"
                    )
                else:
                    scratchpad.append(
                        f"[iter {iteration+1}] read_section({slug!r}, {heading!r}) → NOT FOUND"
                    )

            elif action == "execute_code":
                code = inp.get("code", "")
                reasoning = inp.get("reasoning", "")
                if not self.settings.query.allow_code_execution:
                    scratchpad.append(
                        f"[iter {iteration+1}] execute_code denied (disabled by settings)"
                    )
                    continue
                output = self.interpreter.execute(code)
                result_str = json.dumps(output.to_dict(), ensure_ascii=False)[:800]
                scratchpad.append(
                    f"[iter {iteration+1}] execute_code ({reasoning}) → {result_str}"
                )

            else:
                scratchpad.append(f"[iter {iteration+1}] Unknown action: {action}")

        final_prompt = (
            f"Based on your research:\n{chr(10).join(scratchpad)}\n\n"
            f"Answer the question: {question}\n"
            f"Use [[slug]] citations. If not found, say so."
        )
        answer = self.llm.call(system=system, prompt=final_prompt, temperature=0.1)
        logger.info("ReAct max_iterations: pages=%d", len(pages_read))
        return answer, pages_read, self.max_iterations

    # ── Meta questions ───────────────────────────────────────────

    def _is_meta_question(self, question: str) -> bool:
        meta_signals = [
            "сколько страниц", "how many pages", "список проектов",
            "list projects", "wiki stats", "статистика вики",
            "открытых конфликт", "open conflicts", "what projects",
        ]
        q = question.lower()
        return any(s in q for s in meta_signals)

    def _handle_meta(self, question: str, session: ChatSession) -> QueryResult:
        tree = self.fs.get_wiki_tree()
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
            lines = ["**Wiki stats:**", f"- Страниц: {total}",
                     f"- Проектов: {len(projects)}", f"- Открытых конфликтов: {open_c}"]
            for proj, pages in sorted(tree["projects"].items()):
                lines.append(f"- {proj}: {len(pages)} страниц")
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

    # ── Crystallization ──────────────────────────────────────────

    def crystallize_session(self, session: ChatSession) -> str | None:
        user_msgs = [m for m in session.messages if m.role == "user"]
        if len(user_msgs) < 3:
            return None

        session_text = "\n\n".join(
            f"{m.role.upper()}: {m.content}" for m in session.messages
        )
        today = date.today().isoformat()

        raw = self.llm.call(
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

            self.fs.write_page(slug=slug, meta=meta, content=content)
            return slug
        except Exception:
            return None

    # ── Helpers ──────────────────────────────────────────────────

    def _classify(self, question: str) -> tuple[str, list[str]]:
        raw = self.llm.call(
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

    def _expand_query(self, question: str) -> list[str]:
        """
        Deterministic query expansion: generate additional search terms
        without calling LLM. Uses keyword extraction, common synonyms,
        and project detection.

        Returns list of expanded query strings to try.
        """
        expansions = [question]

        # Extract key terms (nouns, technical terms)
        words = question.lower().split()
        stop_words = {
            "что", "как", "где", "когда", "почему", "зачем", "какой",
            "какие", "кто", "чем", "ли", "или", "и", "в", "на", "с",
            "по", "для", "из", "у", "к", "о", "а", "но", "не",
            "what", "how", "where", "when", "why", "which", "who",
            "is", "are", "does", "do", "the", "a", "an", "in", "on",
            "of", "for", "to", "with", "from", "about",
        }
        key_terms = [w.strip("?.,;:!\"'()") for w in words if w.lower() not in stop_words and len(w) > 2]

        if key_terms:
            expansions.append(" ".join(key_terms))

        # Common technical term expansions
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

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for q in expansions:
            if q not in seen:
                seen.add(q)
                unique.append(q)
        return unique

    def _retrieve_pages(
        self,
        keywords: list[str],
        project: str | None,
        top_k: int = 20,
    ) -> list[WikiPage]:
        query = " ".join(keywords)

        # Try expanded queries if initial search is weak
        results = self.fs.search_pages_weighted(query, project=project, top_k=top_k)

        if len(results) < 3:
            expansions = self._expand_query(query)
            for expanded in expansions[1:]:
                more = self.fs.search_pages_weighted(expanded, project=project, top_k=top_k)
                for r in more:
                    if r["slug"] not in {x["slug"] for x in results}:
                        results.append(r)
                if len(results) >= 3:
                    break

        slugs = [r["slug"] for r in results[:top_k]]
        pages = []
        for slug in slugs:
            page = self.fs.read_page(slug)
            if page:
                pages.append(page)
        return pages

    def _build_wiki_context(self, pages: list[WikiPage]) -> str:
        parts = [p.raw for p in pages]
        fitted = self.budget.fit_wiki_pages(parts)
        return "\n\n---\n\n".join(fitted)

    def _generate_answer(
        self,
        question: str,
        wiki_context: str,
        session: ChatSession,
    ) -> str:
        skills = self._skills_query_rules()
        agents_md = self._read_agents_md()
        history = session.to_llm_messages(self.budget.history)

        system = ANSWER_SYSTEM.format(skills_query_rules=skills or "None.")
        if agents_md:
            system = agents_md + "\n\n" + system

        messages = [*history, {
            "role": "user",
            "content": ANSWER_PROMPT.format(
                question=question,
                wiki_context=self.budget.trim(wiki_context, "wiki_context"),
            )
        }]
        return self.llm.call(
            system=system,
            prompt=messages[-1]["content"],
            temperature=0.2,
        )

    def _skills_query_rules(self) -> str:
        skills = self.fs.read_skills()
        match = re.search(
            r"## Query Formatting Rules\n(.*?)(?=\n## |\Z)", skills, re.DOTALL
        )
        return match.group(1).strip() if match else ""

    def _read_agents_md(self) -> str:
        path = self.fs.root / "AGENTS.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""
