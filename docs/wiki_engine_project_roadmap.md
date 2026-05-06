# Дорожная карта проекта Wiki Engine

## Назначение документа

Этот документ фиксирует целевое направление проекта Wiki Engine, этапы развития, состав функциональности по фазам, критерии готовности, ограничения и принципы принятия решений. Он нужен как рабочий ориентир для разработки, планирования релизов, коммуникации внутри команды и оценки того, что должно попасть в MVP, а что осознанно переносится в следующие этапы.

Проект опирается на паттерн persistent LLM wiki: вместо одноразового RAG-ответа на каждый запрос система постепенно строит и поддерживает постоянную вики-структуру между сырыми источниками и пользовательскими запросами, обновляя сущности, концепты, связи, противоречия и журнал изменений по мере появления новых материалов.[cite:1]

## Видение продукта

Wiki Engine — это локально или серверно разворачиваемая система, которая превращает поток markdown-документов в поддерживаемую LLM-агентом базу знаний. В центре продукта находится не просто поиск по документам, а накопительный knowledge layer: источники остаются неизменяемыми, а их интерпретация, синтез, связи и пользовательские выводы складываются в отдельный слой wiki.[cite:1]

Ценность системы строится на трёх уровнях:

- Raw sources — неизменяемая коллекция исходников, которую агент читает, но не редактирует.[cite:1]
- Wiki — слой markdown-страниц, который агент создаёт и поддерживает: summary pages, entity pages, concept pages, comparisons, derived notes.[cite:1]
- Schema / agent policy — документ с правилами поведения агента, структурой вики, ingest/query/lint workflow и стандартами качества, который превращает LLM из общего чат-бота в дисциплинированного вики-оператора.[cite:1]

## Продуктовая цель

Ключевая цель первой линии развития — дать пользователю систему, которая:

1. Принимает markdown-источники в контролируемый raw-слой.
2. Автоматически строит и обновляет wiki-слой.
3. Позволяет задавать вопросы не по исходным документам напрямую, а по собранной knowledge-структуре.
4. Показывает конфликты, противоречия, устаревшие утверждения и пробелы.
5. Даёт понятный UI для навигации, контроля ingest-процесса и развития базы знаний.

## Принципы продукта

### 1. Persistent knowledge вместо одноразового retrieval

Система должна усиливаться по мере использования: каждый ingest, вопрос, уточнение или разрешённый конфликт делает базу знаний богаче, а не исчезает в истории чата.[cite:1]

### 2. Raw sources неизменяемы

Первичный источник — это ground truth слой. Его можно дополнять, но нельзя переписывать автоматически агентом.[cite:1]

### 3. Wiki — производный, но долговечный артефакт

Wiki-слой допускает автоматические обновления, supersede-цепочки, пересборку и нормализацию, но рассматривается как основной рабочий интерфейс понимания информации.[cite:1]

### 4. Человек остаётся куратором

Пользователь управляет источниками, направляет анализ, разрешает конфликты и определяет полезный workflow, а агент берёт на себя рутинную интеграцию, систематизацию и поддержку согласованности.[cite:1]

### 5. Markdown-first и Git-friendly

Все основные артефакты проекта должны жить в читаемых markdown-файлах, чтобы сохранялись переносимость, версионность, понятность структуры и независимость от проприетарного хранилища.[cite:1]

## Целевые сценарии использования

Проект проектируется не под один узкий кейс, а под несколько классов задач, перечисленных и в исходном паттерне:

- personal knowledge base;
- research workspace;
- reading companion wiki;
- internal team knowledge base;
- due diligence / competitive analysis / course notes / trip planning.[cite:1]

На первом этапе фокус лучше удерживать на knowledge-work сценариях с markdown-источниками: research, due diligence, product documentation, internal notes. Эти сценарии проще стандартизовать и быстрее дают ценность.

## Контуры системы

Ниже зафиксирована целевая архитектурная модель проекта.

| Слой | Назначение | Статус в проекте |
|---|---|---|
| `raw/` | Хранение исходных markdown-файлов и вложений | Базовый обязательный слой |
| `wiki/` | Сгенерированные и поддерживаемые страницы знаний | Основной слой продукта |
| `AGENTS.md` / `CLAUDE.md` | Правила поведения агента и схема wiki | Критически важный слой |
| `index.md` | Каталог страниц wiki для навигации и query-routing | Обязательный слой MVP |
| `log.md` | Хронология ingest/query/lint событий | Обязательный слой MVP |
| `conflicts.md` | Реестр конфликтов и решений | Обязательный слой MVP |
| `skills.md` | Извлечённые проектные правила и heuristics | Обязательный слой MVP+ |

## Этапы проекта

Развитие проекта разбивается на 8 этапов. Каждый этап заканчивается понятным артефактом, критериями готовности и списком того, что сознательно не включается.

---

## Этап 0. Product foundation

### Цель

Согласовать модель продукта, структуру репозитория, формат документов и минимальную агентную спецификацию до начала активной разработки.

### Что должно быть сделано

- Зафиксирован scope MVP.
- Описана структура директорий `raw/`, `wiki/`, `config/`, `app/`, `ui/`.
- Утверждён базовый формат frontmatter для wiki pages.
- Подготовлен черновик `AGENTS.md`/`CLAUDE.md`.
- Описан жизненный цикл ingest → query → lint → conflict resolution.
- Подготовлен этот roadmap как master planning document.

### Функциональность этапа

На этом этапе пользовательской функциональности почти нет. Это инфраструктурно-продуктовая фаза, которая нужна, чтобы не строить ingest, query и UI на неустойчивых правилах.

### Артефакты

- Vision / roadmap document.
- Agent schema draft.
- Wiki page templates.
- Settings template.
- Definition of Done по MVP.

### Критерии завершения

- Команда одинаково понимает, что такое raw/wiki/schema.
- Сформирован единый vocabulary: entity page, concept page, conflict, superseded, crystallization.
- Нет критических неопределённостей по структуре файлов.

### Что не входит

- Реальный ingest код.
- Реальный UI.
- Автоматический аудит.
- Поиск.

---

## Этап 1. Core storage and wiki filesystem

### Цель

Построить стабильный файловый фундамент: операции чтения, записи, поиска, индексации и парсинга wiki-страниц.

### Функциональность

#### 1. Raw storage

- Сохранение входящих markdown-файлов в `raw/<project>/`.
- Поддержка `_general` как кросс-проектного пространства.
- Список raw-файлов через API.

#### 2. Wiki storage

- Запись страниц по slug.
- Чтение страниц с разбором frontmatter.
- Поддержка markdown content + метаданных.
- Вычисление char count, wikilinks, project, title, page type.

#### 3. Special files

- Создание и обновление `index.md`.
- Создание и обновление `log.md`.
- Создание и обновление `conflicts.md`.
- Создание и обновление `skills.md`.

#### 4. Search foundation

- Keyword search по wiki pages.
- Фильтр по project.
- Возврат excerpt и score.

### Пользовательская ценность

Этап сам по себе ещё не создаёт “умную” систему, но формирует надёжную основу, без которой ingest и query будут хаотичными.

### Критерии завершения

- Все wiki-артефакты можно прочитать и записать программно.
- Страницы корректно восстанавливаются из файловой системы.
- Search по slug/title/content работает на приемлемом уровне для малой базы.
- Специальные файлы обновляются детерминированно.

### Риски

- Непродуманный frontmatter сломает следующие этапы.
- Нестабильные slug conventions усложнят конфликты и supersede-цепочки.

### Что не входит

- Синтез через LLM.
- Streaming chat.
- Полноценный semantic audit.

---

## Этап 2. Ingest pipeline MVP

### Цель

Научить систему принимать один markdown-источник и интегрировать его в wiki как набор новых или обновлённых knowledge pages.

### Функциональность

#### 1. Single-source ingest

- Пользователь загружает `.md` файл.
- Файл сохраняется в `raw/`.
- Агент анализирует источник.
- Создаёт summary page.
- Обновляет релевантные entity/concept pages.
- Обновляет `index.md`.
- Добавляет запись в `log.md`.

Это напрямую соответствует базовому паттерну ingest из статьи: новый источник должен не только храниться, но и затрагивать несколько страниц wiki, усиливая уже накопленный слой знаний.[cite:1]

#### 2. Two-step ingest behavior

- Анализ документа и план изменений.
- Применение изменений к wiki.

Это особенно полезно для контроля качества: пользователь или разработчик может видеть, что собирается сделать агент, ещё до записи в wiki.

#### 3. Supersede support

- Если новая страница заменяет старую версию, фиксируется `supersedes`/`superseded_by`.
- Старые страницы не удаляются физически, а помечаются как заменённые.

#### 4. Conflict detection baseline

- При явных противоречиях создаётся запись в `conflicts.md`.
- Conflict не должен останавливать весь ingest по умолчанию.

### Пользовательская ценность

После этого этапа продукт начинает выполнять свою ключевую функцию: он перестаёт быть просто хранилищем файлов и начинает собирать persistent wiki-слой.[cite:1]

### Критерии завершения

- Один файл можно загрузить через API/UI.
- После ingest появляются новые wiki pages.
- `index.md` и `log.md` обновляются автоматически.
- Система способна зафиксировать хотя бы простейший конфликт.

### Что не входит

- Массовый rebuild.
- Сложный semantic merge.
- Batch quality heuristics.
- Human approval workflow на каждый шаг.

---

## Этап 3. Query engine MVP

### Цель

Сделать wiki usable для повседневных вопросов: пользователь задаёт вопрос, система ищет страницы, читает релевантные фрагменты и отвечает с цитированием wiki-slugs.

### Функциональность

#### 1. Question classification

Поддержка базовых типов вопросов:

- factual;
- comparison;
- exploratory;
- meta.

Это следует логике статьи: разные типы вопросов могут порождать разные формы ответа, а хорошие ответы потенциально должны становиться новыми wiki-артефактами.[cite:1]

#### 2. Retrieval over wiki

- Поиск по `index.md` и страницам.
- Выбор top-k страниц.
- Ограничение по project при необходимости.

#### 3. Answer generation

- Ответ только на основе wiki context.
- Inline citations как `[[slug]]`.
- Раздельная обработка factual/comparison/exploratory запросов.

#### 4. Session memory

- Chat history хранится по session ID.
- Можно возвращаться к прошлым сессиям.

#### 5. Crystallization

- Полезный ответ или серия ответов может быть превращена в отдельную wiki page.

Это одна из центральных идей паттерна: результаты вопросов не должны теряться в чате, а должны при необходимости сохраняться обратно в knowledge base.[cite:1]

### Пользовательская ценность

На этом этапе система становится не только knowledge compiler, но и usable assistant поверх накопленной wiki.

### Критерии завершения

- Вопросы отрабатывают через API и UI.
- Есть потоковый ответ или хотя бы быстрый non-stream режим.
- Цитирование по wiki-slugs стабильно.
- Можно открыть cited page в интерфейсе.

### Что не входит

- Web search outside wiki.
- Глубокий multi-agent research.
- Auto-filing каждого ответа без участия пользователя.

---

## Этап 4. Conflict management and human-in-the-loop

### Цель

Сделать противоречия и неопределённости управляемыми, а не скрытыми.

### Почему это важно

Статья отдельно подчёркивает, что wiki должна отмечать, где новые данные противоречат старым, а также периодически проверяться на contradictions и stale claims.[cite:1] Без этого wiki быстро деградирует в “псевдо-истину”, где противоречия скрыты в тексте и неуправляемы.

### Функциональность

#### 1. Conflict registry

- `conflicts.md` как единый реестр открытых и решённых конфликтов.
- Structured fields: id, date, source file, conflict type, page A/B, context, options, comment, resolution.

#### 2. Resolution flow

- Пользователь выбирает вариант решения.
- Может добавить комментарий.
- Конфликт переводится в `RESOLVED`.

#### 3. Skill extraction

- Из решения можно извлечь правило и добавить его в `skills.md`.
- Это снижает вероятность повторения той же ошибки агентом.

#### 4. UI for conflict review

- Список open/resolved conflicts.
- Карточка конфликта.
- Кнопки resolve/comment.

### Пользовательская ценность

Появляется прозрачность: пользователь видит, где wiki уверена, а где нет; где знания устойчивы, а где требуется управленческое решение.

### Критерии завершения

- Конфликт можно открыть и разрешить через UI.
- После разрешения обновляется `conflicts.md`.
- Из решения может быть сформирован новый skill.

### Что не входит

- Полностью автоматическое разрешение сложных конфликтов.
- Формальная логическая дедупликация фактов между всеми страницами.

---

## Этап 5. Lint and audit system

### Цель

Добавить периодический health-check wiki и превратить базу знаний в поддерживаемую систему, а не просто набор страниц.

### Основание

В исходном паттерне lint — это отдельная операция: система должна искать contradictions, stale claims, orphan pages, missing cross-references и data gaps, а также подсказывать, какие вопросы и источники стоит изучить дальше.[cite:1]

### Функциональность

#### 1. Structural audit

- broken wikilinks;
- broken path links;
- missing anchors;
- orphan pages;
- missing frontmatter;
- char limit violations;
- duplicate titles;
- stale pages;
- superseded active pages.

#### 2. Optional semantic audit

- factual contradictions across pages;
- duplicate content;
- missing backlinks;
- stale facts;
- suspicious version/date mismatches.

#### 3. Audit report

- Сводка по ошибкам и предупреждениям.
- Разделение structural vs semantic.
- JSON и UI-friendly выдача.

#### 4. Conflict auto-generation

- Для части semantic issues система может автоматически создавать conflicts.

### Пользовательская ценность

Этап делает wiki self-maintained в духе статьи: поддержание связности, актуальности и полноты перестаёт быть ручной задачей.[cite:1]

### Критерии завершения

- Structural audit можно запустить по API.
- Semantic audit можно включать опционально.
- Результаты читаемы человеком и пригодны для action list.

### Что не входит

- Полностью автономное самовосстановление wiki без контроля.
- Fact-check against web by default.

---

## Этап 6. Usable product UI

### Цель

Собрать рабочий интерфейс, с которым можно реально жить: загружать документы, задавать вопросы, просматривать wiki, разбирать конфликты и запускать rebuild.

### Функциональность

#### 1. Three-panel workspace

- Левая панель — history / sessions.
- Центр — chat.
- Правая панель — wiki tree + page viewer + conflicts.

#### 2. Upload workflow

- Upload modal.
- Drag & drop markdown files.
- Project selection.
- Progress feedback.

#### 3. Chat workflow

- Потоковые ответы.
- Отображение cited pages.
- Session switching.
- New session / delete session.

#### 4. Wiki navigation

- Tree by projects.
- Search by keyword.
- Page open in right panel.
- Superseded warning.

#### 5. Conflict operations

- Просмотр открытых конфликтов.
- Выбор suggested option.
- Комментарий и resolve.

#### 6. Rebuild workflow

- Полный rebuild из raw.
- SSE progress stream.
- Журнал обработки.

### Пользовательская ценность

После этого этапа продукт уже можно использовать как реальный internal tool, а не только как backend-концепт.

### Критерии завершения

- Базовый workflow выполняется без консоли.
- Новому пользователю можно показать end-to-end demo.
- UI достаточно стабилен для ежедневного использования на малой команде.

### Что не входит

- Полноценный design system enterprise-уровня.
- Fine-grained RBAC.
- Multi-user collaboration.

---

## Этап 7. MVP release

### Цель

Собрать все базовые компоненты в первый релиз, который можно положить в продакшн-песочницу или дать пилотным пользователям.

### Состав MVP

| Область | Входит в MVP |
|---|---|
| Raw ingest | Да |
| Wiki page generation | Да |
| Index/log/conflicts/skills | Да |
| Search по wiki | Да |
| Streaming chat | Да |
| Crystallization | Да |
| Structural lint | Да |
| Semantic audit (optional) | Да, базово |
| Rebuild | Да |
| Single-user UI | Да |
| Docker deployment | Да |
| Multi-user auth | Нет |
| Fine-grained approval pipelines | Нет |
| External web connectors | Нет |
| Non-markdown document parsers as first-class | Нет |

### MVP-позиционирование

MVP — это personal/team knowledge work tool для curated markdown sources. Не universal enterprise KM suite.

### Критерии готовности MVP

- Система поднимается через Docker.
- Пользователь может загрузить набор markdown-файлов.
- Wiki строится и пополняется.
- Можно задавать вопросы и получать ответы с wiki-citations.
- Есть обзор конфликтов и возможность их разрешения.
- Есть health-check wiki.
- Есть rebuild из raw.
- Основной workflow документирован.

### Основные ограничения MVP

- Single-user or trusted small-team mode.
- In-memory chat sessions.
- Limited scaling on very large wiki.
- Качество ответов зависит от качества schema и page templates.
- Не все конфликты будут детектироваться автоматически.

---

## Этап 8. Post-MVP / Phase 2

### Цель

Усилить продукт до уровня надёжного командного knowledge platform, сохранив markdown-first архитектуру.

### Направления развития

#### 1. Search and retrieval upgrade

В статье предлагается, что на ранних масштабах можно обходиться `index.md`, а при росте базы имеет смысл добавить локальный search engine, например qmd с hybrid BM25/vector search и LLM reranking.[cite:1]

Практически это означает:

- qmd или собственный search service;
- hybrid retrieval;
- semantic reranking;
- better recall на сотнях и тысячах страниц.

#### 2. Better source ingestion

- PDF/HTML/email/Slack transcript ingestion.
- Автоконвертация в markdown.
- Работа с изображениями и вложениями.

Это также соответствует статье, где отдельно упоминаются картинки, локальные attachments и практика, когда LLM читает текст, а затем при необходимости отдельно просматривает изображения.[cite:1]

#### 3. Human review modes

- Draft mode before apply.
- Approve/reject page diffs.
- Conflict escalation policies.
- Review queues by project.

#### 4. Team features

- Multi-user sessions.
- Authentication / authorization.
- Shared workspaces.
- Audit trail per user.
- Notifications on conflicts and stale pages.

#### 5. Automated maintenance

- Scheduled lint.
- Suggested follow-up questions.
- Suggested missing pages.
- Suggested new source collection priorities.

Это непосредственно продолжает идею статьи, где lint должен не только искать поломки, но и подсказывать, какие вопросы и источники стоит исследовать дальше.[cite:1]

#### 6. Better outputs

Статья прямо допускает, что ответы могут принимать форму markdown page, comparison table, slide deck, chart или canvas.[cite:1] Поэтому логичное развитие:

- export в отчёты;
- slide deck generation;
- chart generation;
- decision memo generation;
- briefing packs.

#### 7. Git-native workflows

Поскольку wiki по своей природе — это git repo markdown-файлов, полезно развивать:[cite:1]

- branch-based reviews;
- diff visualization;
- commit messages from ingest;
- rollback / compare revisions;
- PR-like review for major rewrites.

---

## Функциональность по релизам

### Release 0.1 — Internal prototype

- Файловая структура.
- API для upload и wiki page operations.
- Примитивный ingest.
- Примитивный query.
- Локальный запуск.

### Release 0.2 — MVP-alpha

- Полный ingest flow.
- `index.md`, `log.md`, `conflicts.md`.
- Базовый UI.
- Session history.
- Wiki tree.
- Resolve conflict flow.

### Release 0.3 — MVP-beta

- Streaming chat.
- Crystallization.
- Structural lint.
- Rebuild flow.
- Улучшенная стабильность page templates.

### Release 1.0 — MVP production pilot

- Docker deployment.
- Semantic audit (optional).
- Улучшенная обработка ошибок.
- Документация для эксплуатации.
- Onboarding сценарий.

### Release 1.1+

- Search engine upgrade.
- Team features.
- Review workflows.
- Connectors and richer source formats.

## Приоритеты по реализации

### P0 — Без этого продукт не существует

- WikiFS.
- Page templates.
- Ingest agent.
- Query agent.
- `index.md`, `log.md`, `conflicts.md`.
- Basic UI.

### P1 — Делает продукт управляемым

- Conflict resolution UI.
- Skills extraction.
- Structural lint.
- Rebuild.
- Session history.

### P2 — Делает продукт зрелым

- Semantic audit.
- Crystallization.
- Better retrieval.
- Better search.
- Better observability.

### P3 — Делает продукт платформой

- Team workflows.
- Auth.
- Connectors.
- Scheduling.
- Git-integrated review.

## Ключевые метрики успеха

### Продуктовые

- Время от загрузки документа до появления wiki updates.
- Доля ingest без ручного исправления.
- Среднее число обновлённых wiki pages на один ingest.
- Доля вопросов, на которые система отвечает с достаточными citations.
- Доля полезных answer artifacts, ушедших в crystallization.

### Качество базы знаний

- Количество open conflicts.
- Количество orphan pages.
- Количество stale pages.
- Доля broken wikilinks.
- Доля страниц с низкой confidence.

### UX / adoption

- Частота повторного использования пользователем.
- Средняя длина активной сессии.
- Количество upload → query → resolve циклов в неделю.
- Количество ручных возвратов к raw sources вместо wiki.

## Основные риски проекта

### 1. Слабая schema / AGENTS.md

Исходный паттерн прямо указывает, что schema — ключевой файл всей системы.[cite:1] Если правила агента размыты, wiki получится непоследовательной независимо от качества модели.

### 2. Плохие page templates

Если entity и concept pages не имеют чёткой структуры, данные начнут расползаться по wiki в непредсказуемом формате.

### 3. Низкое качество conflict detection

Если противоречия остаются неявными, доверие к wiki быстро падает.

### 4. Слабый retrieval на росте базы

На малом масштабе `index.md` может быть достаточным, но при росте базы без улучшения search/retrieval качество query начнёт проседать.[cite:1]

### 5. Переусложнение до MVP

Есть риск слишком рано уйти в multi-user, connectors, rich formats и approval pipelines. Это может затормозить выпуск реально полезного ядра.

## Рекомендуемая последовательность реализации

1. Этап 0 — зафиксировать schema и templates.
2. Этап 1 — закончить файловый слой.
3. Этап 2 — собрать single-file ingest.
4. Этап 3 — включить query + citations.
5. Этап 4 — сделать конфликты управляемыми.
6. Этап 5 — добавить lint/audit.
7. Этап 6 — довести UI до daily-usable состояния.
8. Этап 7 — выпустить MVP.
9. Этап 8 — расширять retrieval, team workflows и automation.

## Решение о границах MVP

Для текущего проекта разумно считать MVP завершённым, когда система уже делает главное: превращает curated markdown-источники в persistent wiki, поддерживает обновление знаний при ingest, позволяет спрашивать по этому слою, показывает конфликты и даёт оператору понятный контроль над жизненным циклом базы знаний.[cite:1]

Именно это соответствует духу исходной идеи LLM Wiki: не просто retrieval-интерфейс поверх документов, а постоянно поддерживаемый knowledge artifact, который становится ценнее с каждым новым документом и каждым новым вопросом.[cite:1]
