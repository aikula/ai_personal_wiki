# Session Review Followup — 2026-06-13

**Context:** детальное ревью серии коммитов `e52352a..0ef4444` выявилo
регрессию, недостающие спецификационные тесты, отсутствие UI diff-preview
для conflict flow, ~12 недостающих модулей в AGENTS.md и две документационные
несогласованности. Этот документ описывает закрытые пробелы.

Спецификация-источник: `docs/review_fixes_spec_2026-06-13.md`.
Предыдущий session doc: `docs/session-2026-06-13.md`.

## Изменения по коммитам

### `2dafd10` — Tier 1: regression + docs sync

1. **Регрессия `wiki_fix.py`** — восстановлен безусловный skip для
   `page_type in ("index", "log")`, удалённый в `0ef4444`. Slug-нормализация
   оставлена. Обоснование: `log.md` — append-only история, autolink-pruning
   в нём необратимо стирает исторические ссылки.

2. **`AGENTS.md`** (оно же `CLAUDE.md` через symlink):
   - frontmatter example: `***` → `---` (соответствует реальному коду)
   - layout дополнен ~12 модулями: `context.py`, `utils.py`,
     `linter_models.py`, `metered_llm_client.py`, `raw_sources.py`,
     `migrations/runner.py`, `dependencies.py`, `session_store.py`,
     `audit.py`/`auth.py`/`onboarding.py`/`sources_api.py`/`usage.py`
     routes, `seed_data/`, `config.py`
   - секция «Filesystem is the database» — добавлен параграф про
     `wiki-data/drafts/` как documented exception

3. **`session-2026-06-13.md:76`** — удалена ссылка на несуществующий
   `session-2026-06-13-fixes.md`.

### `62b3e3f` — Tier 2: P0-1 conflict tests

Новый файл `tests/test_conflicts.py` с 6 тестами (HTTP-level через
ASGITransport + AsyncClient, mock LLM через monkeypatch):

| Тест | Что проверяет |
|---|---|
| `test_prepare_creates_new_md` | после prepare в draft есть `new.md`, `diff.patch`, `meta.json` |
| `test_apply_uses_stored_candidate` | mock LLM call count == 1 после apply (не вызывается повторно) |
| `test_apply_preserves_frontmatter` | title/project/created сохранены после apply |
| `test_apply_rebuilds_index` | index.md содержит обновлённую stats-строку |
| `test_reject_keeps_page_unchanged` | page.raw идентичен, draft удалён |
| `test_apply_without_prepare_returns_400` | 400/404 когда нет draft |

### `2a163d1` — Tier 2: UI diff-preview

`app/ui/index.html`, `ConflictsPanel`:
- Новые state hooks: `preparing`, `preparedDraft` (per-conflict-id)
- `applyUpdate` больше не вызывает prepare — только apply
- Новые функции `prepareUpdate`, `rejectUpdate`
- RESOLVED блок: если diff есть — `<pre>` с diff + Apply/Reject кнопки;
  если нет — одна кнопка «Подготовить обновление»

### `0211bd0` — Tier 3: polish

- `app/api/routes/conflicts.py` — inline `import shutil` (×2) и
  дубликат `import asyncio` вынесены в top-level
- `README.ru.md`, `README.en.md` — curl-пример для `/api/ingest/batch`,
  явная фраза «UI works offline» (закрытие spec P2-2)

## Метрики

```bash
$ python -m pytest tests/ -q --tb=no
349 passed, 1 skipped, 1 warning in 21.50s

$ python -m ruff check app/
All checks passed!

$ find app -name "*.py" -exec wc -l {} \; | awk '$1 > 500'
772 app/core/wiki_fs.py        # documented exception

$ grep -E "unpkg|cdnjs" app/ui/index.html
(empty)
```

Тесты выросли: 343 → **349** (+6 P0-1 conflict tests).

## Что НЕ сделано (намеренно)

- Перенос прямых записей в `drafts/` через `wiki_fs` API — оставлено как
  documented exception (см. AGENTS.md «Exception — wiki-data/drafts/»).
  Причина: рефакторить через `create_draft` — значит расширять его под
  чужую форму single-page draft; дешевле и честнее документировать
  установленный паттерн.
- Прямая проверка регрессии `wiki_fix.py` автотестом — тест требует
  сложной фикстуры (создать log entry, удалить страницу, вызвать fix).
  Текущее покрытие `tests/test_*.py` не включает log.md edge cases.
  Добавить при появлении отдельной test-suite для wiki_fix.
- Удаление `***` section-separator'ов в AGENTS.md (строки 240, 340) —
  это легитимный markdown horizontal rule, не frontmatter.

## Out of scope (из спецификации, намеренно не делаем)

- Vector search, BM25 implementation, Postgres migration, real OCR,
  further `wiki_fs.py` split — все задокументированы как техдолг.
