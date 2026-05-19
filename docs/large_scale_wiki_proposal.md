# Large-Scale Wiki Engine Proposal

Дата: 2026-05-19

## Контекст

Текущая реализация хорошо подходит для небольших источников и умеренного числа
wiki-страниц, но начинает терять полноту при росте объема:

- ingest обрезает входной документ до текущего `wiki_context` лимита;
- поиск ответов читает только несколько top-страниц;
- ReAct-режим видит только preview страницы, а не структуру документа;
- жесткий `max_pages_per_source=10` защищает от взрыва страниц, но ломает
  большие источники;
- простой keyword search плохо различает похожие страницы.

Цель: сохранить plain-text/filesystem-first архитектуру, но научить систему
переваривать большие документы, отвечать по 200+ страницам и не терять
provenance.

## Главный принцип

Не обрезать знания под context window.

Context window должен ограничивать размер одной операции LLM, а не размер
источника, wiki или ответа. Большие документы нужно компилировать по частям:

```text
raw source -> outline -> chunks -> claims/plans -> merged analysis -> wiki pages
```

## Что полезно из обсуждения LLM Wiki

Из статьи Karpathy и комментариев к ней полезны следующие идеи:

- `index.md` должен быть активной navigation map, а не только UI-каталог.
- Хороший query сначала читает index/outline, затем проваливается в страницы.
- Ingest должен быть triage-first: сначала план, diff, conflicts, потом write.
- Raw sources остаются immutable source of truth.
- Хорошие ответы надо уметь crystallize обратно в wiki.
- Wiki нужно периодически lint/self-heal: contradictions, stale claims, orphan
  pages, missing links, duplicate topics.
- Source drift должен быть видимым через sha256/provenance.
- Для роста wiki нужны typed relationships, иначе все связи превращаются в
  слабое `related`.

Проекты из обсуждения, которые можно использовать как ориентиры:

- `qmd`: CLI/MCP search over markdown with query/get/multi_get primitives.
- `Origin`: granular memories, traceability, dedupe, contradiction detection.
- `OmegaWiki`: wiki как центр полного workflow, skills as rigid workflows.
- `knowledge_management` plugin: triage-first ingest, sha256 drift detection,
  deterministic scripts for mechanical work.

## Ingest документов на 1 млн символов

### Почему нельзя обрезать

Обрезание до 21k chars означает, что система вообще не узнает о фактах,
разделах и конфликтах после лимита. Это допустимо только как emergency fallback,
но не как штатный ingest.

### Предлагаемый pipeline

1. `parse_outline(source)`
   - Для Markdown: заголовки `#`, `##`, `###`.
   - Для PDF/DOCX/PPTX после conversion: искать заголовки, нумерацию,
     оглавление, page markers.
   - Если структуры нет: строить synthetic outline по крупным абзацам.

2. `chunk_by_outline(outline)`
   - Резать по естественным границам: heading, subsection, paragraph.
   - Target chunk: примерно `12_000-18_000` chars.
   - Hard max chunk: примерно `25_000` chars.
   - Таблицы, code blocks и списки стараться держать атомарно.
   - Для слишком больших секций применять recursive split.

3. `analyze_chunk(chunk)`
   - Извлечь candidate pages.
   - Извлечь factual claims.
   - Найти локальные conflicts с уже известной wiki.
   - Вернуть structured JSON, не писать wiki.

4. `merge_analysis(chunk_results)`
   - Объединить одинаковые slugs.
   - Слить source sections.
   - Убрать дубли claims.
   - Поднять conflicts на уровень source.
   - Сформировать общий triage report.

5. `generate_pages(merged_analysis)`
   - Генерировать страницы батчами.
   - При больших изменениях создавать draft/triage для review.
   - Писать через `wiki_fs.py`.

### Что делать с `max_pages_per_source=10`

Жесткий лимит на весь источник нужно убрать.

Вместо него:

- `max_pages_per_batch`: сколько страниц генерировать за один проход;
- `max_auto_write_pages`: сколько можно записать без review;
- `require_review_if_pages_gt`: порог для обязательного draft;
- `large_source_mode`: режим для источников выше заданного char threshold.

Большой источник может легитимно породить 40-100 страниц. Это не ошибка, если
страницы имеют provenance и прошли triage.

## Source Cards

Добавить служебные страницы источников:

```text
wiki/_sources/<project>/<source-slug>.md
```

Назначение:

- хранить outline источника;
- хранить sha256 raw-файла;
- фиксировать ingest status;
- перечислять созданные/обновленные wiki pages;
- перечислять extracted claims;
- фиксировать unresolved conflicts;
- показывать source drift, если raw изменился.

Пример frontmatter:

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
source_path: raw/myapp/deploy_guide.md
source_sha256: ...
ingest_status: active
---
```

## Claims Layer

Для больших документов полезен промежуточный слой claims.

Wiki page - это distillation. Claim - это маленькая проверяемая единица знания.

Минимальная claim-модель:

```yaml
claim_id: raw/myapp/deploy_guide.md#claim-001
source_path: raw/myapp/deploy_guide.md
source_section: "## Redis"
quote: "Redis 7.2 is used for session cache"
normalized: "Redis 7.2 используется для session cache."
related_slugs: [myapp/storage/redis]
confidence: 0.92
status: active
```

Где хранить:

- начально можно хранить claims внутри Source Card;
- позже можно вынести в `wiki/_claims/`, если объем станет большим.

Зачем:

- легче dedupe;
- легче conflict detection;
- легче source drift;
- проще объяснить, откуда взялась строка в wiki page.

## Query по 200+ страницам

### Текущая проблема

Сейчас factual/comparison retrieval берет только несколько страниц. ReAct читает
первые 1500 символов страницы. Если ответ лежит в середине или конце страницы,
агент его не увидит.

### Новый query flow

```text
question
  -> classify
  -> read project index / global index
  -> search candidates
  -> read outlines
  -> select sections
  -> read sections
  -> answer with citations
```

### Новые tools/methods

1. `read_page_outline(slug)`
   - title;
   - synopsis;
   - tags;
   - headings;
   - short section previews.

2. `read_page_section(slug, heading)`
   - полный текст секции;
   - optional char limit;
   - anchors for citation.

3. `multi_read_sections(requests)`
   - batch read selected sections.

4. `expand_query(question)`
   - 3-5 query variants;
   - synonyms;
   - expected entity names;
   - possible section titles.

5. `search_pages(query, top_k=20)`
   - пока keyword search;
   - позже BM25/TF-IDF;
   - потом optional vector/rerank.

### Почему outline лучше preview

Preview первых 1500 символов отвечает на вопрос "что в начале страницы".
Outline отвечает на вопрос "есть ли на странице нужный раздел".

Для длинной страницы outline почти всегда полезнее preview.

## Retrieval без BM25 на первом этапе

BM25/TF-IDF можно добавить позже. До этого улучшить качество можно так:

- искать по нескольким query variants;
- учитывать title/tags/synopsis/headings сильнее, чем body;
- читать index перед search;
- возвращать больше кандидатов;
- использовать section drilldown;
- давать агенту tool для чтения outline;
- выбирать секции перед чтением full text.

Пример scoring без новых зависимостей:

```text
score =
  8 * matches_in_title +
  5 * matches_in_tags +
  4 * matches_in_synopsis +
  3 * matches_in_headings +
  1 * matches_in_body
```

Это не BM25, но лучше текущего `text.count(w)` по всему raw.

## Typed Relationships

Обычный wikilink говорит только "связано". Для большой wiki этого мало.

Минимальный вариант:

```yaml
relations:
  - type: uses
    target: myapp/storage/redis
  - type: supersedes
    target: myapp/deploy/old-guide
  - type: configured_by
    target: myapp/config/env
```

В тексте остаются обычные `[[slug]]`, но frontmatter дает machine-readable graph.

Полезные типы:

- `uses`
- `depends_on`
- `configured_by`
- `supersedes`
- `contradicts`
- `implements`
- `mentions`
- `source_for`

## Index Improvements

`index.md` должен содержать не только project list, но и compact catalog:

- project;
- page slug;
- title;
- one-line synopsis;
- tags;
- updated date;
- source count.

Для больших проектов L1 index должен быть более полезным:

```markdown
## Storage
[[myapp/storage/redis]] - Session cache, Redis 7.2, TTL policy
[[myapp/storage/postgres]] - Primary relational database

## Deployment
[[myapp/deploy/docker]] - Docker Compose deployment
[[myapp/deploy/env]] - Required environment variables
```

Если L1 index превышает лимит, его нужно split по категориям, а не просто писать
warning.

## Lint / Self-Healing

Audit должен стать рабочим циклом:

1. Detect:
   - broken links;
   - missing source files;
   - source sha drift;
   - duplicate titles;
   - duplicate claims;
   - orphan pages;
   - pages without incoming/outgoing links;
   - stale facts;
   - unresolved conflicts.

2. Plan:
   - deterministic fixes;
   - candidate merges;
   - conflict queue;
   - suggested source re-ingest.

3. Apply:
   - safe deterministic fixes automatically;
   - semantic changes через draft/human review.

## Implementation Plan

### Phase 1: Query Reliability

- Add `read_page_outline`.
- Add `read_page_section`.
- QueryAgent uses index-first search.
- Increase candidate search to top-20.
- Add query expansion.
- Stop relying on first 1500 chars preview.

Success criteria:

- Questions can be answered from sections in middle/end of long pages.
- 200+ page wiki still returns relevant candidates for single-project queries.

### Phase 2: Large Source Ingest

- Add source outline parser.
- Add chunking by headings/paragraphs.
- Run Step1 per chunk.
- Merge chunk analysis.
- Replace `max_pages_per_source=10` with batch/review limits.

Success criteria:

- 1M char source is fully processed without truncation.
- Ingest reports all chunks processed.
- No information is silently dropped because of context limit.

### Phase 3: Source Cards and Drift

- Create `wiki/_sources/...` pages.
- Store source sha256 and outline.
- Link generated pages to source cards.
- Linter warns if raw source changed after ingest.

Success criteria:

- Every generated fact can be traced to a raw source.
- Changed source files are detectable.

### Phase 4: Claims Layer

- Extract claims during chunk analysis.
- Store claims in source cards.
- Use claims for conflict detection and page generation.

Success criteria:

- Conflict detection operates on claims, not only page prose.
- Page updates can cite exact source section/claim.

### Phase 5: Better Retrieval

- Replace naive search with weighted field search.
- Later add BM25/TF-IDF.
- Optional: qmd-like CLI/MCP integration.

Success criteria:

- Retrieval quality improves before adding embeddings.
- BM25 can be introduced without changing QueryAgent tool flow.

## Open Questions

1. Should Source Cards be visible in the UI tree by default or hidden under a
   technical section?
2. Should claims be separate files or embedded in Source Cards initially?
3. What threshold defines `large_source_mode`: 50k, 100k, 250k chars?
4. What review threshold is acceptable: more than 10 pages, 25 pages, 50 pages?
5. Should typed relations be required immediately or optional/linter-warned?
6. Should crystallized answers become normal concept pages or a separate
   `wiki/_queries/` namespace?

## Recommended Next Step

Start with Phase 1.

Reason: better query tools are useful immediately, low-risk, and also become the
foundation for large-source ingest review. If the agent can read outlines and
sections reliably, it can both answer better and inspect large ingest outputs
better.
