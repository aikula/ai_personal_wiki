# Large-Scale Wiki Engine Specification
Дата: 2026-05-19

Статус: рабочая спецификация для реализации агентом.

## 1. Цель

Сохранить plain-text/filesystem-first архитектуру Wiki Engine и убрать
ограничение "wiki помещается в один context window".

Система должна:

- ingest-ить источники до 1 млн символов без silent truncation;
- отвечать по wiki на 200+ страниц, читая нужные секции, а не первые preview;
- сохранять provenance до raw-файла, секции и extracted claim;
- обнаруживать drift источников через sha256;
- поддерживать универсальную wiki: ПО, инструкции, техника, правила, положения.

Не цели текущего этапа:

- не добавлять базу данных;
- не добавлять vector embeddings в обязательный path;
- не делать автоматическое разрешение semantic conflicts;
- не crystallize ответы обратно в wiki, но историю вопросов/ответов хранить.

## 2. Принципы

### 2.1 Context window не является границей знания

Context window ограничивает только одну LLM-операцию. Он не должен ограничивать
размер raw source, wiki или ответа.

Нормальный ingest больших документов:

```text
raw source -> source identity -> outline -> chunks -> chunk analysis
  -> claims -> merged analysis -> triage report -> page generation/update
```

### 2.2 Filesystem остается database

Все состояние хранится в Markdown внутри `wiki-data/`. Запись в `wiki-data/`
идет только через `app/core/wiki_fs.py`.

Новые namespace:

```text
wiki/_sources/<project>/<source-slug>.md
wiki/_claims/<project>/<source-slug>.md
wiki/_claims/<project>/<source-slug>/chunk-001.md
```

Для больших источников claims сразу дробятся по chunk-файлам, чтобы не нарушать
лимиты размера.

### 2.3 Все операции typed и проверяемые

LLM output никогда не пишется напрямую:

```text
LLM string -> parse -> Pydantic/dataclass -> validate -> wiki_fs write
```

Malformed JSON: один retry с явным format reminder. Повторный сбой:
`WikiEngineError` с контекстом для API layer.

## 3. Runtime Budgets

При старте система пытается получить context window модели через `/v1/models`.

Требования:

- если endpoint возвращает model context limit, использовать его;
- если поле отсутствует/нестандартное, использовать `settings.yaml`;
- применять safety multiplier, по умолчанию `0.70`;
- output budget резервируется отдельно и не съедается input budget;
- все budget decisions логируются в debug/status output.

Конфигурация:

```yaml
llm:
  context_window_chars_fallback: 48000
  context_window_safety_ratio: 0.70
  output_budget_chars: 6000
```

Acceptance:

- неизвестный `/v1/models` не ломает запуск, используется fallback;
- ни один LLM-call не получает prompt больше safe budget;
- превышение budget приводит к chunking/section selection, а не truncation.

### 3.1 Optional VPS Auth

Для запуска на VPS добавляется опциональная защита одним логином и паролем из
`.env`. Это deployment guard, не user management.

Конфигурация:

```text
WIKI_AUTH_ENABLED=true
WIKI_AUTH_USERNAME=admin
WIKI_AUTH_PASSWORD=<password>
```

Архитектура: FastAPI middleware/dependency над UI и `/api/*`; HTTP Basic Auth,
не cookies/sessions; `secrets.compare_digest`; fail fast при enabled auth и
пустых credentials; не логировать пароль и не отдавать его через settings API;
`/health` можно оставить публичным только если он не раскрывает данные.

Ограничения: Basic Auth только за HTTPS/reverse proxy. Пароль в `.env` допустим
для MVP, но `.env` не должен попадать в git.

Acceptance: disabled auth не меняет поведение; enabled без credentials не
стартует; UI/API без valid credentials дают `401`; valid credentials открывают
UI/API; tests покрывают enabled/disabled/missing creds.

## 4. Large Source Ingest

### 4.1 Source identity

Перед анализом source создается/обновляется Source Card:

```text
wiki/_sources/<project>/<source-slug>.md
```

Frontmatter:

```yaml
---
title: Source: deploy_guide.md
project: myapp
type: source
tags: [source, ingest]
confidence: 1.0
sources: 1
last_confirmed: 2026-05-19
supersedes: null
superseded_by: null
created: 2026-05-19
source_id: myapp/deploy_guide
source_path: raw/myapp/deploy_guide.md
source_sha256: "<sha256>"
ingest_status: active
---
```

Source Card содержит outline, ingest status, chunk counters, planned/written
pages, opened conflicts, links to claims files и drift status.

### 4.2 Outline parser и chunking

`parse_outline(source)` строит структуру документа.

Fallback order:

1. Markdown headings: `#`, `##`, `###`, ...
2. Converted document markers: page markers, TOC, numbered headings.
3. Large paragraph groups.
4. Sentence groups.
5. Hard split только как emergency fallback.

Hard split должен не резать code fence, Markdown table или sentence посередине,
если можно отступить к ближайшей естественной границе. Chunk, созданный hard
split, помечается `split_reason: hard_max`.

Конфигурация:

```yaml
ingest:
  large_source_threshold_chars: 100000
  chunk_min_chars: 8000
  chunk_target_chars: 16000
  chunk_max_chars: 25000
```

Env overrides:

```text
WIKI_CHUNK_MIN_CHARS
WIKI_CHUNK_TARGET_CHARS
WIKI_CHUNK_MAX_CHARS
WIKI_LARGE_SOURCE_THRESHOLD_CHARS
```

### 4.3 Chunk analysis

Каждый chunk анализируется отдельно и возвращает typed result:

```yaml
chunk_id: "chunk-001"
source_id: "myapp/deploy_guide"
section_path: ["Deployment", "Redis"]
candidate_pages: []
claims: []
conflicts: []
ignored_sections: []
```

Правила:

- chunk analysis не пишет wiki pages;
- conflicts пишутся в `conflicts.md`, но не блокируют non-conflicting ingest;
- каждый source section получает outcome: page, claim, conflict, ignored или
  failed.

### 4.4 Claims layer

Claims выносятся сразу, не встраиваются в Source Card.

Минимальная модель:

```yaml
claim_id: myapp/deploy_guide#chunk-001-claim-001
source_id: myapp/deploy_guide
source_path: raw/myapp/deploy_guide.md
source_sha256: "<sha256>"
source_section: "## Redis"
quote: "Redis 7.2 is used for session cache"
normalized: "Redis 7.2 используется для session cache."
related_slugs: [myapp/storage/redis]
confidence: 0.92
status: active
```

Statuses:

```text
active | superseded | contradicted | unresolved | ignored
```

Claims нужны для dedupe, conflict detection, source drift analysis, точной
provenance и объяснения, почему факт попал в page.

### 4.5 Merge analysis и page generation

`merge_analysis(chunk_results)` объединяет одинаковые slugs, dedupe-ит claims,
поднимает conflicts на source level, строит triage report и page write plan.

Жесткий `max_pages_per_source=10` удаляется.

Новая конфигурация:

```yaml
ingest:
  max_pages_per_batch: 10
  max_auto_write_pages: 15
  require_review_if_pages_gt: 25
```

Большой источник может создать 40-100 страниц. Это не ошибка, если все страницы
имеют provenance и прошли triage.

Базовый safe path: генерировать валидную новую версию страницы целиком и
сравнивать diff до записи.

Точечные правки разрешены только как structured operations:

```text
replace_section
append_section
update_frontmatter_field
add_provenance_marker
```

Запрещено:

- произвольный LLM patch без typed operation;
- rewrite страницы с потерей frontmatter;
- удаление старых claims без статуса `superseded` или `ignored`;
- semantic conflict resolution без записи в `conflicts.md`.

Acceptance:

- 1M char source полностью обработан без silent truncation;
- Source Card показывает все chunks и их status;
- каждый chunk имеет processed/failed outcome;
- every generated page имеет frontmatter, Wikilinks и provenance;
- claims записаны в `_claims`;
- conflicts не блокируют non-conflicting pages;
- index обновлен после create/delete;
- file size limits не нарушены.

## 5. Query по 200+ страницам

### 5.1 Query flow

```text
question -> classify intent/project -> read L0/L1 index
  -> expand query if needed -> search candidates -> read page outlines
  -> select sections -> multi_read_sections -> answer with citations
```

QueryAgent больше не должен полагаться на первые 1500 символов page preview.

### 5.2 Required tools

`read_page_outline(slug)` возвращает title, summary/synopsis, tags, headings,
short section previews и source/claim references if available.

`read_page_section(slug, heading, char_limit=None)` возвращает полный текст
секции, anchor, provenance markers и section source refs.

`multi_read_sections(requests)` batch-читает выбранные секции.

`search_pages(query, top_k=20)` использует weighted field search.

`expand_query(question)` сначала делает deterministic expansion. LLM expansion
разрешен только если deterministic retrieval слабый или вопрос сложный.

### 5.3 Weighted search без BM25

На первом этапе без новых зависимостей:

```text
score =
  8 * matches_in_title +
  5 * matches_in_tags +
  4 * matches_in_summary +
  3 * matches_in_headings +
  1 * matches_in_body
```

Позже BM25/TF-IDF можно добавить без изменения QueryAgent tool flow.

Acceptance:

- ответ находится в середине/конце длинной страницы;
- wiki на 200+ страниц возвращает релевантные candidates;
- ответ цитирует wiki pages как `[[slug]]`;
- если проекты различаются, ответ показывает side-by-side, не выбирая winner;
- если context не хватает, агент читает дополнительные sections, а не truncates.

## 6. Index Architecture

`index.md` - active navigation map, не только UI-каталог.

Иерархия:

- L0 `wiki/index.md`: проекты, top-level domains, ссылки на L1;
- L1 `wiki/<project>/index.md`: compact catalog проекта;
- L2 category indexes: создаются при превышении лимита L1.

L0 не должен пытаться подробно перечислять все 200+ pages.

L1 entry формат:

```markdown
[[myapp/storage/redis]] - Session cache, Redis 7.2, TTL policy
```

Если index превышает лимит, split по смысловым категориям, не mechanical split.

Acceptance:

- create/delete page обновляет index;
- L0 остается compact;
- L1/L2 позволяют человеку и агенту пройти от проекта к нужной секции;
- index pages не превышают лимиты.

## 7. Typed Relationships

Wikilinks остаются human navigation layer. Typed relationships - optional
machine-readable graph во frontmatter.

Relations не обязательны для всех страниц на первом этапе. Linter может
предупреждать только для page types, где отношения очевидны.

Пример:

```yaml
relations:
  - type: part_of
    target: myapp/equipment/pump
  - type: requires
    target: myapp/procedures/safety-check
  - type: governed_by
    target: myapp/rules/regulation-12
```

Allowed relation types:

```text
part_of | contains | depends_on | related_to | contradicts | supersedes
source_for | governed_by | requires | prohibits | allows | applies_to
procedure_for | mentions
```

Правила:

- missing relation target: linter warning;
- unknown relation type: linter error;
- `related_to` допустим, но не должен заменять более точный тип, когда он ясен;
- semantic relations не используются для auto-resolution conflicts.

## 8. Audit / Self-Healing

Audit становится циклом Detect -> Plan -> Apply.

Detect: broken links, missing source files, source sha drift, duplicate
titles/claims, orphan pages, pages without incoming/outgoing links, stale facts,
unresolved conflicts, invalid relation targets/types, claims without Source
Card, source sections without outcome.

Safe auto fixes: index counts, missing index entry for existing page, stale
generated timestamp, broken link caused by known slug rename, Source Card status
refresh after sha check.

Review required: duplicate page merge, contradiction resolution, deleting a
page, semantic rewrite, marking claim contradicted/superseded unless derived
from approved conflict.

Acceptance: audit report separates safe auto fixes from review-required changes;
no semantic fix is applied silently; source drift is visible without reading raw
files manually.

## 9. Implementation Plan

### Phase 1A: Query Reliability
Work: add `read_page_outline`, `read_page_section`, `multi_read_sections`; make
QueryAgent index-first; raise candidate search to top-20; add deterministic
query expansion; remove dependency on first-page preview. Acceptance: answer
after 5000+ chars is found; 200 synthetic pages retrieve the expected page;
answers cite `[[slug]]`; no query path truncates instead of selecting sections.
Checklist: outline/section extraction tests; long-page and 200-page integration
tests; backward-compatible API.

### Phase 1B: Source Identity and Drift Primitives
Work: compute source sha256; create Source Card skeleton; add source drift
linter check; link pages to source cards where available. Acceptance: changed
raw file is detected; unchanged raw file is not reported; Source Card is valid;
missing source warning does not crash audit. Checklist: sha256 tests; Source
Card path tests; linter tests for drift and missing raw source.

### Phase 2: Large Source Ingest
Work: implement outline parser and chunking fallback order; run Step 1 per
chunk; merge chunk analysis; replace `max_pages_per_source=10` with
batch/review limits. Acceptance: 1M char fixture processes all chunks; failed
chunk is reported; no section is silently dropped; conflicts do not abort
non-conflicting writes; generated pages stay under char limits. Checklist:
Markdown/synthetic outline tests; code/table-safe chunking tests; mock-LLM
multi-chunk integration test; review threshold test.

### Phase 3: Claims Layer
Work: extract claims during chunk analysis; write claims into `_claims`; dedupe
claims; use claims for conflict detection and page generation provenance.
Acceptance: factual page sections reference source/claim; duplicate claim is not
written twice; contradicted claim opens conflict; claim files stay under limits.
Checklist: claim model; claim id/status tests; claim-driven conflict test.

### Phase 4: Safe Page Updates
Work: implement page write plans; support section-level typed operations; show
deterministic diffs before large writes; require review over thresholds.
Acceptance: update preserves frontmatter and unrelated sections; invalid
operation is rejected; large update enters review; arbitrary LLM patch is
rejected. Checklist: tests for `replace_section`, `append_section`,
`update_frontmatter_field`, and arbitrary patch rejection.

### Phase 5: Better Retrieval
Work: replace naive search with weighted field search; keep QueryAgent tool
contract stable; optionally add BM25/TF-IDF later. Acceptance:
title/tags/headings outrank body-only matches; query API remains compatible;
BM25 can be added without changing QueryAgent prompts/tools. Checklist: field
scoring tests; current search regression tests; similar-pages benchmark fixture.

## 10. Global Done Checklist

- [ ] No database or JSON state store added; all writes to `wiki-data/` go
      through `wiki_fs.py`.
- [ ] All new pages have required frontmatter; all internal links use
      `[[slug]]`; L0/L1 indexes update after page create/delete.
- [ ] No file size limit is exceeded silently; ingest remains two-step; conflicts
      are written before any resolution.
- [ ] Query and ingest read `skills.md` before operation; agent tests use mock
      LLM returning valid JSON.
- [ ] Tests cover new `wiki_fs.py` methods; API route tests use
      `httpx AsyncClient` where route behavior changes.

## 11. Open Decisions

Resolved now:

- claims are separate from Source Cards from the start;
- typed relations are optional and universal, not software-only;
- source sha256/drift primitives are implemented before full large ingest;
- exact page updates are structured section/frontmatter operations only;
- indexes are hierarchical and split semantically.

Still open:

- whether Source Cards are visible in UI tree by default or hidden;
- exact UI for review/draft plans;
- whether query history lives only in chat history or also in `_queries`;
- when to introduce BM25/TF-IDF.

Recommended first task: Phase 1A, because outline/section query tools are
low-risk, immediately useful, and become the inspection foundation for large
ingest, Source Cards, claims, and review flows.
