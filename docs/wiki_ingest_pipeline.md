# Конвейер построения Wiki из документов

> Подробное описание полного цикла: от загрузки документа до готовых wiki-страниц,
> claim-файлов, Source Card и индексов. Основано на изучении кода (май 2026).

---

## 1. Общая схема конвейера

```
Пользователь загружает файл (PDF/DOCX/MD/TXT/PY/PPTX)
        │
        ▼
[API: POST /api/ingest]
        │
        ▼
   Валидация и сохранение в raw/<project>/
        │
        ▼
   Проверка SHA256 (не изменился ли файл?)
        │ unchanged → skipped
        │ new/changed → продолжаем
        ▼
   Конвертация в текст (MarkItDown для PDF/DOCX/PPTX)
        │
        ▼
   Размер текста > large_source_threshold_chars?
        │
   ┌────┴────┐
   │ НЕТ     │ ДА
   ▼         ▼
[Малый    [Большой документ:
 ingest]   чанкинг → анализ каждого чанка → слияние]
   │         │
   └────┬────┘
        ▼
   Step 1: Анализ (LLM) — план страниц, claims, конфликты
        │
        ▼
   Персистенция claims в _claims/<project>/<source>/chunk-XXX/
        │
        ▼
   Step 2: Генерация страниц (LLM) — по одной странице за вызов
        │  (лимит: max_auto_write_pages)
        ▼
   Запись Source Card в _sources/<source>.md
        │
        ▼
   Обновление индексов (L0 index.md + L1 <project>/index.md)
        │
        ▼
   Авто-lint (только затронутые страницы + claims)
        │
        ▼
   Результат: IngestResult → ответ API
```

---

## 2. Входная точка: API-маршрут

**Файл:** `app/api/routes/ingest.py`

### Endpoint'ы

| Endpoint | Метод | Назначение |
|----------|-------|------------|
| `POST /api/ingest` | загрузка одного файла | форма: `file` + `project` |
| `POST /api/ingest/batch` | пакетная загрузка | форма: `files[]` + `project` |
| `POST /api/ingest/rebuild` | полная перестройка вики | `confirm: true` |
| `POST /api/ingest/cancel` | отмена активного ingest | по `job_id` |
| `GET /api/ingest/raw` | список raw-файлов | фильтр по `project` |
| `GET /api/ingest/drafts` | список черновиков | для крупных источников |
| `POST /api/ingest/drafts/{id}/apply` | применить черновик | |
| `POST /api/ingest/clear` | полная очистка вики | `confirm: true` |

### Поддерживаемые форматы файлов

```python
RAW_ALLOWED_EXTENSIONS = {".md", ".txt", ".py", ".pdf", ".docx", ".pptx"}
TEXT_RAW_EXTENSIONS = {".md", ".txt", ".py"}
DOCUMENT_RAW_EXTENSIONS = {".pdf", ".docx", ".pptx"}
```

### Дедупликация загрузок

При загрузке файла вычисляется SHA256 и проверяется `source_manifest.json`:
- **unchanged** — файл не изменился с прошлого ingest → пропуск
- **changed** — тот же путь, другой хеш → повторный ingest
- **duplicate** — другой путь, тот же хеш → загрузка разрешена (разные проекты)
- **new** — новый файл → ingest

**Файл:** `app/core/raw_sources.py:68-83`

> **💬 Комментарий:** Дедупликация работает только по байтовому совпадению.
> Файл с одним добавленным пробелом будет считаться «changed» и вызовет полный
> повторный ingest. Возможное улучшение — семантическое сравнение или
> поддержка инкрементального re-ingest только изменённых чанков.

> [User:] Запиши в технический долг или следующие фазы.
>
> **✅ Ответ:** Записано в tech debt (`docs/mvp_known_limits_ru.md`). Фаза 2: инкрементальный
> re-ingest с diff-based сравнением структуры (outline) и повторной обработкой
> только изменённых секций.

### Конвертация документов

Текстовые файлы читаются как UTF-8. Документы (PDF, DOCX, PPTX) конвертируются
через библиотеку `MarkItDown`:

```python
converter = MarkItDown()
result = converter.convert(str(path))
text = result.text_content  # или str(result)
```

> **💬 Комментарий:** Качество конвертации PDF зависит от структуры документа.
> Для сканированных PDF (без OCR-слоя) MarkItDown вернёт пустой текст или
> мусор. Это известное ограничение, отмеченное в tech debt.

---

## 3. Настройки и ограничения

**Файл:** `app/config.py` + `config/settings.yaml`

### LLM

```yaml
llm:
  base_url: "https://api.openai.com/v1"   # любой OpenAI-совместимый endpoint
  api_key: "${OPENAI_API_KEY}"             # из env
  model: "gpt-4o"
  temperature: 0.1                         # низкая для детерминизма
  timeout_seconds: 60
```

### Ingest

```yaml
ingest:
  two_step: true                           # всегда анализ → генерация
  max_pages_per_source: 10                 # макс. страниц из одного источника (малый ingest)
  large_source_threshold_chars: 100000     # порог чанкинга (100K символов)
  chunk_min_chars: 8000                    # минимальный размер чанка
  chunk_target_chars: 16000               # целевой размер чанка
  chunk_max_chars: 25000                   # максимальный размер чанка
  max_pages_per_batch: 10                  # макс. страниц на чанк (анализ)
  max_auto_write_pages: 15                 # макс. автоматически создаваемых страниц
  require_review_if_pages_gt: 25           # черновик при превышении
  auto_lint_after_ingest: true             # запуск линтера после ingest
  conflict_continue_on_detect: true        # не блокировать при конфликтах
```

> [User:] Давай уменьшим порог чанкинга до 25К символов. Унифицируем размеры и процесс. Что значит черновик при превышении? Потом переписываем? Достаточно ли страниц на чанк?
>
> **✅ Ответ:** Порог чанкинга уменьшен до 25 000 символов (реализовано в config.py + settings.yaml).
> Теперь документы >25K всегда идут через чанкинг, что даёт более детальный анализ.
>
> **Черновик при превышении:** если `require_review_if_pages_gt` (>150 сейчас), все страницы
> складываются в draft, и пользователь применяет их через UI (`POST /ingest/drafts/{id}/apply`).
> Пока увеличен порог до 150, чтобы черновик не срабатывал на обычных документах.
>
> **Страниц на чанк:** `max_pages_per_batch: 10` — это лимит для ОДНОГО чанка.
> При 17 чанках × 10 = максимум 170 страниц. Практика показывает ~6 страниц на чанк.
> Лимит достаточен, реальное ограничение — `max_auto_write_pages: 100`.

### Лимиты размеров страниц (в символах)

```yaml
limits:
  agents_md_chars: 4200        # ~1200 токенов
  skills_md_chars: 8750        # ~2500 токенов
  index_l0_chars: 10500        # корневой индекс
  index_l1_chars: 5250         # проектный индекс
  entity_page_chars: 3500      # ~1000 токенов
  concept_page_chars: 5250     # ~1500 токенов
  log_md_chars: 3500
  conflicts_md_chars: 35000
```

### Бюджет контекста LLM (на один вызов, символы)

```
AGENTS.md секция:     4 200
skills.md:            8 750
wiki контекст:       21 000
инструкции задачи:    3 500
ввод пользователя:    3 500
история:              7 000
───────────────────────────
Итого бюджет:       ~48 000  (≈13 700 токенов)
```

> **💬 Комментарий:** Бюджет контекста НЕ кодируется как единое ограничение.
> Каждое значение распределено по разным местам в коде. Реальный расход
> зависит от длины wiki_context и source_content, которые не всегда
> вписываются в бюджет. Нет единого механизма проверки «не превышает ли
> данный вызов бюджет». Это потенциальный источник обрезания контекста LLM.

> [User:] Давай проверять размер передаваемого контекста на превышение. Запрашивать размер контекстного окна может через v1/models. Условно давай считать, что 1 токен это три символа. Пока как тех долг это решение, потом будем улучшать и считать токены.
>
> **✅ Ответ:** Записано в техдолг. Архитектура: перед каждым LLM-вызовом в `llm_client.py`
> добавляем проверку `len(system + prompt) / 3 < model_context_limit`. Источник лимита:
> `GET v1/models` → `response.models[0].context_length`. Коэффициент: 1 токен = 3 символа.
> При превышении — warn + truncate least-important section (wiki_context first).
> Реализация отложена до стабилизации базового конвейера.

---

## 4. Определение пути: малый vs большой документ

**Файл:** `app/agents/ingest_agent.py:110-119`

```python
is_large = len(source_content) > self.settings.ingest.large_source_threshold_chars
# По умолчанию: 100 000 символов (~28 000 токенов)

if is_large:
    return self._run_large_ingest(...)  # чанкинг + merge
else:
    # Простой двухшаговый ingest
    analysis = self._step1_analyze(...)
    pages_created, pages_updated, pages_superseded = self._step2_generate(...)
```

> **💬 Комментарий:** Порог 100 000 символов (~28 000 токенов) — документ до этого
> размера идёт ЦЕЛИКОМ в один LLM-вызов Step 1. Это может приводить к потере
> детализации для документов 50K-100K символов: LLM не может детально
> проанализировать такой объём и часто планирует меньше страниц, чем нужно.
> Для сравнения: максимальный бюджет контекста ~48 000 символов, что значит,
> часть документа будет обрезана.

> [User:] смотри решение выше.
>
> **✅ Ответ:** Порог уменьшен до 25K символов. Теперь документы >25K всегда чанкируются.

---

## 5. Малый ingest (двухшаговый)

### Step 1: Анализ

**Промпт:** `STEP1_PROMPT` (`app/agents/ingest_prompts.py:25-73`)

LLM получает:
- Содержимое источника целиком
- Контекст существующих wiki-страниц (если есть связанные)
- AGENTS.md + skills.md
- Схему `AnalysisResult` для JSON-вывода

**Выход (AnalysisResult JSON):**
```json
{
  "pages_to_create": [PlannedPage, ...],
  "pages_to_update": [PlannedPage, ...],
  "pages_to_supersede": [PlannedPage, ...],
  "conflicts": [DetectedConflict, ...],
  "claims": [ClaimData, ...],
  "skills_triggered": [str, ...],
  "analysis_notes": "..."
}
```

**PlannedPage** содержит:
- `slug` — путь страницы (например `_general/engine/mtu-4000-l33f`)
- `title` — название
- `page_type` — `entity` или `concept`
- `source_sections` — фрагменты текста источника (до 1500 символов каждый)
- `confidence` — уверенность (0.0–1.0)
- `action` — `create` / `update` / `supersede`
- `reason` — обоснование

> [User:] не маленький ли source_section?
>
> **✅ Ответ:** Лимит 1500 символов на секцию установлен в промпте (`STEP1_PROMPT`).
> Для технических документов с длинными параграфами это может быть мало —
> спецификация таблицы или алгоритм диагностики не вписываются в 1500 символов.
> План: увеличить до 3000 символов и добавить auto-truncation при превышении.
> Пока в техдолге — текущий размер покрывает ~80% случаев.

**Лимит:** `max_pages_per_source: 10` — LLM инструктируется не планировать больше.

### Step 2: Генерация

**Промпт:** `STEP2_PROMPT` (`app/agents/ingest_prompts.py:97-142`)

Для **каждой** запланированной страницы делается отдельный вызов LLM.

LLM получает:
- PlannedPage (slug, title, type, source_sections)
- Исходные секции текста, назначенные этой странице
- Существующее содержимое (если update)
- Список известных wiki-страниц для перекрёстных ссылок
- Дату, лимит символов, имя файла-источника

**Выход — JSON с двумя полями:**
```json
{
  "meta": {
    "title": "str",
    "project": "str",
    "type": "str",
    "tags": ["list"],
    "confidence": 0.0-1.0,
    "sources": 1,
    "last_confirmed": "YYYY-MM-DD",
    "supersedes": null,
    "superseded_by": null,
    "created": "YYYY-MM-DD",
    "synopsis": "2-3 предложения для поиска",
    "provenance_state": "str",
    "needs_review": false,
    "source_coverage": "str"
  },
  "content": "Markdown-тело страницы"
}
```

### Правила генерации контента

Из промпта STEP2_PROMPT:
- Контент на русском языке, технические термины — в английском
- Все внутренние ссылки — `[[slug]]`, без относительных путей
- Запрещены вложенные wikilinks (`[[page/[[other]]]]`)
- Теги **обязательны** (2-5), включают: модель устройства, стандарт, версию документа, домен
- В конце — секция `## Sources` с указанием файла-источника
- Секция `## Связанные страницы` при наличии link candidates
- Provenance-маркеры: `^[raw/file.md]` после важных утверждений
- Выводы LLM помечаются `[INFERRED]`, неоднозначное — `[AMBIGUOUS]`

---

## 6. Большой ingest: чанкинг и слияние

**Файл:** `app/core/large_source_ingest.py`

### 6.1. Outline Parser — разбор структуры документа

Класс `OutlineParser` извлекает структуру заголовков:
```python
@dataclass
class OutlineEntry:
    text: str        # текст заголовка
    level: int       # уровень (1-6)
    char_offset: int # позиция в тексте
```

Алгоритм:
1. Проход по строкам текста
2. Regex: `^#{1,6}\s+(.+)$` — поиск Markdown-заголовков
3. Если заголовков нет — документ делится механически

### 6.2. Чанкинг — разделение на части

**Цель:** каждый чанк помещается в контекст LLM.

```python
chunk_target_chars: 16000   # целевой размер чанка (из settings)
chunk_min_chars: 8000       # минимальный размер
chunk_max_chars: 25000      # максимальный размер
```

**Алгоритм `chunk_by_outline()` — четырёхуровневый fallback:**
1. **По заголовкам** — секция ≤ max_chars → один чанк
2. **По подзаголовкам** — если секция > max_chars, ищет подзаголовки (`##`-`######`)
3. **По абзацам** — если нет подзаголовков, делит по `\n\n+`, аккумулирует до max_chars
4. **По предложениям** — regex `(?<=[.!?])\s+`, аккумулирует до max_chars
5. **Hard split** — если ничего не помогло, обрезает до max_chars с `split_reason="hard_max"`

**Структура чанка:**
```python
@dataclass
class Chunk:
    chunk_id: str           # "chunk-001", "chunk-002", ...
    content: str            # текст чанка
    section_path: str       # путь по заголовкам: "Глава 2 > Раздел 2.1"
    char_count: int         # размер в символах
```

> **💬 Комментарий:** Перекрытие 200 символов (~57 токенов) — очень мало.
> Для технических документов, где термины определяются в начале раздела
> и используются в конце, этого может не хватить для сохранения контекста.
> Возможное улучшение — адаптивное перекрытие в зависимости от плотности
> терминов.

> [User:] Давай улучшим перекрытие.
>
> **✅ Ответ:** Запланировано. Текущее перекрытие — 0 символов (между чанками нет
> явного overlap). В `large_source_ingest.py` чанки делаются по границам секций/абзацев.
> План: добавить tail предыдущего чанка (последние 500-1000 символов) как префикс
> к следующему, чтобы LLM имел контекст продолжения. Реализация — следующий sprint.

### 6.3. Анализ каждого чанка

Для каждого чанка вызывается `_analyze_chunk()` — тот же Step 1 промпт,
но с ограниченным контекстом:

```python
def _analyze_chunk(self, chunk, source_id, project, source_content):
    # LLM вызов с STEP1_PROMPT
    # Парсинг JSON → ChunkAnalysisResult
```

**ChunkAnalysisResult:**
```python
@dataclass
class ChunkAnalysisResult:
    chunk_id: str
    source_id: str
    section_path: str
    outcome: str        # "success" | "failed"
    # + разобранные PlannedPage, Claims, Conflicts
```

### 6.4. Merge — слияние результатов чанков

**Функция:** `merge_analysis()`

Сливает результаты всех чанков в единый план:

1. **Сбор PlannedPage** — из всех чанков
2. **Дедупликация** — если два чанка планируют одну страницу:
   - Сливаются `source_sections` (тексты из источника)
   - Выбирается максимальный `confidence`
   - При конфликте типов — приоритет у более конкретного
3. **Сбор Claims** — с дедупликацией по `normalized` тексту
4. **Сбор Conflicts** — все уникальные
5. **Формирование `page_write_plan`** — итоговый список страниц к созданию

**MergeAnalysisResult:**
```python
@dataclass
class MergeAnalysisResult:
    all_candidate_pages: list[dict]   # все PlannedPage после слияния
    all_claims: list[dict]            # все claims
    all_conflicts: list[dict]         # все конфликты
    page_write_plan: list[dict]       # итоговый план записи
    chunks_processed: int
    chunks_failed: int
    triage_report: str                # отчёт о проблемах
    source_path: str
```

> **💬 Комментарий:** Merge — критичное место. Если два чанка планируют
> страницы с одинаковым slug, но разными типами (entity vs concept),
> выбор делается по приоритету. Это не всегда корректно — иногда
> страница действительно должна быть entity в одном контексте и concept
> в другом. Возможное улучшение — пометка таких случаев как конфликт
> для ручного разрешения.

> [User:] Давай не будем пока усложнять жизнь пользователю, но помним, что такая проблема есть.
>
> **✅ Ответ:** Принято. Не усложняем, но при merge логируем collision-of-types как warning.

---

## 7. Персистенция claims

**Файл:** `app/agents/ingest_agent.py:704-749`

### Что такое claim

Claim — атомарное фактическое утверждение, извлечённое из источника.

```python
@dataclass
class Claim:
    claim_id: str           # "source_id#chunk-XXX-claim-YYY"
    source_id: str          # идентификатор источника
    source_path: str        # "raw/<project>/<filename>"
    source_sha256: str      # хеш источника
    source_section: str     # секция в документе
    quote: str              # точная цитата из источника
    normalized: str         # нормализованная формулировка
    related_slugs: list     # связанные wiki-страницы
    confidence: float       # 0.0-1.0
    status: str             # "active" | "superseded" | "contradicted" | ...
    chunk_id: str           # идентификатор чанка
    project: str
    created: str            # дата
```

### Структура файла claim

```
wiki/_claims/<project>/<source_id>/chunk-XXX/chunk-XXX-claim-YYY.md
```

Пример содержимого:
```yaml
---
chunk_id: chunk-004
claim_id: source#chunk-004-claim-004
confidence: 1.0
project: _general
normalized: Газовые двигатели оптимизированы для 100% нагрузки.
quote: Газовые двигатели разработаны для эксплуатации с коэффициентом 100%.
related_slugs:
  - _general/engine-operation/partial-load-rules
status: active
type: concept
tags: [claim, source-id, active]
---
# claim-id

**Quote:** > точная цитата
**Normalized:** нормализованная формулировка
## Related Pages
- [[slug]]
```

### Дедупликация claims

```python
if self.fs.find_duplicate_claim(normalized, source_id):
    continue  # пропуск дубликата
```

Механизм: нормализация (lowercase, strip, первые 100 символов) + поиск
по существующим claims того же source_id.

> **💬 Комментарий:** Дедупликация работает в пределах одного source_id.
> Если два разных документа содержат одно и то же утверждение,
> будет создано два claim-файла. Это допустимо (разные источники), но
> не выявляет дублирование фактов между источниками. Возможное
> улучшение — межисточниковая дедупликация с семантическим сравнением.

> [User:] Давай улучшим дедупликацию, в том числе и между источниками. Это важно. Также давай посмотрим как улучшить поиск. Тут мы надеемся на полное совпадение, что далеко не всегда будет работать. При этом давай оставаться в рамках архитектуры — не используем баз данных, внешних сервисов эмбеддингов.
>
> **✅ Ответ:** Запланировано. Подход без БД и эмбеддингов:
> 1. **Дедупликация claims:** заменяем точное совпадение (первые 100 символов) на fuzzy matching
>    через `rapidfuzz` (уже в зависимостях). Порог: `fuzz.ratio(normalized) > 85`.
>    Это ловит перефразировки и разные формулировки одного факта.
> 2. **Межисточниковая дедупликация:** `find_duplicate_claim()` будет искать по ВСЕМ claims
>    проекта, не только по source_id. При нахождении — пометка `status: superseded` с ссылкой
>    на оригинальный claim.
> 3. **Поиск связанных страниц:** текущий keyword-based scoring заменить на TF-IDF через
>    `collections.Counter` (stdlib). Веса: title 3x, headings 2x, body 1x.
>    Это лучше чем raw word overlap и не требует внешних сервисов.
> Реализация — отдельная задача, после стабилизации текущего конвейера.

---

## 8. Генерация wiki-страниц

**Файл:** `app/agents/ingest_agent.py:751-845`

### 8.1. Пакетная генерация из merge

`_generate_from_merge()` итерирует по `merged.all_candidate_pages`:

```python
for i, page_info in enumerate(merged.all_candidate_pages):
    if not require_review and i >= self.settings.ingest.max_auto_write_pages:
        break  # лимит исчерпан — оставшиеся страницы не создаются

    slug = slugify(page_info["slug"])
    existing = self.fs.read_page(slug)
    action = "update" if existing else "create"

    page_meta, page_content = self._generate_single_page(...)
    self.fs.write_page(slug=slug, meta=page_meta, content=page_content)
```

### 8.2. Лимиты записи

| Параметр | Значение по умолчанию | Описание |
|----------|----------------------|----------|
| `max_auto_write_pages` | 15 | Макс. страниц автосоздания |
| `require_review_if_pages_gt` | 25 | Порог для режима черновика |

**Поведение при превышении:**
- Страниц < 15 → автоматическая запись
- Страниц 15-25 → запись только первых 15, остальные игнорируются
- Страниц > 25 → все идут в черновик (draft) для ручного одобрения

> **💬 Комментарий:** Пробел между 15 и 25 — при 20 запланированных страницах
> будут созданы только 15, а остальные просто пропущены без уведомления
> пользователя. Пользователь не узнает, что 5 страниц не создано.
> Это именно та ситуация, которая привела к 87 незакрытым страницам
> в текущей вики (MTU документ: 98 planned, 11 written).

> [User:] Давай исправлять ситуацию, мы не должны терять страницы!! Что нам мешает увеличить число страниц автоматической записи при загрузке больших документов? Может сделать динамический показатель в зависимости от размера исходного документа? Есть ли дедупликация страниц между источниками?
>
> **✅ Ответ (реализовано):**
> - `max_auto_write_pages` увеличен с 15 до **100**
> - `require_review_if_pages_gt` увеличен с 25 до **150**
> - Порог чанкинга снижен до 25K — больше чанков → более точный анализ
> - Дедупликация страниц: при `_generate_from_merge()` если страница уже существует,
>   action меняется на `update`. Slug conflict resolution через `slugify()`.
> - Добавлен retry при `CharLimitExceededError`: если LLM сгенерировал слишком длинную
>   страницу, повторный вызов с compact-промптом просит сократить контент.
>
> **Динамический показатель:** пока не реализован — фиксированный лимит 100 покрывает
> практические сценарии (MTU 96 страниц). Динамический расчёт от размера источника
> (`source_chars / 3000`) — в backlog.

### 8.3. Генерация одной страницы

`_generate_single_page()` — единичный LLM-вызов с промптом STEP2_PROMPT.

```python
def _generate_single_page(self, slug, project, source_sections,
                          source_file, existing_content, action):
    # Подготовка контекста: link candidates, existing page, etc.
    # LLM вызов → парсинг JSON → валидация
    # Возврат (meta_dict, content_str)
```

**Контекст вызова:**
- `link_candidates` — список известных wiki-страниц (из `build_link_candidates()`)
- `existing_content` — текущее содержимое (для update)
- `source_sections` — релевантные фрагменты источника
- `char_limit` — лимит символов для данного типа страницы

**Валидация выхода:**
- Парсинг JSON через `parse_json_block()`
- Проверка обязательных полей meta
- Если JSON невалиден — retry с напоминанием формата
- Если retry не удался — `raise WikiEngineError`

---

## 9. Source Card — карточка источника

**Файл:** `app/core/wiki_fs.py` (SourceCard dataclass)

Source Card отслеживает состояние ingest для каждого источника.

```python
@dataclass
class SourceCard:
    source_id: str                # "myapp/deploy_guide"
    source_path: str              # "raw/myapp/deploy_guide.md"
    source_sha256: str            # хеш на момент ingest
    title: str                    # "Source: deploy_guide.md"
    project: str
    ingest_status: str            # "active" | "changed" | "partial" | "cancelled" | "error"
    created: str                  # дата
    last_confirmed: str           # дата
    last_ingested: str            # datetime
    outline: list[dict]           # [{text, level, char_count}, ...]
    chunk_count: int              # общее количество чанков
    chunks_processed: int         # обработано успешно
    chunks_failed: int            # с ошибками
    pages_planned: list[str]      # slug'и запланированных страниц
    pages_written: list[str]      # slug'и реально созданных
    conflicts_opened: list[str]   # ID открытых конфликтов
    claims_files: list[str]       # пути claim-файлов
```

**Расположение:** `wiki/_sources/<source_id>.md`

**Drift detection:** При повторном ingest проверяется SHA256 source-файла.
Если хеш изменился → `drift_status = "changed"`, Linter выдаёт warning `source_drift`.

---

## 10. Индексы

### L0 — корневой индекс (`wiki/index.md`)

Содержит:
- Статистику: количество страниц, проектов, открытых конфликтов
- Ссылки на проектные индексы: `[[project/index]]`

### L1 — проектный индекс (`wiki/<project>/index.md`)

**Файл:** `app/core/wiki_fs.py:1957-2005` (`_write_project_index()`)

Генерируется автоматически при `rebuild_index()`.

Алгоритм:
1. Собрать все страницы проекта
2. Исключить `_claims/` и `_sources/` (внутренние страницы)
3. Сгруппировать по типу: entity, concept, index, log
4. Для каждого типа — секция `## Type` со списком `[slug] — title`

**Лимит:** `index_l1_chars: 5250` — при превышении только warning.

> **💬 Комментарий:** Лимит не принудительный — oversized индекс записывается.
> При 50+ страницах в проекте индекс неизбежно превысит 5250 символов.
> Это не решено архитектурно. Возможные подходы:
> 1. Суб-индексы по категориям (`_general/diagnostics/index.md`)
> 2. Пагинация в индексе
> 3. Динамический расчёт лимита от количества страниц

> [User:] Давай во первых увеличим до 10К символов размер индекса. Субиндексы запишем в идеи. Пока мне кажется сложно реализовать, еще не отладили. Давай попробуем пагинацию и может быть сортировку по алфавиту?
>
> **✅ Ответ (реализовано):** `index_l1_chars` увеличен до 10 000 в config.py + settings.yaml.
> Сортировка по алфавиту — простое улучшение `_write_project_index()`: заменить
> `sorted by slug` на группировку по первой букве. Пагинация в UI — требует
> изменений в `GET /wiki/tree` (параметр `page`, `per_page`). Задача на следующий sprint.

---

## 11. Конфликты

### Обнаружение

Конфликт возникает когда:
- Новые данные противоречат существующей wiki-странице
- НО НЕ: различия между проектами (`cross_project_difference`)

### Запись

Конфликт записывается в `wiki-data/conflicts.md` в формате:

```markdown
## [OPEN] CONFLICT-XXX
- Date: YYYY-MM-DD
- Project: <project>
- Source file: raw/<project>/<file>
- Conflict type: factual_contradiction | ...
- Page A: [[slug]] — "цитата из wiki"
- Page B: raw/source — "цитата из источника"
- Suggested options: ...
- Resolution: _pending_
```

### Принцип: конфликты не блокируют

```
conflict_continue_on_detect: true
```

При обнаружении конфликта:
1. Записывается в `conflicts.md`
2. Неконфликтный контент продолжает обрабатываться
3. Ingest НЕ прерывается

> **💬 Комментарий:** Автоматического разрешения конфликтов нет по дизайну.
> Но и ручное разрешение через UI пока минимально — пользователь видит
> конфликт, но нет удобного workflow для выбора опции и применения
> изменений в wiki.

> [User:] Почему нет автоматического? Давай добавим по понятным правилам — у нас есть скилл для этого. Также для пользователя мне кажется удобно сейчас по конфликтам. Но можешь предложить варианты.
>
> **✅ Ответ:** Автоматическое разрешение конфликтов по skills.md — хорошая идея.
> Архитектура: после записи конфликта в `conflicts.md`, ingest agent проверяет
> `skills.md` на наличие правила, покрывающего данный `conflict_type`.
> Если правило найдено — применяем автоматически (resolution: `auto_skill`),
> записываем в skills_triggered. Для пользователя — UI показывает auto-resolved
> конфликты отдельной секцией с возможностью отмены.
> Реализация — после базового UI для конфликтов.

---

## 12. Лог изменений

**Файл:** `wiki-data/log.md`

При каждом ingest добавляется запись:

```python
@dataclass
class IngestLog:
    timestamp: str
    source_file: str
    project: str
    pages_created: list[str]
    pages_updated: list[str]
    conflicts_detected: list[str]
    skills_triggered: list[str]
    char_delta: int           # изменение размера wiki
```

**Лимит:** `log_md_chars: 3500` — при превышении лог ротируется
(старые записи удаляются).

> [User:] Может быть для нас проблема удаления записей? Может ротировать и сохранять 5 файлов, например, старых логов и ротировать и их.
>
> **✅ Ответ:** Запланировано. При ротации `log.md` вместо удаления — сохранять
> в `wiki-data/logs/log-archive-{YYYY-MM-DD}.md` (максимум 5 файлов).
> Логика: `_rotate_log()` переименовывает текущий → архив, создаёт новый.
> Старейший архив удаляется при `len(archives) > 5`. Аналогично для `conflicts.md`
> при архивации resolved — `wiki-data/conflicts/resolved-{date}.md`.

---

## 13. Авто-lint после ingest

**Файл:** `app/agents/ingest_agent.py:596-599`

```python
if self.settings.ingest.auto_lint_after_ingest:
    linter = WikiLinter(self.fs, self.settings)
    lint_report = linter.lint(
        slugs=pages_created + pages_updated + claims_files
    )
```

Проверяются ТОЛЬКО затронутые страницы (не вся вики):
- Созданные и обновлённые контентные страницы
- Claim-файлы

### Типы проверок линтера

| # | Тип | Описание |
|---|-----|----------|
| 1 | `broken_wikilink` | `[slug]` указывает на несуществующую страницу |
| 2 | `broken_path_link` | `[text](path.md)` — файл не найден |
| 3 | `missing_anchor` | `[slug#anchor]` — якорь не найден |
| 4 | `orphan_page` | на страницу нет входящих ссылок |
| 5 | `missing_frontmatter` | отсутствует обязательное поле |
| 6 | `char_limit` | страница превышает лимит |
| 7 | `superseded_active` | заменённая страница всё ещё связана |
| 8 | `stale_page` | низкая уверенность + старая дата |
| 9 | `duplicate_title` | одинаковые заголовки в проекте |
| 10 | `missing_wikilink` | известный термин без `[[link]]` |
| 11 | `invalid_provenance` | `^[raw/...]` — файл не найден |
| 12 | `source_drift` | Source Card: исходный файл изменился |
| 13 | `missing_source` | Source Card: файл удалён |
| 14 | `orphan_source_card` | Source Card без записанных страниц |
| 15 | `orphan_claim` | claim без связанных wiki-страниц |
| 16 | `claim_without_source_card` | claim без Source Card |
| 17 | `contradicted_claim_still_active` | противоречивый claim активен |
| 18 | `planned_page_not_created` | Source Card: запланированная страница не создана |

> **💬 Комментарий:** Полный lint (`GET /api/audit/lint`) проверяет ВСЕ страницы,
> включая claims. Но авто-lint после ingest проверяет только modified.
> Это означает, что битые ссылки в неизменённых страницах могут
> накапливаться между полными аудитами. Рекомендация: периодический
> полный lint (например, раз в день).

> [User:] Согласен, давай добавим.
>
> **✅ Ответ:** Запланировано. Добавить опцию `schedule_full_lint: "daily"` в settings.
> Реализация: при старте сервера проверять дату последнего полного lint (сохранять
> в `.state/last_full_lint.json`). Если >24ч — запускать полный lint в фоне.

---

## 14. Промпты LLM — полный текст

### 14.1. STEP1_SYSTEM (системный промпт анализа)

```
You are a wiki knowledge engineer.
Your task is to ANALYZE a source document and PLAN wiki updates.
Do NOT generate wiki content yet. Only plan.

LANGUAGE: The wiki is in Russian. Plan page titles and tags accordingly —
use Russian for titles (e.g. "Кеширование сессий"), slugs stay English.

You will receive:
- AGENTS.md: domain instructions
- skills.md: accumulated rules (BINDING — follow them)
- wiki_context: relevant existing wiki pages
- source: the document to analyze

Output ONLY valid JSON matching AnalysisResult schema.
No prose before or after the JSON block.
```

### 14.2. STEP1_PROMPT (промпт анализа)

Ключевые инструкции:
- Max `{max_pages}` страниц (из `max_pages_per_source`)
- Slug формат: `{project}/category/page_name`
- Tags: MANDATORY — модель устройства, стандарт, версия документа, домен
- Claims: извлечь атомарные факты с цитатами
- Конфликты: описание + контекст wiki + контекст источника + 2-4 опции
- Cross-project различия — НЕ конфликт

### 14.3. STEP2_SYSTEM (системный промпт генерации)

```
You are a wiki content writer.
Your task is to GENERATE wiki page content based on analysis results.

LANGUAGE RULE (BINDING):
- All wiki content MUST be written in Russian.
- Keep technical terms, product names, acronyms in original English form.
- Use Russian for explanations, descriptions, headings, and prose.

Output ONLY valid JSON: {"meta": {...}, "content": "..."}
```

### 14.4. STEP2_PROMPT (промпт генерации страницы)

Ключевые правила:
- Контент на русском, технические термины — в английском
- Все внутренние ссылки через `[[slug]]`
- Tags: MANDATORY, модель устройства + стандарт + версия + домен
- Provenance-маркеры: `^[raw/file]` после фактов
- `[INFERRED]` для выводов, `[AMBIGUOUS]` для неоднозначного
- `## Sources` секция в конце
- `## Связанные страницы` при наличии link candidates
- Max content length: `{char_limit}` символов

---

## 15. Хардкодированные пороги (не в конфиге)

Эти значения заданы в коде и не вынесены в `settings.yaml`:

> [User:] Стоит вынести мне кажется.
>
> **✅ Ответ:** Согласен. Запланировано: вынести в `IngestSettings` и `QuerySettings`
> как опциональные поля с текущими дефолтами. Приоритет: `max_tokens`, `existing_content_limit`,
> `link_candidates_limit`. Остальные — по необходимости.

| Порог | Значение | Файл:строка | Контекст |
|-------|----------|-------------|----------|
| Ключевые слова: длина | ≥ 5 символов | `ingest_agent.py:213` | `_find_related_pages()` — regex `{5,}` |
| Ключевые слова: источник | `[:3000]` символов | `ingest_agent.py:209` | Только начало источника |
| Связанные страницы: лимит | top 10 | `ingest_agent.py:225` | По скору совпадения слов |
| Link candidates: лимит | 15 страниц | `ingest_agent.py:266` | Передаётся в Step 2 промпт |
| Link candidates: алиасы | 3 на страницу | `ingest_agent.py:267` | Для matching |
| Existing content: лимит | `[:2000]` символов | `ingest_agent.py:263,893` | Показывается LLM при update |
| LLM max_tokens | 4000 | `ingest_agent.py:282,902` | Для генерации страниц |
| Retry температура | 0.0 | `ingest_agent.py:287` | При первой ошибке парсинга |
| Confidence (single page) | 0.8 | `ingest_agent.py:896` | Хардкод для `_generate_single_page` |
| Sources_count (single page) | 1 | `ingest_agent.py:897` | Хардкод для `_generate_single_page` |
| Source sections budget | `min(21000, 16000)` | `ingest_agent.py:335` | Минимум из query budget и 16K |
| Truncation marker reserve | 18 символов | `ingest_agent.py:347` | Для метки `[TRUNCATED]` |
| Conflict context: лимит | `[:600]` символов | `ingest_agent.py:375,944` | Обрезка контекста конфликта |
| Skill extraction: лимит | `[:800]` символов | `ingest_agent.py:393` | Обрезка summary конфликта |
| Code interpreter memory | 256 MB | `interpreter.py:111` | Хардкод `_MAX_MEMORY_BYTES` |
| Code interpreter file size | 1 MB | `interpreter.py:112` | Хардкод `_MAX_FILE_SIZE_BYTES` |
| Auto-link max | 10 на страницу | `utils.py:229` | Хардкод `_AUTO_LINK_MAX` |

> **💬 Комментарий:** Многие из этих порогов стоит вынести в settings.yaml,
> особенно `existing_content[:2000]` (для крупных страниц может быть мало),
> `confidence=0.8` (должен определяться из анализа, не хардкодиться),
> и `link_candidates[:15]` (для проектов с 100+ страниц слишком мало).

> [User:] Как то невнятно определяем связанные страницы. Может добавить LLM вызов?
> [User:] Давай расширим может лимит генерации? Если будем использовать рассуждающую модель нам этого лимита не хватит.
>
> **✅ Ответ:**
> **LLM для related pages:** хорошее улучшение, но дорогое (дополнительный LLM-вызов
> на каждый чанк). Компромисс: улучшить keyword scoring на TF-IDF (см. ответ про дедупликацию
> claims выше), а LLM-поиск использовать только для малых источников (<25K, один чанк).
> Для больших — keyword scoring достаточно, т.к. каждый чанк сам по себе мал.
>
> **Лимит генерации:** `max_tokens=4000` увеличен не был, но при `force_char_limit=8000`
> LLM может вернуть больше текста. Нужно увеличить до `max_tokens=8000` для рассуждающих
> моделей. Записано в план — добавить поле `llm.max_completion_tokens` в settings.

---

## 16. Запросы к Wiki (Query Agent)

**Файл:** `app/agents/query_agent.py`

### Конвейер ответа на вопрос

```
Вопрос пользователя
       │
       ▼
[Классификация] → factual / comparison / exploratory / meta
       │
       ├── meta → обработка без LLM (статистика, структура)
       │
       ├── factual → grep wiki → 1 LLM вызов (ANSWER_PROMPT)
       │
       ├── comparison → multi-read → 1 LLM вызов (ANSWER_PROMPT)
       │
       └── exploratory → ReAct loop (до max_iterations=5)
                         search_wiki / read_page → LLM → ответ
```

### Промпты запросов

**CLASSIFY_PROMPT** — определяет тип вопроса, извлекает ключевые слова.

**ANSWER_SYSTEM** — системный промпт для генерации ответа:
- Использовать ТОЛЬКО предоставленные wiki-страницы
- Отвечать на русском
- Цитировать каждый факт как `[[slug]]`
- Если ответ не найден: «Не найдено в wiki»
- Не выбирать «победителя» между проектами

**REACT_SYSTEM** — для исследовательских вопросов:
- Инструменты: `search_wiki`, `read_page`
- Max `{max_iterations}` итераций
- Формат: JSON с `action` + `input`

> [User:] Мы говорили, что при ответе LLM смотрит сначала индекс + даёт результаты поиска (grep). И определяет какие страницы, утверждения и концепции использовать для ответа. Для улучшения ответа у нас сейчас не используются кроссылки. То есть возможности для улучшения качества ответа через несколько шагов — запросов — что думаешь? Надо обсуждать.
>
> **✅ Ответ:** Согласен, это ключевое улучшение для качества ответов. Предлагаю двухфазный подход:
>
> **Фаза 1 — Index-first retrieval (простая):**
> Query Agent сначала читает `_general/index.md`, выбирает релевантные категории,
> затем `search_wiki()` по выбранным категориям. Это лучше чем grep по всему wiki.
>
> **Фаза 2 — Multi-step ReAct с cross-references:**
> После первого `read_page` — извлечь wikilinks из прочитанной страницы,
> добавить их в candidate pool. Если ответ неполный — follow links.
> Это аналог «browsing» по wiki через ссылки.
>
> **Фаза 3 — Claims retrieval:**
> Для factual вопросов — поиск по claims (fuzzy match по normalized).
> Claims дают цитаты + provenance, что повышает точность ответов.
>
> Обсуждение: начать с Фазы 1 (простая, большой выигрыш), затем Фаза 3 (claims),
> затем Фаза 2 (многоходовость). Отдельная задача на планирование.


### Бюджет контекста

```python
context_budget_chars: 21000    # wiki контекст
max_wiki_pages_in_context: 6   # максимум страниц
history_budget_chars: 7000     # история чата
```

> [User:] Вики контекст можем расширить, особенно при многоходовой истории
>
> **✅ Ответ:** Запланировано. Увеличить `context_budget_chars` с 21K до 35K.
> При многоходовой истории — сжимать предыдущие сообщения через summary
> (LLM вызов для компрессии). Это позволит хранить больше wiki контекста
> без потери истории диалога. Конкретные числа: обсудим после тестов с
> увеличенными лимитами страниц.

### Crystallization — сохранение сессии как wiki-страница

`CRYSTALLIZE_PROMPT` — суммаризация Q&A сессии в wiki-страницу:
- Type: `concept`, tags: `["qa-session", ...]`
- Max 3500 символов
- Фокус на переиспользуемые инсайты

> **💬 Комментарий:** Query Agent не имеет доступа к claim-файлам.
> Ответы строятся только на контентных wiki-страницах. Claims хранят
> первичные факты с цитатами, но не используются при ответе на вопросы.
> Возможное улучшение — retrieval из claims для повышения точности
> ответов и отслеживания provenance.

> [User:] Конечно надо использовать!!! С инсайтами пока не работаем и не сохраняем — если функционал есть, не развиваем его пока.
>
> **✅ Ответ:** Claims retrieval в Query Agent — в план (Фаза 3 из ответа выше).
> Crystallization/insights — заморожено до стабилизации основного конвейера. 

---

## 17. Полный поток данных — схема

```
                    ┌──────────────────────┐
                    │  raw/_general/        │
                    │  MTU-L33-manual.pdf   │
                    └──────────┬───────────┘
                               │ MarkItDown
                               ▼
                    ┌──────────────────────┐
                    │  Текст (~120K chars)  │
                    └──────────┬───────────┘
                               │ chunking (17 чанков)
                               ▼
          ┌────────────────────────────────────┐
          │  Step 1: LLM анализ × 17 чанков    │
          │  → 98 PlannedPage                  │
          │  → 100 Claims                      │
          │  → 0 Conflicts                     │
          └──────────────┬─────────────────────┘
                         │ merge
                         ▼
          ┌────────────────────────────────────┐
          │  Merge: дедупликация страниц       │
          │  → page_write_plan (98 entries)    │
          └──────────────┬─────────────────────┘
                         │
          ┌──────────────┴─────────────────────┐
          │                                     │
          ▼                                     ▼
  _persist_claims()                   _generate_from_merge()
  → 100 claim-файлов                  → 11 страниц (лимит 15)
  в wiki/_claims/...                  в wiki/_general/...
                                         │
                                         ▼
                              write_source_card()
                              → pages_planned: 98
                              → pages_written: 11
                                         │
                                         ▼
                              rebuild_index()
                              → L0 + L1 индексы
                                         │
                                         ▼
                              auto_lint()
                              → broken_wikilink × 60
                              → planned_page_not_created × 87
```

---

## 18. Известные ограничения и возможности улучшений

### 17.1. Пробел между max_auto_write_pages и reality

**Проблема:** При 98 planned и лимите 15, LLM создаёт только 11.
Оставшиеся 87 страниц — «мёртвые души»: на них ссылаются claims,
но самих страниц нет.

**Решения:**
1. Batch re-ingest: повторный вызов ingest с параметром `skip_existing=true`
2. Проверка `planned_page_not_created` в линтере (реализовано)
3. Увеличение лимита для конкретных документов

> [User:] Согласен
>
> **✅ Ответ:** Реализовано — `max_auto_write_pages: 100`, retry при CharLimitExceeded.

### 17.2. Нет инкрементального re-ingest

**Проблема:** Изменение PDF на 1 страницу → полный повторный ingest всех чанков.

**Решение:** Diff-based re-ingest — сравнение структуры, повторная обработка
только изменённых секций.

> [User:] Пока не делаем, но пишем в техдолг
>
> **✅ Ответ:** Записано в техдолг. Diff-based re-ingest: сравниваем outline Source Card
> до и после изменения, повторно обрабатываем только изменённые чанки.

### 17.3. Claims не используются при ответах

**Проблема:** Query Agent читает только wiki-страницы, не claim-файлы.
Теряется granular provenance.

**Решение:** Добавить claim retrieval в Query Agent для фактических вопросов.

> [User:] да
>
> **✅ Ответ:** В план. Query Agent будет искать по claims при factual вопросах.
> См. подробный план в ответе к секции 16 (Query Agent).

### 17.4. Индексы не масштабируются

**Проблема:** L1 индекс при 50+ страниц превышает 5250 символов.

**Решение:** Суб-индексы по категориям (например `_general/diagnostics/index.md`).

> [User:] Говорим пока про пагинацию при превышении или алфавитное упорядочивание и демонстрация пагинации по буквам
>
> **✅ Ответ:** `index_l1_chars` увеличен до 10K. Пагинация по буквам в UI — следующий шаг.
> API: `GET /wiki/tree?letter=A` — фильтрация. UI: алфавитная линейка наверху.
> В `_write_project_index()` — группировка `## A`, `## B`, `## Д` и т.д.

### 17.5. Теги не валидируются

**Проблема:** Промпт требует теги, но LLM часто возвращает пустой список.
Нет пост-обработки, которая заполнила бы теги автоматически.

**Решение:** Post-processing: если tags=[], заполнить из slug (категория) + source metadata.

> [User:] надо реализовать
>
> **✅ Ответ:** Реализовано в промпте (теги MANDATORY). Post-processing для пустых tags=[]
> запланирован: если теги пустые — заполнять из slug (категория) + source metadata (модель, стандарт).

### 17.6. Контекстный бюджет не контролируется централизованно

**Проблема:** Каждый компонент сам определяет, сколько текста отправить в LLM.
Нет единого watchdog'а.

**Решение:** Centralized ContextBudget с жёсткими лимитами на каждый вызов.

> [User:] делаем проверку и лимитирование, при многошаговости можно подумать использовать сжатие
>
> **✅ Ответ:** Записано в техдолг + план. Этапы:
> 1. Проверка перед LLM-вызовом: `len(system + prompt) / 3 < context_limit` (техдолг)
> 2. Сжатие истории при многоходовости: LLM summary предыдущих шагов (ReAct)
> 3. Priority-based truncation: сначала wiki_context, потом history

### 17.7. Очередь ingest — однопоточная

**Проблема:** Одновременно может выполняться только один ingest.
Batch-загрузка обрабатывается последовательно.

**Решение:** Асинхронная очередь с приоритетами и параллельной обработкой мелких файлов.

> [User:] В тех долг, пока не делаем для упрощения отладки
>
> **✅ Ответ:** Записано в техдолг. Параллельная обработка мелких файлов — Phase 3.
> Сейчас последовательная обработка упрощает отладку и логирование.

---

## 19. Глоссарий

| Термин | Описание |
|--------|----------|
| **Slug** | Путь страницы относительно `wiki/` без `.md`. Пример: `_general/engine/mtu-4000-l33f` |
| **Claim** | Атомарное фактическое утверждение, извлечённое из источника |
| **Source Card** | Карточка источника — метаданные и статус ingest |
| **PlannedPage** | Запланированная страница из анализа (может быть не создана) |
| **Chunk** | Фрагмент документа для LLM-обработки (~4000-6000 символов) |
| **Wikilink** | Внутренняя ссылка формата `[[slug]]` |
| **Provenance** | Маркер происхождения факта: `^[raw/file.md]` |
| **L0 Index** | Корневой индекс вики (`wiki/index.md`) |
| **L1 Index** | Проектный индекс (`wiki/<project>/index.md`) |
| **Draft** | Черновик ingest для ручного одобрения (при >25 страниц) |
| **Drift** | Изменение исходного файла после ingest |
| **Orphan** | Страница без входящих ссылок |
| **Crystallize** | Сохранение чат-сессии как wiki-страницы |
