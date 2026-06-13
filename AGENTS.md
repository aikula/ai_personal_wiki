# CLAUDE.md

```markdown
# CLAUDE.md — Wiki Engine: Agent Instructions

## Project Overview
Wiki Engine is a Python application that ingests Markdown documents,
builds and maintains a structured wiki, detects conflicts, accumulates
skills, and answers questions via LLM. All storage is plain text files.
No databases. No vector embeddings in Phase 1.

## Repository Layout
```
wiki-engine/
├── app/
│   ├── agents/
│   │   ├── ingest_agent.py       # Plan-and-Execute ingest pipeline
│   │   ├── ingest_helpers.py     # Standalone ingest helper functions
│   │   ├── ingest_prompts.py     # Ingest prompt templates
│   │   ├── ingest_types.py       # Ingest type definitions
│   │   ├── query_agent.py        # Policy-driven ReAct query agent
│   │   ├── query_search.py       # Standalone query search/retrieval helpers
│   │   ├── query_prompts.py      # Query prompt templates
│   │   ├── query_types.py        # Query type definitions
│   │   └── audit_agent.py        # Parallel structural audit
│   ├── core/
│   │   ├── wiki_fs.py            # All filesystem operations (single source of truth)
│   │   ├── linter.py             # WikiLinter: structural checks
│   │   ├── interpreter.py        # Sandboxed Python code interpreter
│   │   ├── llm_client.py         # OpenAI-compatible client wrapper
│   │   ├── token_budget.py       # Symbol/token counting utilities
│   │   ├── large_source_ingest.py # Outline parser, chunking, merge analysis
│   │   ├── safe_page_updates.py  # Typed page operations with diff generation
│   │   └── search_provider.py    # Abstract search interface (weighted/BM25)
│   ├── api/
│   │   ├── main.py               # FastAPI app entrypoint
│   │   ├── routes/
│   │   │   ├── ingest.py         # POST /ingest, POST /rebuild
│   │   │   ├── chat.py           # POST /chat, GET /chat/history
│   │   │   ├── wiki.py           # GET /wiki/tree, GET /wiki/page
│   │   │   ├── conflicts.py      # GET/POST /conflicts
│   │   │   └── settings.py       # GET/POST /settings
│   │   └── models.py             # Pydantic request/response models
│   ├── ui/
│   │   ├── index.html            # Single-file React app (no build step)
│   │   └── assets/
├── wiki-data/                    # MOUNTED VOLUME — never commit
│   ├── raw/                      # Source documents
│   │   ├── _general/             # Cross-project documents
│   │   └── <project_name>/       # Per-project documents
│   ├── wiki/
│   │   ├── index.md              # L0 navigation index
│   │   ├── log.md                # Rolling change log
│   │   ├── _general/             # Entities/concepts from general raw
│   │   ├── _sources/             # Source Cards (Phase 1B+)
│   │   ├── _claims/              # Individual claims (Phase 3)
│   │   └── <project_name>/       # Per-project wiki pages
│   ├── conflicts.md              # Conflict queue (unresolved + resolved)
│   ├── skills.md                 # Accumulated rules and patterns
│   └── AGENTS.md                 # Domain-specific LLM instructions (editable)
├── config/
│   └── settings.yaml             # LLM URL, model, limits, thresholds
├── tests/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml                # Ruff config + dependencies
└── CLAUDE.md                     # This file
```

## Core Principles (NEVER VIOLATE)

### 1. Filesystem is the database
- ALL wiki state lives in `wiki-data/` as plain `.md` files
- No SQLite, no JSON state files, no pickle — only Markdown
- Every operation that changes wiki MUST go through `wiki_fs.py`
- `wiki_fs.py` is the ONLY module allowed to write to `wiki-data/`

### 2. File size limits (in CHARACTERS, not tokens; 1 token ≈ 3.5 chars)
```
AGENTS.md:          max  4_200 chars  (~1200 tokens)
skills.md:          max  8_750 chars  (~2500 tokens)
index.md (L0):      max 10_500 chars  (~3000 tokens)
domain index (L1):  max  5_250 chars  (~1500 tokens)
entity page:        max 10_000 chars  (~2850 tokens)
concept page:       max 14_000 chars  (~4000 tokens)
log.md:             max  3_500 chars  (rotate when exceeded)
conflicts.md:       max 35_000 chars  (archive resolved when exceeded)
```
If an agent operation would exceed these limits, it MUST split or
rotate BEFORE writing. Splitting must be semantic (find natural
boundary), never mechanical (never split mid-sentence or mid-section).

### Token Metering (Multi-user mode)
- Billing: output tokens only (input logged but not charged)
- Reasoning models (o1, o3, r1, deepseek-r): 1.5x billing multiplier
- Per-call logging: model, input/output tokens, billed amount, reasoning flag
- Daily token budget: 30,000 output tokens
- Welcome tokens: configurable per user (default 200K)
- reasoning_model_budget: 500,000 reserved

### 3. Every wiki page has frontmatter
Required fields — agent MUST include all of them:
```yaml
***
title: <human readable title>
project: <project_name | _general>
type: <entity | concept | index | log | source>
tags: [tag1, tag2]
confidence: <0.0–1.0>
sources: <int, number of source files that contributed>
last_confirmed: <YYYY-MM-DD>
supersedes: null
superseded_by: null
created: <YYYY-MM-DD>
***
```

### 3.1. New namespaces (Phase 1B+)
```
wiki/_sources/<project>/<source-slug>.md   — Source Cards with SHA256 + drift
wiki/_claims/<project>/<source-slug>/      — Individual claims per chunk
```

### 4. WikiLinks are the navigation layer
- Internal links MUST use `[[slug]]` format, e.g. `[[projectA/deploy]]`
- Slug = path relative to `wiki/` without `.md` extension
- Display name override: `[[slug|Display Text]]`
- Anchors: `[[slug#heading-anchor]]`
- Agent MUST update `index.md` after EVERY page create or delete

### 5. Agent output is always structured
Every agent method returns a typed dataclass or Pydantic model.
Raw LLM string output is NEVER passed directly to the filesystem.
Parse → validate → write. Always.

### 6. Skills.md governs agent behavior
Before EVERY ingest or query operation, agent reads `skills.md`.
Skills are binding rules, not suggestions.
New skill is appended ONLY after human resolves a conflict or
explicitly approves a skill suggestion.

### 7. Two-step ingest (NEVER skip step 1)
Step 1 — Analysis pass: read source, identify entities/concepts,
         find potential conflicts with existing wiki, plan pages to
         create/update. Output: AnalysisResult dataclass.
Step 2 — Generation pass: use AnalysisResult to write pages.
         Never write pages based on raw source directly.
         Chunk overlap: 750 chars between consecutive chunks.

### 8. Conflicts are opportunities, not errors
When ingest detects a conflict:
- Write to conflicts.md with full context
- Continue ingest of non-conflicting content
- DO NOT block or abort ingest on conflict
- DO NOT auto-resolve conflicts without human approval

### 9. Code Interpreter is sandboxed
Allowed imports: re, json, pathlib, datetime, collections,
                 itertools, difflib, functools, textwrap,
                 rapidfuzz, frontmatter, yaml
Forbidden: os.system, subprocess, requests, httpx, socket,
           open() with write mode (use wiki_fs.py instead),
           __import__, eval, exec (within interpreted code)
Execution timeout: 10 seconds hard limit.

### 10. Project isolation in wiki
- Pages for project "myapp": `wiki/myapp/*.md`
- Pages for general: `wiki/_general/*.md`  
- Cross-project pages (comparing two projects): `wiki/_general/`
  with frontmatter `project: _general` and tags for both projects
- Conflict between projects = NOT a conflict, mark as
  `type: cross_project_difference` in conflicts.md

## LLM Call Conventions
All LLM calls use `llm_client.py`. Never call OpenAI SDK directly
from agents. System prompt always contains:
1. Relevant section of AGENTS.md
2. Current skills.md (full)
3. Task-specific instructions

Max context budget per call (in characters):
```
AGENTS.md section:    4_200
skills.md:            8_750
wiki context:        21_000
task instructions:    3_500
user input:           3_500
history:              7_000
─────────────────────────────
Total budget:        ~48_000  (≈13700 tokens, fits any modern model)
```

## Error Handling Policy
- FileNotFoundError on wiki page → log warning, continue
- LLM returns malformed JSON → retry once with explicit format reminder
- LLM retry fails → raise WikiEngineError with full context for API layer
- Ingest step fails → write to log.md what was processed, what failed
- NEVER silently swallow exceptions in agent code

## Testing Requirements
- Every `wiki_fs.py` method: unit test with tmp_path fixture
- Every agent: integration test with mock LLM (fixture returns valid JSON)
- WikiLinter: test each rule type with crafted markdown fixtures
- CodeInterpreter: test allowed and forbidden imports, timeout
- API routes: test with httpx AsyncClient

## What NOT to do
- Do NOT add a database "just for this feature"
- Do NOT store state in global variables between requests
- Do NOT call LLM for operations that can be done with regex/pathlib
- Do NOT let any page grow beyond its character limit silently
- Do NOT merge project-specific content into _general automatically
- Do NOT resolve conflicts without writing to conflicts.md first
- Do NOT skip frontmatter on any new page
```

***

## Структура данных: ключевые файлы

### `wiki-data/conflicts.md` — формат

```markdown
# Conflicts

## [OPEN] CONFLICT-001
- **Date:** 2026-04-29
- **Project:** myapp
- **Source file:** raw/myapp/deploy_guide.md
- **Conflict type:** factual_contradiction
- **Page A:** [[myapp/infrastructure/redis]] — "Redis 7.2 used for caching"
- **Page B:** raw/myapp/deploy_guide.md — "PostgreSQL used for all storage"
- **Context A:** (первые 300 символов страницы wiki)
- **Context B:** (первые 300 символов источника)
- **Suggested options:**
  1. Trust wiki (redis.md is more recent)
  2. Trust source (deploy_guide.md is primary doc)
  3. Both true — different subsystems
- **User comment:** _none_
- **Resolution:** _pending_

---

## [RESOLVED] CONFLICT-000
- **Date:** 2026-04-28
- **Resolution:** option_3 — both true, different subsystems
- **User comment:** "Redis для кеша, Postgres для данных, это правильно"
- **Skill extracted:** "Для myapp: хранилища Redis и PostgreSQL не конкурируют,
  они выполняют разные роли. Не считать их конфликтом."
- **Wiki updated:** [[myapp/infrastructure/redis]], [[myapp/infrastructure/postgres]]
- **Resolved by:** user
- **Resolved at:** 2026-04-28T18:32:00
```

### `wiki-data/skills.md` — формат

```markdown
# Skills

## Source Trust Rules
<!-- Правила приоритета источников -->
- Primary docs (README, official guides) override secondary (blog posts, notes)
- For myapp: deploy_guide.md is authoritative for infrastructure decisions

## Conflict Resolution Patterns
<!-- Паттерны решения конфликтов -->
- myapp: Redis и PostgreSQL — разные роли, не конфликт
- Version numbers: use the highest confirmed version unless explicitly downgraded

## Domain Conventions
<!-- Соглашения по именованию и структуре -->
- Entity names: use exact casing from official docs (e.g. "FastAPI", not "fastapi")
- Do not merge config examples from different projects into one page

## Query Formatting Rules
<!-- Как формировать ответы -->
- Always cite wiki pages as [[slug]] in answers
- When projects differ: show both implementations side-by-side, do not pick winner
- Cross-project differences: label clearly as "ProjectA approach" / "ProjectB approach"

## Ingest Patterns
<!-- Чему научились при ingest -->
- (empty — populated as conflicts are resolved)
```

### `wiki-data/wiki/index.md` — формат L0

```markdown
---
title: Wiki Index
project: _general
type: index
tags: []
confidence: 1.0
sources: 0
last_confirmed: 2026-04-29
supersedes: null
superseded_by: null
created: 2026-04-29
---

# Wiki Index

Last updated: 2026-04-29T16:00:00
Pages: 24 | Projects: 2 | Open conflicts: 1

## _general (4 pages)
[[_general/index]] — общие концепции и паттерны

## myapp (12 pages)
[[myapp/index]] — документация проекта MyApp

## otherapp (8 pages)
[[otherapp/index]] — документация проекта OtherApp
```

***

## `config/settings.yaml`

```yaml
llm:
  base_url: "https://api.openai.com/v1"   # любой OpenAI-совместимый endpoint
  api_key: "${OPENAI_API_KEY}"             # из env
  model: "gpt-4o"
  temperature: 0.1                         # низкая для детерминизма при ingest
  timeout_seconds: 60
  context_window_tokens: 0                 # 0 = auto-detect from model
  max_completion_tokens: 16000             # output-only billing with reasoning model budget

limits:
  agents_md_chars: 4200
  skills_md_chars: 8750
  index_l0_chars: 10500
  index_l1_chars: 5250
  entity_page_chars: 10000
  concept_page_chars: 14000
  log_md_chars: 3500
  conflicts_md_chars: 35000

ingest:
  two_step: true
  max_pages_per_source: 10        # защита от взрыва страниц
  auto_lint_after_ingest: true
  conflict_continue_on_detect: true
  max_completion_tokens: 8000
  chunk_overlap_chars: 750
  max_auto_write_pages: 150
  require_review_if_pages_gt: 150

query:
  context_budget_chars: 35000
  max_wiki_pages_in_context: 6
  history_budget_chars: 10000

audit:
  confidence_warn_threshold: 0.4
  stale_days_threshold: 90
  run_llm_audit_default: false    # дорого, только явно

ui:
  host: "0.0.0.0"
  port: 8000
  wiki_data_path: "/wiki-data"    # в Docker; локально: ./wiki-data
```

### Поддерживаемые форматы файлов для загрузки

Система поддерживает загрузку следующих типов файлов из каталога `raw/`:
- **Markdown** (`.md`) - основной формат для документации
- **Текстовые файлы** (`.txt`) - простые текстовые документы
- **Python файлы** (`.py`) - код и комментарии
- **PDF документы** (`.pdf`) - извлечение текста через mrkitdown
- **Word документы** (`.docx`) - извлечение текста через mrkitdown
- **PowerPoint презентации** (`.pptx`) - извлечение текста через mrkitdown

При загрузке файлов форматов `.pdf`, `.docx`, `.pptx` используется библиотека mrkitdown для извлечения текстового содержимого, которое затем обрабатывается как обычный текстовый источник.

### Локализация и язык интерфейса

Система поддерживает многоязычный интерфейс через переменную окружения `LANGUAGE`:
- По умолчанию: `ru` (русский)
- Можно установить в любой код языка (например, `en` для английского)
- Настройка выполняется через:
  - Переменную окружения `LANGUAGE`
  - Поле `language` в `config/settings.yaml`
  - API endpoint `GET /api/settings/language`

Все пользовательские интерфейсы, сообщения об ошибках и подсказки переводятся на выбранный язык.

### Рендеринг provenance-маркеров

При загрузке документов система автоматически добавляет provenance-маркеры вида `^[raw/source.md]` после важных factual claims. Эти маркеры:
- Отображаются как надстрочные ссылки с подсказкой showing source file path
- При нажатии открывают исходный файл в режиме просмотра raw markdown
- Проверяются линтером на существование参照ного raw-файла
- Используются для отслеживания происхождения информации в вики
```

# 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. KISS & DRY

**Keep It Simple, Stupid. Don't Repeat Yourself.**

- Every piece of knowledge and logic must have a single, unambiguous representation in the system
- If you write the same pattern twice, extract it on the third occurrence (Rule of Three)
- No copy-pasted code. Ever. Shared logic goes into `app/core/utils.py`
- Favor flat structure over nested: prefer early returns, guard clauses, and linear flow
- A function should do one thing. If it has "and" in its name, split it.
- Before adding a new dependency or abstraction, ask: "Can I do this with stdlib in 10 lines?"

## Deployment

**Production:** `docker compose -f docker-compose-prod.yml up -d`
- Использует traefik (`tghub-network`), порт 8000 не торчит наружу
- Домен: `https://ai-wiki.kulinich.ru`
- После `docker compose down` всегда стартовать prod-файл, а не dev

**Dev:** `docker compose up -d`
- Пробрасывает порт `${APP_PORT:-8000}:8000`
- Не подключён к traefik, для локальной разработки

## Session Snapshots

Состояние после каждой сессии фиксируется в `docs/session-YYYY-MM-DD.md`:
- что сделано, какие файлы изменены
- открытые проблемы и findinds
- план на следующий раз

## 6. File Size Limit

**No file should exceed 500 lines without a justified exception.**

Exceptions (must be documented here):
- `app/ui/index.html` (3012+ lines) — single-file React app, cannot split by design
- `app/core/wiki_fs.py` (2505+ lines) — single source of truth for all filesystem ops
- `app/agents/ingest_agent.py` (1120+ lines) — complex ingest pipeline logic; was split once (761→449) but grew back; needs another split

All other files MUST stay under 500 lines. When modifying a file that exceeds the limit, prioritize splitting over adding code.

Resolved:
- `app/agents/query_agent.py` 729→424 lines — split into `query_types.py` (51), `query_prompts.py` (114), `query_search.py` (381), `query_agent.py` (424)
