# Прогноз поведения инжеста — тест-сценарий

Этот файл НЕ является источником для инжеста. Это предсказание того, что должно произойти при обработке трёх тестовых файлов.

---

## Что инжестируем

| Файл | Проект | Ожидаемые страницы |
|------|--------|-------------------||
| `backend/redis_cache.md` | backend | create: `backend/infrastructure/redis-cache` (entity) |
| `backend/postgres_storage.md` | backend | create: `backend/infrastructure/postgres-storage` (entity) |
| `frontend/redis_cache.md` | frontend | create: `frontend/infrastructure/redis-cache` (entity) |

---

## Прогноз по каждому файлу

### Файл 1: `backend/redis_cache.md`

**Step 1 (Analysis):**
- LLM обнаружит 1–2 entity-страницы: `backend/infrastructure/redis-cache` и возможно `backend/infrastructure/redis-monitoring`
- Конфликтов нет (wiki пустая на старте)
- `skills_triggered: []` — нет накопленных скиллов
- `confidence` ожидается высокий (0.85–0.95) — источник конкретный, технический

**Step 2 (Generation):**
- Страница будет на русском языке, технические термины (Redis, Docker, Prometheus) останутся на английском
- Frontmatter: `type: entity`, `project: backend`, `tags: [redis, cache, infrastructure]`
- Будут ссылки `[[backend/infrastructure/postgres-storage]]` из секции "Связанные компоненты"
- **Проблема:** `[[backend/infrastructure/postgres-storage]]` ещё не существует в момент генерации → linter выдаст `broken_wikilink` (ERROR)

**Lint после ingest:**
- 1 × `broken_wikilink` — ссылка на postgres-страницу которой ещё нет
- 0 × `orphan_page` — новая страница, корректно

---

### Файл 2: `backend/postgres_storage.md`

**Step 1 (Analysis):**
- Создаст: `backend/infrastructure/postgres-storage` (entity)
- Conflict detection: в исходнике написано что Redis — read-cache, а PostgreSQL — источник правды для сессий. Это ДОПОЛНЯЕТ уже созданную redis-страницу, не противоречит. Конфликта быть не должно.
- Если LLM решит обновить уже созданный `backend/infrastructure/redis-cache` (добавить упоминание PostgreSQL) → `pages_to_update` вместо `pages_to_create`

**Lint после ingest:**
- Если страница postgres создана: `broken_wikilink` на redis-странице ИСЧЕЗНЕТ
- `orphan_page` для postgres — возможен если redis-страница не обновлена автоматически

---

### Файл 3: `frontend/redis_cache.md`

**Step 1 (Analysis) — САМЫЙ ИНТЕРЕСНЫЙ ФАЙЛ:**
- Создаст: `frontend/infrastructure/redis-cache` (entity)
- **Обнаружит конфликт** типа `cross_project_difference`:
  - Backend Redis 7.2 vs Frontend Redis 6.2
  - Backend: allkeys-lru, 512MB, порт 6379
  - Frontend: volatile-lru, 256MB, порт 6380
- **Ожидаемое поведение:** конфликт пишется в `conflicts.md` с типом `cross_project_difference`, `is_cross_project: true`
- Инжест НЕ блокируется — страница создаётся

**Что будет в conflicts.md:**
```
## [OPEN] CONFLICT-001
- conflict_type: cross_project_difference
- is_cross_project: true
- page_a_slug: backend/infrastructure/redis-cache
- page_b_ref: frontend/redis_cache.md
- context: версия Redis, порт, maxmemory, политика вытеснения
```

---

## Возможные проблемы при инжесте

### P0: `_find_related_pages` вернёт пустой список

Из ревью кода: `_find_related_pages` не делает `print(json.dumps(result))` — `result_json` будет `None` всегда. Следовательно `wiki_context` в Step 1 будет пустым. Это означает:

- Конфликт между `backend/redis_cache.md` и `frontend/redis_cache.md` **НЕ БУДЕТ** обнаружен автоматически при обработке frontend-файла — LLM не увидит уже созданную backend-страницу в контексте
- Этот баг подтвердится если в conflicts.md после 3 инжестов будет 0 конфликтов

### P1: `action = "created"` будет неверным

Из ревью: `is_new` проверяется после записи → в логе все страницы могут отображаться как `updated` вместо `created`.

### P2: `ContextBudget()` без settings

IngestAgent создаёт `ContextBudget()` с дефолтными значениями, игнорируя yaml-конфиг. При маленьких файлах не заметно, но может срезать контекст неожиданно.

---

## Ожидаемый итог после всех трёх инжестов

| Метрика | Ожидание (если баги исправлены) | Ожидание (с текущими багами) |
|---------|--------------------------------|------------------------------|
| Создано страниц | 3 | 3 |
| Конфликтов в conflicts.md | 1 (cross_project_difference) | 0 (баг _find_related_pages) |
| Broken wikilinks после 1-го инжеста | 1 | 1 |
| Broken wikilinks после 2-го инжеста | 0 | 0 |
| Orphan pages | 0–1 | 0–1 |
| Что проверит, что страница "created" | log.md → action=created | log.md → action=updated (баг) |

---

## Что проверять в UI

1. **Wiki tree** → должно появиться 3 страницы в двух проектах: `backend` и `frontend`
2. **Правая панель** → клик на страницу → проверить что `[[...]]` ссылки кликабельны (баг с content_html)
3. **Conflicts tab** → должен показать 1 конфликт (или 0 если баг не исправлен)
4. **Chat** → спросить "чем отличается Redis в backend и frontend?" → ответ должен цитировать обе страницы
5. **Chat citations** → клик на `[[backend/infrastructure/redis-cache]]` в ответе → правая панель должна открыться
