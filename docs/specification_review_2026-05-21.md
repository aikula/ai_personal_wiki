# Ревью спецификации Large-Scale Wiki Engine

Дата: 2026-05-21

Статус после исправлений: замечания 1, 2, 3, 4, 5, 6 и 7 закрыты в коде. Проверка: `231 passed`; ruff по измененным файлам проходит.

## Итог

Спецификация в целом реализована в ключевых частях: есть Source Cards, claims layer, outline/section query tools, large source ingest и typed page updates. 
Но несколько пунктов из спецификации реализованы частично или с оговорками, и это влияет на поведение в production.

## Замечания

### 1. Отсутствует VPS auth, описанный в спецификации

Спецификация требует опциональную защиту через `WIKI_AUTH_ENABLED`, `WIKI_AUTH_USERNAME`, `WIKI_AUTH_PASSWORD`. 
Сейчас загрузка конфигурации читает только `LANGUAGE`, LLM-переменные и `WIKI_DATA_PATH` в [app/config.py](/root/dev/ai_personal_wiki/app/config.py:108), 
а FastAPI-приложение в [app/api/main.py](/root/dev/ai_personal_wiki/app/api/main.py:39) не добавляет middleware/dependency для Basic Auth.

Риск:
- при запуске на VPS UI и API остаются открытыми;
- спецификация формально не выполнена.

### 2. Large ingest все еще обрезает контекст при генерации страниц

В ingest pipeline chunk analysis уже идет по частям, но генерация страницы использует полный source и затем дополнительно 
режет его до 3000 символов в [app/agents/ingest_agent.py](/root/dev/ai_personal_wiki/app/agents/ingest_agent.py:702-709). 
Это означает, что страницы могут строиться не из релевантных секций chunk-а, а из усеченного общего контекста.

Риск:
- факты из середины и конца больших источников могут не попасть в page generation;
- поведение расходится с требованием "не обрезать знания под context window".

> [user]: насколько я помню, мы планировали в первую очередь искать заголовки Маркдаун и резать по ним и уже позже использовать фолбэк просто нарезку, если не получится. 
Обязательно исключить потерю контекста!

### 3. Claims слой не записывается в wiki-data при ingest

Chunk analysis возвращает `claims` в [app/agents/ingest_agent.py](/root/dev/ai_personal_wiki/app/agents/ingest_agent.py:582-589), 
но в ingest flow они не пишутся через `write_claim()` из [app/core/wiki_fs.py](/root/dev/ai_personal_wiki/app/core/wiki_fs.py:1227). 
Source Card тоже не получает список `claims_files`.

Риск:
- `_claims` существует как механизм, но не как полноценно используемый ingest-артефакт;
- provenance/conflict/drift-логика на claims остается неполной.

### 4. Review threshold объявлен, но не блокирует автозапись

В [app/agents/ingest_agent.py](/root/dev/ai_personal_wiki/app/agents/ingest_agent.py:614-623) при превышении `require_review_if_pages_gt` 
формируется warning, но ingest все равно продолжает auto-write до `max_auto_write_pages`.

Риск:
- крупные semantic изменения могут попасть в wiki без обязательного review;
- это противоречит спецификации для больших источников.

### 5. Section retrieval не учитывает вложенные подзаголовки

`read_page_section()` в [app/core/wiki_fs.py](/root/dev/ai_personal_wiki/app/core/wiki_fs.py:694-754) останавливается на 
следующем heading любого уровня. Для секции `## Deployment` это скрывает `### Docker`, `### Env` и другие вложенные подзаголовки.

Риск:
- query-agent может не увидеть факты, которые находятся в дочерних секциях;
- это снижает качество outline/section-first retrieval.

### 6. Chunk section_path может искажать иерархию

В [app/core/large_source_ingest.py](/root/dev/ai_personal_wiki/app/core/large_source_ingest.py:268-284) построение `section_path` 
проходит по всем предыдущим headings и вставляет их в начало path. Для глубоко вложенных секций порядок может стать неестественным.

Риск:
- в claims/chunk prompts попадает неточная иерархия секции;
- provenance для чанков становится менее надежным.

### 7. Env overrides для chunking не подключены

Спецификация требует env-переопределения для размеров чанков, но [app/config.py](/root/dev/ai_personal_wiki/app/config.py:108-123) их не читает.

Риск:
- параметры крупного ingest нельзя менять без правки yaml;
- развертывание на VPS и в CI менее гибкое.

## Что уже реализовано

- Source Cards и drift primitives есть в [app/core/wiki_fs.py](/root/dev/ai_personal_wiki/app/core/wiki_fs.py:1038-1221).
- Claims слой есть в [app/core/wiki_fs.py](/root/dev/ai_personal_wiki/app/core/wiki_fs.py:1227-1401) и покрыт тестами.
- `read_page_outline`, `read_page_section`, `multi_read_sections` реализованы в [app/core/wiki_fs.py](/root/dev/ai_personal_wiki/app/core/wiki_fs.py:636-769).
- outline parser и chunking реализованы в [app/core/large_source_ingest.py](/root/dev/ai_personal_wiki/app/core/large_source_ingest.py:1-350).
- typed page updates реализованы в [app/core/safe_page_updates.py](/root/dev/ai_personal_wiki/app/core/safe_page_updates.py:1-220).

## Проверка

Запускались тесты:

```text
pytest tests/test_wiki_fs.py tests/test_large_source_ingest.py tests/test_claims.py tests/test_safe_page_updates.py -q
```

Результат:

```text
128 passed in 1.53s
```

## Вывод

Спецификация реализована существенно, но не полностью. Главные расхождения с документом:

- нет VPS Basic Auth;
- large ingest все еще использует усеченный общий контекст на этапе page generation;
- claims не пишутся как полноценный ingest-артефакт;
- review threshold не принуждает draft/review path.

Если продолжать, я бы в первую очередь закрыл auth и claims persistence, затем пересмотрел бы page generation для large source ingest.
