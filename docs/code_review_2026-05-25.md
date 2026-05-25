---
title: Ревью кода проекта Wiki Engine
date: 2026-05-25
author: OpenCode
project: _general
type: document
tags: [review, audit, refactoring]
confidence: 1.0
---

# Ревью кода проекта Wiki Engine

**Дата:** 2026-05-25  
**Область:** полная кодовая база + последние изменения (13 коммитов от HEAD~13 до HEAD)  
**Статус:** ✅ завершено

---

## 1. Общая оценка

Проект находится в **стабильном состоянии**.

- **308 тестов проходят** (21.66s)
- Архитектура последовательно следует принципу **«filesystem is the database»**
- Код агентов хорошо разделён на модули (`query_agent.py` ~404 строки, `ingest_agent.py` ~449 после сплита)
- API покрыт тестами, линтер покрыт 17 проверками
- Безопасность: sandbox усилен, контейнер не от root, auth middleware корректен

---

## 2. Последние изменения (HEAD~13 … HEAD)

| Коммит | Что сделано | Оценка |
|--------|-------------|--------|
| `Refine UI flows and update docs` | Переработаны UI-потоки, улучшен wiki-viewer, добавлен AuditModal с линтингом | ✅ Хорошо |
| `Feat: add /api/audit/lint endpoint` | Новый endpoint + structural lint panel в UI | ✅ Хорошо |
| `Fix: normalize_wikilinks` | Пост-обработка слитых ссылок `[[prefix/[[slug]]` + unlink несуществующих | ✅ Хорошо |
| `Fix: provenance markers` | Нормализация `^[...]` → `^[raw/...]`, проверка в линтере | ✅ Хорошо |
| `Fix: allow Unicode letters in slugs` | `slugify()` для LLM-генерации, поддержка Unicode | ✅ Хорошо |
| `Feat: harden code interpreter` | AST-анализ, resource limits, 10s timeout | ✅ Хорошо |
| `Fix: non-root container` | `appuser` в Docker, entrypoint для прав | ✅ Хорошо |
| `Fix: credit reset logic` | Сброс только если пользователь реально потратил токены | ✅ Хорошо |

---

## 3. Найденные и исправленные проблемы

### 🐛 Дублирующийся метод `_policy_comparison` в `query_agent.py`

**Файл:** `app/agents/query_agent.py`  
**Суть:** В файле присутствовали **две** сигнатуры метода `_policy_comparison`:

- Первая (строки ~241–275): содержала «index-first» логику с чтением `outline` для выбора лучших страниц.
- Вторая (строки ~279–298): упрощённая версия без outline-логики.

Вторая перезаписывала первую → **функциональность index-first для comparison-запросов была молча потеряна**.

**Исправление:** Удалён дублирующийся метод. Оставлена версия с outline-first логикой.  
**Проверка:** 308 тестов проходят.

---

## 4. Предупреждения (не критично, но стоит учесть)

### 4.1. FastAPI `@app.on_event("startup")` deprecated

- **Где:** `app/api/main.py:134`
- **Рекомендация:** перейти на `lifespan` event handlers (FastAPI ≥ 0.93)
- **Как:**
  ```python
  from contextlib import asynccontextmanager

  @asynccontextmanager
  async def lifespan(app: FastAPI):
      ...
      yield
      ...

  app = FastAPI(lifespan=lifespan)
  ```

### 4.2. Pydantic `extra keyword arguments on Field` deprecated

- **Где:** `app/api/models.py`
- **Суть:** `example=` → `json_schema_extra=` при миграции на Pydantic V3

### 4.3. Смешанные отступы в `app/ui/index.html`

- **Где:** строки ~292–318 и отдельные JSX-блоки
- **Суть:** Большая часть файла использует 2 пробела, но отдельные блоки содержат табы (`\t`).
- **Влияние:** Не ломает функциональность, но усложняет diff и review.
- **Рекомендация:** Прогнать через Prettier или `sed` для унификации.

---

## 5. Рекомендации по улучшению

### 5.1. Добавить regression-тест на `_policy_comparison`

Сейчас `test_api.py` проверяет API, но нет прямого теста на outline-first логику в query-агенте.  
Стоит добавить тест с mock `WikiFS`, который проверяет, что агент вызывает `read_page_outline` перед чтением полного контента.

### 5.2. Унифицировать отступы в `index.html`

Заменить оставшиеся табы на 2 пробела во всём файле.

### 5.3. Перейти с `@app.on_event` на Lifespan

Устранить DeprecationWarning и подготовить код к FastAPI будущих версий.

### 5.4. Добавить unit-тест на Unicode-слуги в `normalize_wikilinks`

Коммит разрешил Unicode в `validate_slug`, но `_SLUG_OK = re.compile(r"^[\w/-]+$")` в Python 3 поддерживает Unicode letters. Стоит защитить это поведение тестом с кириллическим slug (например, `[[проект/тест]]`).

---

## 6. Итог

| Показатель | Значение |
|------------|----------|
| Тесты | 308 passed, 0 failed |
| Критические баги | 0 (единственный найденный — дубль метода — удалён) |
| Код | Чистый, KISS/DRY, хорошо документирован |
| Безопасность | Sandbox, non-root, `secrets.compare_digest` |
| Техдолг | Минимальный (2 deprecation warning, смешанные отступы) |

---

*Сохранено автоматически из сессии ревью.*
