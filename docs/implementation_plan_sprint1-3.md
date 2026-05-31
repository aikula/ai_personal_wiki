# План реализации: Sprint 1–3

> Сформирован на основе аудита wiki_ingest_pipeline.md.
> Реализовано ранее — см. секцию «Уже реализовано» в конце.

---

## Диаграмма зависимостей

```
Sprint 1.1 (context budget) ─────► Sprint 2.9 (context budget increase)
Sprint 1.2 (fuzzy dedup) ─────────► Sprint 2.7 (claims retrieval)
Sprint 1.5 (collision warning)     (независимо)
Sprint 2.6 (index-first) ─────────► Sprint 2.8 (wikilink follow)
Sprint 3.12 (thresholds → config) ► Sprint 3.13 (max completion tokens)
```

---

## Sprint 1 — Критический (стабильность, 5 задач)

### 1.1 Контекстная валидация бюджета
**Сложность:** M | **Файлы:** `llm_client.py`, `config.py`, `settings.yaml`

- В `LLMClient.__init__()`: запросить `GET {base_url}/models` → `context_length` × 3
- В `LLMClient.call()` перед create: `total_chars // 3 < context_limit - max_tokens`
- При превышении: warn + truncate prompt с суффиксом `[CONTEXT_BUDGET_TRIMMED]`
- Fallback: если API недоступно → дефолт 128K токенов

### 1.2 Нечёткая дедупликация claims
**Сложность:** M | **Файлы:** `wiki_fs.py`, `ingest_agent.py`, `large_source_ingest.py`

- `find_duplicate_claim()`: заменить `normalized[:100].lower()` на `rapidfuzz.fuzz.ratio() > 85`
- Поиск по ВСЕМ claims проекта (не только source_id)
- При нахождении дубликата: `status: superseded` с ссылкой на оригинал
- В `merge_analysis()`: аналогичный fuzzy dedup между чанками
- Оптимизация: сначала `abs(len(a)-len(b)) < 30`, потом fuzzy

### 1.3 Постобработка пустых тегов
**Сложность:** S | **Файлы:** `ingest_agent.py`, `ingest_helpers.py`

- Новый метод `_ensure_tags(meta, planned, analysis)`:
  - Из slug: категория (предпоследний сегмент) + имя страницы
  - Из source: имя файла-источника
  - Макс. 5 тегов
- Вызов после парсинга JSON в `_step2_generate()` и `_generate_single_page()`

### 1.4 Ротация логов с архивом (5 файлов)
**Сложность:** S | **Файлы:** `wiki_fs.py`

- `_rotate_log()`: после записи архива — `archives[5:].unlink()`
- `_archive_resolved_conflicts()`: аналогично, максимум 5 архивов
- Формат: `wiki-data/logs/log-archive-YYYY-MM-DD.md`
- Формат: `wiki-data/conflicts/resolved-YYYY-MM-DD.md`

### 1.5 Collision-of-types warning
**Сложность:** S | **Файлы:** `large_source_ingest.py`

- В `merge_analysis()`: отслеживать `page_types: dict[str, str]`
- При конфликте типов: `logger.warning("Collision-of-types: %s → %s vs %s", slug, type_a, type_b)`
- Не блокировать merge, только логировать

**Порядок реализации:** 1.4 → 1.5 → 1.3 → 1.2 → 1.1

**Статус: ✅ Sprint 1 завершён (commit feb7f4b)**

---

## Sprint 2 — Качество запросов (5 задач)

### 2.6 Index-first retrieval
**Сложность:** M | **Файлы:** `query_agent.py`

- Новый метод `_index_first_retrieve(question, project)`:
  1. Прочитать L1 индекс проекта
  2. Keyword match: question words vs секции/заголовки индекса
  3. Вернуть whitelist slug'ов для `search_pages_weighted()`
- Интеграция в `_policy_factual()` и `_policy_comparison()`

### 2.7 Claims retrieval для factual queries
**Сложность:** M | **Файлы:** `query_agent.py`, `wiki_fs.py` | **Зависит от:** 1.2

- Новый метод `wiki_fs.search_claims(query, top_k=10)`:
  - `rapidfuzz.fuzz.partial_ratio()` по normalized, порог > 60
  - Вернуть quote + provenance + related_slugs
- В `_policy_factual()`: добавить секцию `## Relevant Claims` в wiki_context

### 2.8 Wikilink follow в ReAct
**Сложность:** S | **Файлы:** `query_agent.py`

- После `read_page`: извлечь `page.wikilinks`, добавить в scratchpad
- ReAct prompt hint: "consider following wikilinks for additional context"
- Лимит: 10 wikilinks на страницу

### 2.9 Увеличение контекстного бюджета
**Сложность:** M | **Файлы:** `config.py`, `settings.yaml`, `token_budget.py`, `query_agent.py`, `query_types.py` | **Зависит от:** 1.1

- `context_budget_chars`: 21K → 35K
- `history_budget_chars`: 7K → 10K
- Сжатие истории: `ChatSession.compress_history()` — LLM summary старых сообщений
- Trigger: `len(messages) > 4` → summarize первые N-2, передать последние 2 как есть

### 2.10 Авто-разрешение конфликтов по skills
**Сложность:** M | **Файлы:** `ingest_agent.py`, `wiki_fs.py`

- Новый метод `_try_auto_resolve_conflict(conflict_id, conflict_type)`:
  1. Прочитать skills.md
  2. Поиск правила, покрывающего conflict_type (regex/keyword match)
  3. Если найдено → `resolve_conflict(resolution="auto_skill: ...")`
- Вызов после `_record_conflicts()` и `_record_single_conflict()`
- В UI: показывать auto-resolved конфликты отдельно с возможностью отмены

**Порядок реализации:** 2.8 → 2.6 → 2.7 → 2.10 → 2.9

**Статус: ✅ Sprint 2 завершён**

---

## Sprint 3 — Масштабирование и UX (4 задачи)

### 3.11 Алфавитная пагинация индекса
**Сложность:** M | **Файлы:** `wiki_fs.py`, `wiki.py` (route), `index.html` (UI)

- `_write_project_index()`: группировка `## А`, `## Б`, ..., `## A`, `## B`
- API: `GET /wiki/tree?letter=А` — фильтрация
- UI: алфавитная линейка навигации

### 3.12 Вынос хардкодов в settings
**Сложность:** S (механическая) | **Файлы:** `config.py`, `settings.yaml`, `ingest_agent.py`

Поля для добавления в `IngestSettings`:
```
max_completion_tokens: 4000     # max_tokens в LLM вызовах
existing_content_limit: 2000    # обрезка существующего контента
link_candidates_limit: 15       # лимит link candidates
link_aliases_per_candidate: 3   # алиасы на кандидата
keyword_min_length: 5           # мин. длина слова для поиска
keyword_source_limit: 3000      # обрезка источника для keyword extraction
related_pages_limit: 10         # top-N связанных страниц
retry_temperature: 0.0          # температура при retry
default_confidence: 0.8         # confidence по умолчанию
conflict_context_limit: 600     # обрезка контекста конфликта
skill_extraction_limit: 800     # обрезка skill summary
```

### 3.13 Max completion tokens для reasoning models
**Сложность:** S | **Файлы:** `config.py`, `settings.yaml`, `llm_client.py`, `ingest_agent.py` | **Зависит от:** 3.12

- Новое поле `LLMSettings.max_completion_tokens: int = 8000`
- В `llm_client.py`: `effective_max_tokens = max_tokens or self.default_max_completion_tokens`
- В `ingest_agent.py`: заменить все `max_tokens=4000` на `self.settings.llm.max_completion_tokens`

### 3.14 Перекрытие чанков
**Сложность:** M | **Файлы:** `large_source_ingest.py`, `config.py`, `settings.yaml`

- Новое поле `IngestSettings.chunk_overlap_chars: int = 750`
- В `chunk_by_outline()`: `previous_tail = chunk.text[-overlap:]`
- Префикс следующего чанка: `previous_tail + "\n--- CONTINUED ---\n" + chunk_text`
- Адаптивный overlap: если чанк < overlap → не добавлять

**Порядок реализации:** 3.12 → 3.13 → 3.14 → 3.11

---

## Техдолг (не реализуем, только фиксируем)

| # | Задача | Почему отложено |
|---|--------|----------------|
| TD-1 | Diff-based инкрементальный re-ingest | Сложно, нужен стабильный baseline |
| TD-2 | Параллельная очередь ingest | Усложняет отладку |
| TD-3 | TF-IDF scoring для related pages | Улучшение 2-го порядка |
| TD-4 | OCR для сканированных PDF | Отдельная инфраструктура |
| TD-5 | Source section limit > 3000 | Покрывает ~80% случаев |
| TD-6 | Семантическое сравнение SHA256 | Нишевый кейс |

---

## Уже реализовано (до этого плана)

| # | Задача | Где |
|---|--------|-----|
| ✅ | Порог чанкинга 25K | `config.py`, `settings.yaml` |
| ✅ | max_auto_write_pages: 100 | `config.py`, `settings.yaml` |
| ✅ | require_review_if_pages_gt: 150 | `config.py`, `settings.yaml` |
| ✅ | Лимиты страниц: entity 8K, concept 10K, index_l1 10K | `config.py`, `settings.yaml` |
| ✅ | Source section: 1500→3000 | `ingest_prompts.py` |
| ✅ | Source sections budget: 16K→24K | `ingest_agent.py` |
| ✅ | Retry при CharLimitExceeded | `ingest_agent.py` (`_retry_compact_page`) |
| ✅ | Tags MANDATORY в промпте | `ingest_prompts.py` |
| ✅ | Auto-lint включает claims | `ingest_agent.py` |
| ✅ | Claims/sources исключены из индекса | `wiki_fs.py` |
| ✅ | planned_page_not_created lint | `linter.py` |
| ✅ | Chat persistence (SessionStore) | `session_store.py`, `dependencies.py` |
| ✅ | force_char_limit параметр | `ingest_agent.py` |
