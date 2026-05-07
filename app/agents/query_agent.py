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
from dataclasses import dataclass, field
from datetime import date

from app.config import Settings
from app.core.interpreter import CodeInterpreter
from app.core.llm_client import LLMClient
from app.core.token_budget import ContextBudget
from app.core.utils import extract_wikilinks, now_iso, parse_json_block
from app.core.wiki_fs import WikiFS, WikiPage

logger = logging.getLogger("wiki.query")


# ─────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────

QUESTION_TYPES = {"factual", "comparison", "exploratory", "meta"}


@dataclass
class ChatMessage:
    role: str        # "user" | "assistant" | "tool"
    content: str
    timestamp: str = ""
    cited_slugs: list[str] = field(default_factory=list)
    # slugs mentioned in this message as [[...]] links


@dataclass
class ChatSession:
    session_id: str
    created_at: str
    messages: list[ChatMessage] = field(default_factory=list)
    project_filter: str | None = None
    # None = search all projects

    def to_llm_messages(self, budget_chars: int) -> list[dict]:
        """
        Convert history to OpenAI message format.
        Trims oldest messages to fit budget.
        """
        result = []
        total = 0
        # Iterate newest first, keep what fits
        for msg in reversed(self.messages):
            if msg.role == "tool":
                continue  # don't send tool internals to chat history
            entry = {"role": msg.role, "content": msg.content}
            total += len(msg.content)
            if total > budget_chars:
                break
            result.insert(0, entry)
        return result


@dataclass
class QueryResult:
    answer: str
    cited_slugs: list[str]       # [[slug]] references in answer
    question_type: str
    pages_read: list[str]        # all slugs fetched during query
    iterations: int              # ReAct iterations used (1 for fast paths)
    session_id: str
    crystallized_slug: str | None = None


# ─────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────

CLASSIFY_PROMPT = """Classify this question into exactly one type:
- factual: single specific fact, date, version, configuration value
- comparison: comparing two things, projects, approaches, or implementations
- exploratory: open-ended, requires exploring multiple topics
- meta: question about wiki structure, stats, file counts, projects list

Question: {question}

Output JSON: {{"type": "factual"|"comparison"|"exploratory"|"meta",
               "reasoning": str,
               "keywords": [str]}}
"""

ANSWER_SYSTEM = """You are a wiki assistant. Answer questions using ONLY
the wiki pages provided. Do not use external knowledge.

LANGUAGE RULE (BINDING):
- Answer in Russian.
- Keep technical terms, product names, acronyms, and code in English.
- Use Russian for explanations and prose.

Rules (from skills.md — BINDING):
{skills_query_rules}

Citation rules (ALWAYS follow):
- Cite every fact with [[slug]] immediately after the sentence
- Format: "Redis используется для кеширования [[myapp/storage/redis]]."
- If multiple pages support a fact: "...[[slug1]] [[slug2]]"
- If projects differ: show both — label as "**ProjectA:** ..." and "**ProjectB:** ..."
- Never pick a winner between different project implementations
- If answer not found in wiki: say explicitly "Не найдено в wiki."
  Do NOT invent facts.
"""

ANSWER_PROMPT = """## Question
{question}

## Relevant Wiki Pages
{wiki_context}

## Answer the question. Use [[slug]] citations for every fact."""

REACT_SYSTEM = """You are a wiki research agent with tools.
Think step by step. Use tools to explore wiki until you can answer.
Stop when you have enough information.

LANGUAGE RULE (BINDING):
- Final answer MUST be in Russian.
- Keep technical terms, product names, acronyms in English.

Available tools:
- search_wiki: {{"query": str, "project": str|null}}
  → returns list of matching page slugs and excerpts
- read_page: {{"slug": str}}
  → returns full page content
- execute_code: {{"code": str, "reasoning": str}}
  → runs Python in sandbox (WIKI_ROOT variable available)

Output format for tool call:
{{"action": "search_wiki"|"read_page"|"execute_code", "input": {{...}}}}

Output format for final answer:
{{"action": "answer", "content": str}}

Rules:
- Max {max_iterations} iterations
- Always cite [[slug]] in final answer
- If info not found after {max_iterations} iterations: say so explicitly
"""

REACT_PROMPT = """## Question
{question}

## Conversation so far
{history}

## What have you done so far this query
{scratchpad}

## Next action (JSON):"""

CRYSTALLIZE_PROMPT = """Summarize this Q&A session as a wiki page.
The page captures what was asked, what was found, and key insights.

LANGUAGE RULE (BINDING):
- Write the wiki page content in Russian.
- Keep technical terms, product names, acronyms in English.

Session:
{session_text}

Output JSON page:
{{"meta": {{"title": str, "project": "_general", "type": "concept",
            "tags": ["qa-session", ...],
            "confidence": 0.9, "sources": int,
            "last_confirmed": "{today}",
            "supersedes": null, "superseded_by": null,
            "created": "{today}"}},
 "content": str}}

Keep content under 3500 chars. Focus on reusable insights.
"""


# ─────────────────────────────────────────────
# QueryAgent
# ─────────────────────────────────────────────

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
        llm: LLMClient,
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

        # Append to session
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
        """
        Streaming version for SSE. Yields text chunks.
        Yields special markers for UI:
          [CITED:slug]  — page reference detected
          [DONE]        — stream complete
        """
        # Meta: instant response
        if self._is_meta_question(question):
            result = self._handle_meta(question, session)
            yield result.answer
            yield "[DONE]"
            return

        question_type, keywords = self._classify(question)

        if question_type in ("factual", "comparison"):
            pages_read = self._retrieve_pages(keywords, session.project_filter)
            wiki_context = self._build_wiki_context(pages_read)
            skills = self._skills_query_rules()
            agents_md = self._read_agents_md()

            history = session.to_llm_messages(self.budget.history)
            messages = history + [{"role": "user", "content": question}]

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

        else:  # exploratory — ReAct doesn't stream mid-loop, yields at end
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
        """Fast path: grep → read → answer. 2 LLM calls total."""
        pages = self._retrieve_pages(keywords, session.project_filter, top_k=4)
        wiki_context = self._build_wiki_context(pages)

        answer = self._generate_answer(question, wiki_context, session)
        return answer, [p.slug for p in pages], 1

    # ── Policy: comparison ───────────────────────────────────────

    def _policy_comparison(
        self,
        question: str,
        keywords: list[str],
        session: ChatSession,
    ) -> tuple[str, list[str], int]:
        """
        Multi-project comparison: retrieve from all relevant projects,
        then synthesize side-by-side.
        """
        # Retrieve without project filter to get all perspectives
        pages = self._retrieve_pages(keywords, project=None, top_k=6)

        # Group by project
        by_project: dict[str, list[WikiPage]] = {}
        for p in pages:
            by_project.setdefault(p.project, []).append(p)

        # Build context with project labels
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
                    preview = page.raw[:1500]
                    scratchpad.append(
                        f"[iter {iteration+1}] read_page({slug!r}) → {preview}"
                    )
                else:
                    scratchpad.append(
                        f"[iter {iteration+1}] read_page({slug!r}) → NOT FOUND"
                    )

            elif action == "execute_code":
                code = inp.get("code", "")
                reasoning = inp.get("reasoning", "")
                output = self.interpreter.execute(code)
                result_str = json.dumps(output, ensure_ascii=False)[:800]
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
        """Answer meta questions directly from WikiFS without LLM."""
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
        """
        Distill Q&A session into a wiki concept page.
        Returns slug of created page, or None if session too short.
        Minimum: 3 exchanges before crystallization makes sense.
        """
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

            # Slug: queries/<session_id_short>
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

    def _retrieve_pages(
        self,
        keywords: list[str],
        project: str | None,
        top_k: int = 5,
    ) -> list[WikiPage]:
        query = " ".join(keywords)
        results = self.fs.search_pages(query, project=project)
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

        messages = history + [{
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