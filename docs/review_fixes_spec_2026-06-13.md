# Specification: Review Fixes 2026-06-13

**Дата:** 2026-06-13
**Автор:** ревью кодовой базы по состоянию на коммит `4d0013d`
**Предыдущий план:** `docs/coding_agent_review_fixes_plan.md`
**Связанные:** `docs/mvp_decisions_and_debt_ru.md`, `docs/session-2026-06-13.md`

---

## Контекст

После детального ревью кодовой базы по итогам последних 10 коммитов
(31cd5c6 → 4d0013d) выявлено: часть P0-задач из `coding_agent_review_fixes_plan.md`
не закрыта, добавились новые регрессии, нарушены соглашения из `CLAUDE.md`.

Тесты: **325 passed, 1 skipped, 0 failed**.
Рефакторинг `wiki_fs.py` и `ingest_agent.py` завершён и непротиворечив.

Эта спецификация — actionable: каждая задача имеет чёткие acceptance criteria
и список файлов. Документ — источник правды для следующей итерации.

## Условные обозначения приоритетов

- **P0** — регрессия или явное нарушение design-документа. Блокирует demo.
- **P1** — нарушение соглашений `CLAUDE.md`. Не ломает runtime, но копит долг.
- **P2** — уборка, документация, мелкие улучшения.

---

# P0: Критичные регрессии

## P0-1: `apply-update` не должен вызывать LLM

**Серьёзность:** P0 (нарушение design-документа)
**Сложность:** M
**Файлы:**
- `app/api/routes/conflicts.py:189-271` — `apply_conflict_update`
- `app/core/wiki_conflicts.py:124-177` — `prepare_conflict_resolution_draft`
- `app/ui/index.html` — UI для отображения diff перед apply

### Проблема

`app/api/routes/conflicts.py:189` в `apply_conflict_update`:
```python
"""
Apply a prepared conflict resolution draft to the wiki page.
Uses LLM to generate updated content based on resolution, then applies it.
"""
...
new_content = await asyncio.to_thread(
    agent.llm.call, system=system, prompt=prompt, ...
)
```

`docs/coding_agent_review_fixes_plan.md:357-365` явно запрещает это:
> ## Do not do
> - **Do not call LLM again inside `apply-update` to generate fresh content.**
> - Do not overwrite page without a stored candidate.

Текущий `prepare_conflict_resolution_draft` НЕ сохраняет `new.md` —
он сохраняет только `existing.md` и `meta.json` с разрешением. Поэтому
`apply-update` вынужден генерировать контент заново.

### Корень проблемы

Шаг prepare неполный: draft создаётся без candidate content.

### Требуемое поведение

`prepare-update`:
1. Находит конфликт, извлекает resolution + user_comment.
2. Читает существующую страницу.
3. **Вызывает LLM один раз** для генерации candidate content.
4. Сохраняет в draft: `meta.json`, `existing.md`, `new.md`, `diff.patch`.
5. Возвращает метаданные + diff для рендера в UI.

`apply-update`:
1. Проверяет наличие `new.md` в draft.
2. Если нет — HTTP 400 «Сначала вызовите prepare-update».
3. Читает `new.md` и `meta.json` (для сохранения frontmatter).
4. Парсит frontmatter из `existing.md`, сливает с новым контентом.
5. Записывает страницу.
6. Rebuild index.
7. Удаляет draft.
8. **Не вызывает LLM.**

`reject-update` (новый endpoint):
1. Удаляет draft, не трогая страницу.

### Шаги реализации

1. В `prepare_conflict_resolution_draft` (`app/core/wiki_conflicts.py`):
   - Добавить параметры `llm: LLMGateway`, `settings: Settings`.
   - После чтения `existing_page` — построить prompt и вызвать LLM.
   - Сохранить `new.md` (frontmatter + новый контент) и `diff.patch`
     (unified diff между existing и new).
   - В response добавить поле `diff` для UI.

2. В `app/api/routes/conflicts.py`:
   - `prepare_conflict_update`: передать `agent.llm` и `settings` в
     `prepare_conflict_resolution_draft`. Вернуть `diff` в response.
   - `apply_conflict_update`: убрать LLM-вызов. Читать `new.md` напрямую.
     Применить через `fs.write_page(...)` (НЕ через `frontmatter.dumps`
     напрямую — violates «wiki_fs.py is the ONLY module allowed to write
     to `wiki-data/`» из `CLAUDE.md:94`).
   - Добавить `reject_conflict_update` endpoint: `POST /{id}/reject-update`.

3. В `app/ui/index.html`:
   - В modal конфликта добавить кнопку «Prepare update».
   - После prepare — показывать diff (split или unified) с кнопками
     Apply/Reject.

### Тесты

В `tests/test_conflicts.py` (новый файл):
- `test_prepare_creates_new_md` — после prepare в draft есть `new.md`,
  `diff.patch`, `meta.json`.
- `test_apply_uses_stored_candidate` — мокаем LLM, проверяем, что
  `apply_conflict_update` НЕ вызывает `llm.call`.
- `test_apply_preserves_frontmatter` — после apply frontmatter сохранён.
- `test_apply_rebuilds_index` — после apply `index.md` обновлён.
- `test_reject_keeps_page_unchanged` — после reject страница и draft
  удалена, исходный контент не тронут.
- `test_apply_without_prepare_returns_400`.

### Acceptance criteria

- [ ] `prepare-update` возвращает `diff` в response.
- [ ] `apply-update` не содержит ни одного вызова `llm.call`/`agent.llm`.
- [ ] `reject-update` endpoint существует и удаляет draft.
- [ ] Все 6 тестов проходят.
- [ ] UI показывает diff перед apply.

---

## P0-2: Project attribution на conversion errors

**Серьёзность:** P0 (блокирует корректную отчётность об ошибках)
**Сложность:** S
**Файлы:**
- `app/core/raw_sources.py` — добавить helper
- `app/agents/ingest_agent.py:78-100` — использовать helper
- `app/agents/ingest_large.py:184-186` — то же

### Проблема

`app/agents/ingest_agent.py:81-100`:
```python
except RawSourceError as exc:
    ...
    return IngestResult(success=False, ..., project="_general", ...)
if source_content is None:
    ...
    return IngestResult(success=False, ..., project="_general", ...)
```

При ошибке конверсии для `eywa-demo/bad.pdf` пользователь получает
`project: "_general"` — это прямо противоречит
`docs/coding_agent_review_fixes_plan.md:182-190`:
> If `eywa-demo/bad.pdf` fails conversion, API returns:
> `{"project": "eywa-demo", ...}`

### Корень проблемы

Project вычисляется через `self.fs.get_raw_project(...)` на line 102 —
**после** того как conversion уже упала.

### Требуемое поведение

Project inference должен происходить до чтения файла. Добавить helper:

```python
# app/core/raw_sources.py
def infer_project_from_raw_relative_path(raw_relative_path: str) -> str:
    """
    Infer project name from raw_relative_path BEFORE any I/O.
    Used in error branches where file conversion/read failed.

    >>> infer_project_from_raw_relative_path("eywa-demo/bad.pdf")
    'eywa-demo'
    >>> infer_project_from_raw_relative_path("bad.pdf")
    '_general'
    >>> infer_project_from_raw_relative_path("a/b/c.md")
    'a'
    """
    normalized = raw_relative_path.replace("\\", "/").strip("/")
    if "/" not in normalized:
        return "_general"
    return normalized.split("/", 1)[0] or "_general"
```

Использовать его во всех error-ветках `ingest_agent.run()` и в начале
`_run_large_ingest()`.

### Шаги реализации

1. Добавить `infer_project_from_raw_relative_path` в `app/core/raw_sources.py`.
2. В `app/agents/ingest_agent.py:81-100`: заменить `project="_general"` на
   `project=infer_project_from_raw_relative_path(raw_relative_path)`.
3. В `app/agents/ingest_large.py:184-186` (`source_id = slugify(...)`):
   то же самое, если ветка ошибки возвращает `IngestResult`.
4. Добавить import.

### Тесты

В `tests/test_ingest_agent.py`:
- `test_conversion_error_preserves_project` — мокаем `read_raw_source_file`
  чтобы поднять `RawSourceError` для `eywa-demo/bad.pdf`, проверяем что
  `result.project == "eywa-demo"`.
- `test_missing_source_preserves_project` — то же для `None` от `read_raw_source_file`.

В `tests/test_raw_sources.py` (новый файл):
- `test_infer_project_with_subdir`
- `test_infer_project_root`
- `test_infer_project_empty`
- `test_infer_project_windows_path`

### Acceptance criteria

- [ ] Helper существует и покрыт unit-тестами.
- [ ] Conversion error возвращает корректный `project`.
- [ ] Missing source error возвращает корректный `project`.

---

## P0-3: Убрать CDN React/Babel из runtime UI

**Серьёзность:** P0 (demo в restricted network не взлетит)
**Сложность:** S-M (механическая + проверка лицензий)
**Файлы:**
- `app/ui/index.html:8-10` — убрать CDN-скрипты
- `app/ui/vendor/` (новый каталог) — локальные копии
- `app/api/main.py` — mount `/vendor`
- `app/ui/index.html` — update script paths

### Проблема

`app/ui/index.html:8-10`:
```html
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
```

`docs/coding_agent_review_fixes_plan.md:330`:
> With network disabled, `python -m uvicorn app.api.main:app --reload --port 8000`
> opens the UI successfully.

### Требуемое поведение

UI стартует без интернет-доступа. Скрипты React/ReactDOM/Babel лежат локально.

### Шаги реализации

1. Скачать и положить в `app/ui/vendor/`:
   ```
   react.production.min.js       (~10 KB, MIT license)
   react-dom.production.min.js   (~40 KB, MIT license)
   babel.min.js                  (~3 MB, MIT license)
   ```
   Все три библиотеки под MIT — легально коммитить.

2. Добавить `LICENSE.*` файлы рядом (или сводный `vendor/LICENSES.txt`).

3. В `app/api/main.py`: добавить mount:
   ```python
   from fastapi.staticfiles import StaticFiles
   app.mount("/vendor", StaticFiles(directory="app/ui/vendor"), name="vendor")
   ```

4. В `app/ui/index.html:8-10`: заменить на:
   ```html
   <script src="/vendor/react.production.min.js"></script>
   <script src="/vendor/react-dom.production.min.js"></script>
   <script src="/vendor/babel.min.js"></script>
   ```

5. Проверить, что нет других упоминаний `unpkg.com`/`cdnjs` в UI.

### Тесты

В `tests/test_ui_static.py` (новый файл):
- `test_index_html_no_cdn_references` — `grep -E "unpkg|cdnjs" app/ui/index.html`
  возвращает 0 строк.
- `test_vendor_assets_exist` — три файла существуют и непустые.
- `test_vendor_route_returns_200` — `GET /vendor/react.production.min.js`
  возвращает 200.
- `test_upload_modal_accepts_docx_pdf_pptx` — в HTML есть `.docx`, `.pdf`, `.pptx`.

### Acceptance criteria

- [ ] `grep -rn "unpkg\|cdnjs" app/ui/` возвращает 0 строк.
- [ ] С отключенным интернетом UI открывается.
- [ ] 4 теста проходят.

---

## P0-4: `/api/ingest/batch` должен соответствовать спецификации

**Серьёзность:** P0 (тихий fail, demo-killer)
**Сложность:** S
**Файлы:**
- `app/api/routes/ingest.py:131-164` — `ingest_batch`
- `tests/test_batch_ingest.py` (новый файл)

### Проблема

`app/api/routes/ingest.py:131-164`:
```python
for key, source_file in form.multi_items():
    if key != "files":     # ← не принимает алиас "file"
        continue
    ...
return {
    "total": len(results),
    "skipped": skipped,    # ← нет "processed", нет "skipped_details"
    "successes": ...,
    "failures": ...,
    "details": results,
}
```

4 несоответствия плану (`coding_agent_review_fixes_plan.md:75-148`):

| Требование | Реальность |
|---|---|
| Принимать `files` и алиас `file` | только `files` |
| Пустой батч → HTTP 400 | возвращает 200 с `total: 0` |
| Поле `processed` в response | отсутствует |
| `skipped_details` отдельно от `skipped` | одно поле `skipped` |

### Корень проблемы

Батч был реализован «по-минимуму» без учета явной спецификации.

### Требуемое поведение

```python
@router.post("/batch")
async def ingest_batch(request: Request, ...):
    form = await request.form()
    project = str(form.get("project") or "_general")
    validate_project_name(project)  # → 400 on bad project

    accepted_keys = {"files", "file"}
    items = [
        (key, value) for key, value in form.multi_items()
        if key in accepted_keys and getattr(value, "filename", None)
    ]
    if not items:
        raise HTTPException(400, "Пустой батч: нет файлов с ключом 'files' или 'file'")

    results = []
    skipped_details = []
    for key, source_file in items:
        try:
            response = await _save_and_ingest(project, source_file, agent, fs)
            results.append(response.model_dump())
        except HTTPException as exc:
            skipped_details.append({
                "file": source_file.filename,
                "reason": exc.detail,
            })

    return {
        "total": len(items),
        "processed": len(results),
        "skipped": len(skipped_details),
        "successes": sum(1 for r in results if r["success"]),
        "failures": sum(1 for r in results if not r["success"]),
        "details": results,
        "skipped_details": skipped_details,
    }
```

### Шаги реализации

1. В `app/api/routes/ingest.py` заменить `ingest_batch`.
2. Убедиться, что `_save_and_ingest` правильно классифицирует «unchanged»
   (это success, не skip — `analysis_notes="Source unchanged, skipped"`).

### Тесты

В `tests/test_batch_ingest.py` (новый файл) — через `httpx.AsyncClient`:
- `test_batch_accepts_files_key`
- `test_batch_accepts_file_alias`
- `test_batch_mixed_files_and_file_keys`
- `test_batch_empty_returns_400`
- `test_batch_unsupported_extension_in_skipped_details`
- `test_batch_invalid_project_returns_400`
- `test_batch_response_has_processed_field`
- `test_batch_one_file_fails_others_processed`

### Acceptance criteria

- [ ] 8 тестов проходят.
- [ ] Response содержит поля: `total, processed, skipped, successes,
      failures, details, skipped_details`.
- [ ] Принимаются и `files=`, и `file=`.
- [ ] Пустой батч → HTTP 400.

---

# P1: Нарушения соглашений CLAUDE.md

## P1-1: File Size Limit — 3 недокументированных нарушения

**Серьёзность:** P1
**Сложность:** M (control_store) / S (другие)
**Файлы:**
- `app/core/control_store.py` (595 строк) — сплит
- `app/core/large_source_ingest.py` (539) — сплит или документировать
- `app/core/linter.py` (520) — сплит или документировать
- `AGENTS.md:503-516` — обновить секцию File Size Limit

### Проблема

`CLAUDE.md:505` (оно же `AGENTS.md:505`):
> No file should exceed 500 lines without a justified exception.

`AGENTS.md:513`:
> No current violations.

Реальность (см. `wc -l`):

| Файл | Строк | Статус в AGENTS.md |
|---|---|---|
| `app/core/control_store.py` | **595** | не упомянут |
| `app/core/large_source_ingest.py` | **539** | не упомянут |
| `app/core/linter.py` | **520** | не упомянут |

### Шаги реализации

Выбрать для каждого файла один из двух путей:

#### A. Сплит (предпочтительно для control_store.py)

`control_store.py` (595) → разбить на:
- `app/core/control_store.py` (~120) — Protocol + dataclasses + helpers
- `app/core/control_store_sqlite.py` (~370) — `SQLiteControlStore`
- `app/core/control_store_noop.py` (~60) — `NoopControlStore`

`large_source_ingest.py` (539) → разбить на:
- `app/core/large_source_types.py` (~70) — dataclasses
  (`OutlineItem`, `DocumentOutline`, `Chunk`, `ChunkAnalysisResult`, `MergeAnalysisResult`)
- `app/core/large_source_outline.py` (~180) — `parse_outline`, `_parse_*`
- `app/core/large_source_chunk.py` (~200) — `chunk_by_outline`, `_split_*`
- `app/core/large_source_merge.py` (~110) — `merge_analysis`

`linter.py` (520) → разбить checks на 2-3 группы:
- `app/core/linter.py` (~200) — WikiLinter class, lint(), `_collect_pages`
- `app/core/linter_checks_structure.py` (~180) — wikilink/path/anchor/orphan/frontmatter
- `app/core/linter_checks_sources.py` (~150) — source_drift/missing_source/orphan_claim/...

#### B. Документировать как exception (если сплит рискованный)

В `AGENTS.md:507-509` после wiki_fs добавить:
```
- `app/core/control_store.py` (595 lines) — Protocol + SQLite + Noop implementations
  together for atomic review; split awaits refactor budget
- `app/core/large_source_ingest.py` (539 lines) — outline parser + chunker + merge analysis
  coupled through shared types
- `app/core/linter.py` (520 lines) — WikiLinter + 18 checks share _collect_pages state
```

И убрать строку «No current violations.».

### Рекомендация

Для **`control_store.py`** — сплит (класс `SQLiteControlStore` занимает строки 155-523
один, очевидный кандидат на выделение).
Для **`large_source_ingest.py`** и **`linter.py`** — начать со сплита типов, оставив
логику в одном модуле; если останется >500, документировать как exception.

### Acceptance criteria

- [ ] `find app -name "*.py" -exec wc -l {} \; | awk '$1 > 500'` показывает
      только `wiki_fs.py` и `index.html` (которые задокументированы).
- [ ] Либо AGENTS.md содержит явные exceptions для каждого файла >500 строк.
- [ ] Строка «No current violations.» убрана.
- [ ] Все 325 существующих тестов продолжают проходить после сплитов.

---

## P1-2: Ruff F821 — `ChatSession` undefined в query_agent

**Серьёзность:** P1
**Сложность:** S
**Файлы:**
- `app/agents/query_agent.py:89, 122, 195, 239` — annotations
- (опционально) пробежать `ruff check --fix` по всем app/

### Проблема

```bash
$ python -m ruff check app/ | grep F821
F821 Undefined name `ChatSession` → app/agents/query_agent.py:89
F821 Undefined name `ChatSession` → app/agents/query_agent.py:122
F821 Undefined name `ChatSession` → app/agents/query_agent.py:195
F821 Undefined name `ChatSession` → app/agents/query_agent.py:239
```

Код работает только потому что `from __future__ import annotations` делает
аннотации строками. Любая попытка `typing.get_type_hints(...)` упадёт.

Полный список ошибок ruff: **19**, из них:
- F821: 4 (все `ChatSession`)
- F401: 6 (unused imports в `wiki_types.py`, `ingest_agent.py`, и др.)
- I001: import sorting в нескольких файлах

### Шаги реализации

1. В `app/agents/query_agent.py` добавить import:
   ```python
   from app.agents.query_types import ChatMessage, ChatSession, QueryResult
   ```
   (сейчас импортируются только `ChatMessage`, `QueryResult` — line 44).

2. Пройтись по остальным ruff errors:
   ```bash
   python -m ruff check --fix app/   # автоматически починит F401, I001
   ```

3. Вручную проверить места, где `ruff --fix` удалил unused import — возможно,
   это dead code, который тоже надо убрать.

### Тесты

Существующих тестов достаточно. Добавить:
- `test_ruff_clean` (опционально) — `subprocess.check_call(["ruff", "check", "app/"])`
  в CI-режиме.

### Acceptance criteria

- [ ] `python -m ruff check app/` возвращает 0 ошибок.
- [ ] `ChatSession` импортирован в `query_agent.py`.
- [ ] Все 325 тестов продолжают проходить.

---

## P1-3: AGENTS.md — устаревшее дерево файлов

**Серьёзность:** P1
**Сложность:** S
**Файлы:**
- `AGENTS.md:20-30` (оно же `CLAUDE.md`) — repository layout
- `AGENTS.md:50-51` — дублирование и устаревшие пути

### Проблема

В `AGENTS.md:20-23` дубликат:
```
│   │   ├── ingest_agent.py       # Plan-and-Execute ingest pipeline
│   │   ├── ingest_agent.py       # Plan-and-Execute ingest pipeline (orchestrator)
```

В `AGENTS.md:50-51` два модуля с пересекающимся описанием:
```
│   │   ├── large_source_ingest.py # Outline parser, chunking, merge analysis
│   │   ├── safe_page_updates.py  # Typed page operations with diff generation
```

И в `AGENTS.md:42`:
```
│   │   ├── wiki_updates.py       # Safe page updates with diff generation
```

Реальность:
- `app/core/large_source_ingest.py` существует, но есть **также**
  `app/agents/ingest_large.py` (334 строки) — pipeline orchestrator, использующий
  модуль из core. В AGENTS.md упомянут только core-модуль.
- `app/core/safe_page_updates.py` (366 строк) и `app/core/wiki_updates.py` (59 строк)
  — **оба** существуют с пересекающейся ответственностью:
  - `safe_page_updates.py` — typed ops (replace_section, append_section, ...)
  - `wiki_updates.py` — raw apply_safe_update + generate_update_diff
  Это не bug, но в AGENTS.md описания идентичны, что вводит в заблуждение.

### Шаги реализации

1. Убрать дубликат строки про `ingest_agent.py` (lines 22-23 — оставить одну).

2. Уточнить описания (lines 42, 50, 51):
   ```
   │   │   ├── wiki_updates.py       # apply_safe_update, generate_update_diff (raw)
   │   │   ├── safe_page_updates.py  # Typed ops: replace_section, append_section
   ```

3. В блоке `app/agents/` добавить все недостающие файлы из рефакторинга:
   - `ingest_helpers.py` — helper functions (parse JSON, tags, ...)
   - `ingest_prompts.py` — prompt templates
   - `ingest_types.py` — AnalysisResult, IngestResult, ...
   - `ingest_retrieval.py` — related page retrieval
   - `ingest_generate.py` — page generation
   - `ingest_large.py` — large source pipeline orchestrator
   - `ingest_conflicts.py` — conflict recording, auto-resolution
   - `query_search.py` — search/retrieval/classify/compress
   - `query_prompts.py` — query prompt templates
   - `query_types.py` — ChatMessage, ChatSession, QueryResult

4. В блоке `app/core/` добавить недостающие модули:
   - `raw_sources.py` — binary source handling, MarkItDown
   - `metered_llm_client.py` — output-only billing wrapper
   - `linter_models.py` — LintIssue, LintReport
   - `search_provider.py` (упомянут, но проверить)
   - `control_store.py` (НЕ упомянут в layout, но критичен для multi_user)
   - `context.py` — WorkspaceContext
   - `session_store.py` — НЕ упомянут (в api/, не core)

### Acceptance criteria

- [ ] В AGENTS.md нет дубликатов строк.
- [ ] Каждый .py файл в `app/` имеет соответствующую строку в layout-секции.
- [ ] Описания modules не вводят в заблуждение относительно ответственности.

---

# P2: Уборка и документация

## P2-1: Session-2026-06-13 — некорректные «✅» против плана

**Сложность:** S
**Файлы:** `docs/session-2026-06-13.md:62-65`

### Проблема

```
## Plan for Next Session
1. ✅ Onboarding flow (P1)
2. ✅ Claim-level query integration (P1, #16)
3. ✅ Baseline tests (P1, #2) — ingest agent + query agent claims
4. Tech debt: sandbox hardening, VPS auth hardening (P2, #1)
```

Сессия отмечена как выполнившая P1-задачи, но при этом P0-задачи из
`coding_agent_review_fixes_plan.md` (batch ingest, project attribution,
CDN removal, conflict draft flow) — не закрыты. Это создаёт ложное
впечатление, что план выполнен.

### Шаги реализации

1. В `docs/session-2026-06-13.md` добавить секцию «Plan status — честная сводка»:
   - Что сделано: onboarding, claim-level query, baseline tests для
     ingest/query agents.
   - Что НЕ сделано из P0 плана: CDN removal, batch fixes, project
     attribution, conflict draft flow без LLM.
   - Ссылка на этот документ (`review_fixes_spec_2026-06-13.md`).

### Acceptance criteria

- [ ] Session doc не утверждает, что P0 выполнен.

---

## P2-2: README — обновить «known limitations»

**Сложность:** S
**Файлы:** `README.ru.md`, `README.en.md`

### Проблема

`docs/coding_agent_review_fixes_plan.md:601-607` требует:
> ## Required content
> - Local UI no longer depends on external CDN.
> - Batch endpoint behavior is documented.
> - Conflict resolution draft/diff flow is documented.
> - Known limitations remain honest:
>   - local/trusted usage;
>   - CORS still permissive in dev;
>   - context truncation still coarse;
>   - production hardening not complete.

После закрытия P0-1..P0-4 нужно обновить README.

### Шаги реализации

1. После того как P0-1, P0-2, P0-3, P0-4 закрыты — добавить в README:
   - Раздел «Conflict resolution flow» с описанием prepare/apply/reject.
   - Раздел «Batch ingest» с примером curl.
   - Заменить любые упоминания «requires internet» на «works offline».
2. В known limitations оставить: CORS dev-only, coarse truncation,
   non-production sandbox.

### Acceptance criteria

- [ ] README не overpromise production readiness.
- [ ] Все 4 P0-фичи задокументированы.

---

## P2-3: TODO в `search_provider.py`

**Сложность:** N/A (документировать как техдолг)
**Файлы:** `app/core/search_provider.py:107`, `docs/mvp_decisions_and_debt_ru.md`

### Проблема

`app/core/search_provider.py:107`:
```python
# TODO: implement BM25 with rank-bm25 library
```

BM25 заявлен в AGENTS.md:34 как часть wiki_search.py, но в search_provider
это заглушка. Не блокирует (weighted search работает), но копит несоответствие
docs ↔ code.

### Шаги реализации

В `docs/mvp_decisions_and_debt_ru.md` секция «Технический долг» (после 2.4):
```
### 2.5 BM25 ranking не реализован
Долг:
- search_provider.py содержит заглушку TODO для BM25;
- текущий поиск использует только weighted scoring (term frequency + position);
- BM25 требует зависимости `rank-bm25` и маппинга corpus statistics.

Почему не сейчас:
- weighted scoring достаточно для wiki размеров <1000 страниц;
- BM25 даёт improvement 2-го порядка на коротких queries.
```

### Acceptance criteria

- [ ] В mvp_decisions_and_debt_ru.md есть пункт 2.5 про BM25.

---

# Порядок реализации

Сделать одним PR/sequence (коммитить прямо в `main`, per project convention):

1. **P0-2** (project attribution) — S, без зависимостей, быстрый win.
2. **P0-4** (batch ingest) — S, изолированный endpoint.
3. **P0-1** (conflict draft flow) — M, требует UI-правок.
4. **P0-3** (CDN removal) — S-M, требует download и лицензии.
5. **P1-2** (ruff fix) — S, после всех code changes.
6. **P1-1** (file size split) — M, last потому что easiest to regress.
7. **P1-3** (AGENTS.md tree) — S, в самом конце (после сплитов из P1-1).
8. **P2-*** — последними, после того как код стабилен.

Каждый пункт = отдельный коммит. После каждого коммита — `pytest -q` →
325+ тестов проходят. Если регрессия — откатить, починить, повторить.

---

# Out of scope (намеренно не делаем)

Перенято из `coding_agent_review_fixes_plan.md`:

1. Smarter LLM context packing/truncation — оставляем как есть.
2. CORS restrictive policy — оставляем `*` в dev.
3. Production-grade sandbox — `CodeInterpreter` уже ограничен AST + resource
   limits + 10s timeout; реальный sandbox = отдельный эпик.
4. Postgres migration — SQLite control plane остаётся для MVP.
5. Real OCR for PDFs — пока используем MarkItDown.
6. Vector search — не добавляем.
7. Refactoring `wiki_fs.py` дальше — 772 строки задокументированы как exception.

---

# Метрики успеха после завершения

- `pytest -q` → **333+ passed** (325 существующих + ~8 новых для P0).
- `python -m ruff check app/` → **0 errors**.
- `find app -name "*.py" | xargs wc -l | awk '$1>500'` → только
  `wiki_fs.py` и `index.html` (задокументированные exceptions).
- `grep -rn "unpkg\|cdnjs" app/ui/` → **0 строк**.
- Демо: upload `.pdf` с неверным project → API возвращает корректный project.
- Демо: resolve conflict → UI показывает diff → Apply НЕ вызывает LLM.
- Демо: отключить интернет → UI рендерится.
