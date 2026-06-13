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
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator

from app.agents.query_prompts import (
    ANSWER_PROMPT,
    ANSWER_SYSTEM,
    REACT_PROMPT,
    REACT_SYSTEM,
)
from app.agents.query_search import (
    build_wiki_context,
    classify_question,
    compress_session_history,
    crystallize_session,
    format_claims_for_context,
    generate_answer,
    handle_meta,
    index_first_retrieve,
    is_meta_question,
    read_agents_md,
    read_skills_rules,
    retrieve_claims,
    retrieve_pages,
)
from app.agents.query_types import ChatMessage, ChatSession, QueryResult
from app.config import Settings
from app.core.interpreter import CodeInterpreter
from app.core.llm_client import LLMGateway
from app.core.token_budget import ContextBudget
from app.core.utils import (
    extract_wikilinks,
    now_iso,
    parse_json_block,
    strip_trailing_json_artifact,
)
from app.core.wiki_fs import WikiFS

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

    def run(self, question: str, session: ChatSession) -> QueryResult:
        if is_meta_question(question):
            return handle_meta(self.fs, question, session)

        compress_session_history(self.llm, self.budget, session)
        question_type, keywords = classify_question(self.llm, question)
        logger.info("Query: type=%s question=%s", question_type, question[:100])

        if question_type == "factual":
            answer, slugs, iters = self._policy_factual(question, keywords, session)
        elif question_type == "comparison":
            answer, slugs, iters = self._policy_comparison(question, keywords, session)
        else:
            answer, slugs, iters = self._policy_react(question, session)

        answer = strip_trailing_json_artifact(answer)
        cited = extract_wikilinks(answer)

        session.messages.append(ChatMessage(
            role="user", content=question, timestamp=now_iso(),
        ))
        session.messages.append(ChatMessage(
            role="assistant", content=answer, timestamp=now_iso(), cited_slugs=cited,
        ))

        return QueryResult(
            answer=answer, cited_slugs=cited, question_type=question_type,
            pages_read=slugs, iterations=iters, session_id=session.session_id,
        )

    # ── Public: streaming ────────────────────────────────────────

    def stream(
        self, question: str, session: ChatSession,
    ) -> Generator[str, None, None]:
        if is_meta_question(question):
            result = handle_meta(self.fs, question, session)
            yield strip_trailing_json_artifact(result.answer)
            yield "[DONE]"
            return

        compress_session_history(self.llm, self.budget, session)
        question_type, keywords = classify_question(self.llm, question)

        if question_type in ("factual", "comparison"):
            pages = retrieve_pages(self.fs, keywords, session.project_filter, top_k=20)
            wiki_context = build_wiki_context(self.budget, pages)

            claims = retrieve_claims(self.fs, question, session.project_filter, top_k=5)
            wiki_context += format_claims_for_context(claims)

            skills = read_skills_rules(self.fs)
            agents_md = read_agents_md(self.fs)

            history = session.to_llm_messages(self.budget.history)
            messages = [*history, {"role": "user", "content": question}]

            system = ANSWER_SYSTEM.format(skills_query_rules=skills or "None.")
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

            cleaned = strip_trailing_json_artifact(full_answer)
            if cleaned != full_answer:
                yield f"[REPLACE:{json.dumps(cleaned, ensure_ascii=False)}]"
                full_answer = cleaned

            cited = extract_wikilinks(full_answer)
            for slug in cited:
                yield f"[CITED:{slug}]"

            session.messages.append(ChatMessage(
                role="user", content=question, timestamp=now_iso(),
            ))
            session.messages.append(ChatMessage(
                role="assistant", content=full_answer,
                timestamp=now_iso(), cited_slugs=cited,
            ))
        else:
            answer, _, _ = self._policy_react(question, session)
            answer = strip_trailing_json_artifact(answer)
            yield answer
            for slug in extract_wikilinks(answer):
                yield f"[CITED:{slug}]"
            session.messages.append(ChatMessage(
                role="user", content=question, timestamp=now_iso(),
            ))
            session.messages.append(ChatMessage(
                role="assistant", content=answer, timestamp=now_iso(),
            ))

        yield "[DONE]"

    # ── Policy: factual ──────────────────────────────────────────

    def _policy_factual(
        self, question: str, keywords: list[str], session: ChatSession,
    ) -> tuple[str, list[str], int]:
        pages = retrieve_pages(self.fs, keywords, session.project_filter, top_k=20)

        index_slugs = index_first_retrieve(self.fs, question, session.project_filter)
        if index_slugs:
            index_set = set(index_slugs)
            priority = [p for p in pages if p.slug in index_set]
            rest = [p for p in pages if p.slug not in index_set]
            pages = priority + rest

        selected = []
        for page in pages[:10]:
            outline = self.fs.read_page_outline(page.slug)
            if outline:
                kw_lower = [k.lower() for k in keywords]
                heading_match = any(
                    any(kw in h["text"].lower() for kw in kw_lower)
                    for h in outline.headings
                )
                synopsis_match = any(
                    kw in outline.synopsis.lower() for kw in kw_lower
                )
                if heading_match or synopsis_match:
                    selected.append(page)
                elif len(selected) < 3:
                    selected.append(page)
            else:
                selected.append(page)

        if not selected:
            selected = pages[:4]

        wiki_context = build_wiki_context(self.budget, selected)

        claims = retrieve_claims(self.fs, question, session.project_filter, top_k=5)
        wiki_context += format_claims_for_context(claims)

        answer = generate_answer(self.llm, self.fs, self.budget, question, wiki_context, session)
        return answer, [p.slug for p in selected], 1

    # ── Policy: comparison ───────────────────────────────────────

    def _policy_comparison(
        self, question: str, keywords: list[str], session: ChatSession,
    ) -> tuple[str, list[str], int]:
        pages = retrieve_pages(self.fs, keywords, project=None, top_k=20)

        by_project: dict[str, list] = {}
        for p in pages:
            by_project.setdefault(p.project, []).append(p)

        sections = []
        for proj, proj_pages in sorted(by_project.items()):
            index_slugs = index_first_retrieve(self.fs, question, proj)
            if index_slugs:
                index_set = set(index_slugs)
                priority = [p for p in proj_pages if p.slug in index_set]
                rest = [p for p in proj_pages if p.slug not in index_set]
                proj_pages = priority + rest

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

        claims = retrieve_claims(self.fs, question, None, top_k=8)
        wiki_context += format_claims_for_context(claims)

        answer = generate_answer(self.llm, self.fs, self.budget, question, wiki_context, session)
        return answer, [p.slug for p in pages], 1

    # ── Policy: ReAct ────────────────────────────────────────────

    def _policy_react(
        self, question: str, session: ChatSession,
    ) -> tuple[str, list[str], int]:
        skills = self.fs.read_skills()
        history_msgs = session.to_llm_messages(self.budget.history)
        history_text = "\n".join(
            f"{m['role'].upper()}: {m['content'][:200]}" for m in history_msgs
        )

        agents_md = read_agents_md(self.fs)
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
                return strip_trailing_json_artifact(raw), pages_read, iteration + 1

            action = action_data.get("action")
            inp = action_data.get("input", {})

            if action == "answer":
                answer = strip_trailing_json_artifact(action_data.get("content", ""))
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

            elif action == "search_claims":
                query = inp.get("query", "")
                project = inp.get("project") or session.project_filter
                claims = retrieve_claims(self.fs, query, project, top_k=5)
                if claims:
                    lines = [f"Found {len(claims)} claims:"]
                    for c in claims:
                        slug = c.file_path.replace(".md", "")
                        lines.append(f"  - {c.normalized} [[{slug}]]")
                    tool_result = "\n".join(lines)
                else:
                    tool_result = "No claims found."
                scratchpad.append(
                    f"[iter {iteration+1}] search_claims({query!r}) → {tool_result}"
                )

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
                    wikilinks = extract_wikilinks(page.raw)[:10]
                    if wikilinks:
                        preview += (
                            "\nRelated wikilinks: "
                            + ", ".join(f"[[{wl}]]" for wl in wikilinks)
                        )
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
        answer = strip_trailing_json_artifact(answer)
        logger.info("ReAct max_iterations: pages=%d", len(pages_read))
        return answer, pages_read, self.max_iterations

    # ── Crystallization ──────────────────────────────────────────

    def crystallize_session(self, session: ChatSession) -> str | None:
        return crystallize_session(self.llm, self.fs, session)
