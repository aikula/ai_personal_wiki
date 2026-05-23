# MVP Release Plan

Дата: 2026-05-22

Этот документ фиксирует конечную точку MVP, план движения к ней и правила
остановки. Цель документа - не описать все возможное развитие Wiki Engine, а
зафиксировать, когда текущую версию можно считать готовой к закрытому
тестированию и первой демонстрации.

## 1. Критическая позиция

Проект уже вышел за рамки исходного "персонального wiki-движка". Сейчас реальная
цель шире:

- один кодовый контур для локальной персональной wiki;
- один кодовый контур для персональной wiki на VPS;
- один кодовый контур для server multi-user версии с аккаунтами, изоляцией
  workspace и лимитами токенов.

Главный риск на этом этапе - не отсутствие новых функций, а размывание границ.
Если продолжать добавлять возможности без release boundary, MVP станет
незавершаемым. Поэтому MVP должен быть определен как "достаточно надежная версия
для закрытого тестирования", а не как полноценная SaaS-платформа.

## 2. Конечная точка MVP

MVP считается готовым к закрытому тестированию и представлению, когда одновременно
выполнены условия:

- `personal_local` стабильно работает без аккаунтов и без SQLite control DB.
- `personal_server` стабильно работает с опциональной Basic Auth защитой.
- `multi_user` стабильно работает с аккаунтами, личными workspace, admin role,
  token usage accounting и лимитами.
- Пользователь может пройти основной сценарий без ручного вмешательства в БД:
  регистрация, вход, загрузка документов, ingest, просмотр wiki, вопрос агенту,
  просмотр usage.
- Админ может управлять server-level настройками через admin-only API.
- Обычный пользователь не может читать или менять чужие данные и server-level
  настройки.
- Есть минимальная русская эксплуатационная документация и тест-план.
- Нет известных critical/high security findings по tenant isolation.
- Критичные automated tests проходят локально.

Это конечная точка для MVP. Все, что не требуется для этих условий, переносится
в post-MVP.

## 3. Зачем движемся именно туда

Ценность MVP:

- доказать, что markdown-first wiki может пополняться из документов и отвечать
  через агента;
- показать один и тот же продукт в локальном и серверном режимах;
- дать внешним пользователям безопасно тестировать систему в своих workspace;
- собрать обратную связь по качеству ingest/query до вложений в billing,
  сложную RBAC-модель, очереди, Postgres и масштабирование.

Ключевое инженерное решение:

- wiki-контент остается в файловой структуре workspace;
- SQLite используется только как control plane для server multi-user режима;
- агенты и WikiFS не форкаются на personal/multi-user варианты;
- tenant boundary задается на уровне FastAPI dependencies и workspace root.

## 4. Что входит в MVP

Обязательный scope:

- режимы `personal_local`, `personal_server`, `multi_user`;
- auth: register, login, logout, me;
- роль `admin` и admin-only server-level API под `/api/admin/*`;
- отдельный workspace на пользователя;
- загрузка поддерживаемых документов;
- ingest в wiki;
- wiki tree, page view, search;
- chat по своей wiki;
- usage tracking;
- daily и welcome token limits;
- lazy daily reset;
- conflict list и базовое разрешение конфликтов;
- draft/review flow в пределах собственного workspace;
- rebuild/clear в пределах собственного workspace;
- базовый UI для пользовательского сценария;
- тесты на критичные сценарии.

## 5. Что не входит в MVP

Это не блокирует готовность MVP:

- billing и платежи;
- роли сложнее `user/admin`;
- админ-панель управления всеми пользователями;
- ручная выдача лимитов из UI;
- shared workspace между пользователями;
- организация/команды;
- Postgres;
- фоновые очереди и distributed workers;
- S3/object storage;
- production observability stack;
- email verification и password reset;
- OAuth;
- полноценный audit trail действий пользователей;
- Git workflow для review;
- enterprise RBAC;
- high-load режим;
- SLA/backup/restore как продуктовая функция.

Если одна из этих тем всплывает до MVP, решение по умолчанию: перенести в
post-MVP, если она не закрывает critical/high security или launch blocker.

## 6. Этапы до MVP

### Этап 1. Security and Tenant Boundary Freeze

Цель: закрыть риски доступа пользователя к чужим данным.

Задачи:

- зафиксировать список tenant-level endpoints;
- зафиксировать список admin-level endpoints;
- проверить, что все `/api/admin/*` требуют admin;
- проверить, что все tenant-level endpoints используют текущий workspace;
- проверить, что chat sessions scoped по пользователю;
- проверить, что global settings недоступны обычным пользователям;
- добавить тесты на cross-user access для wiki, chat sessions, usage и settings.

Acceptance:

- non-admin получает `403` на `/api/admin/*`;
- unauthenticated multi-user request получает `401` на tenant data endpoints;
- user A не может получить session/wiki/usage user B через API;
- server-level settings не раскрывают глобальные пути и LLM config обычному
  пользователю.

Exit rule:

- нет известных critical/high tenant isolation findings.

### Этап 2. Quota and LLM Accounting Freeze

Цель: убедиться, что лимиты реально защищают LLM usage.

Задачи:

- проверить, что chat использует `MeteredLLMClient`;
- проверить, что ingest использует `MeteredLLMClient`;
- проверить, что failed LLM call не расходует quota;
- проверить, что streaming error не проглатывается;
- проверить, что daily и welcome buckets списываются в правильном порядке;
- проверить concurrency behavior SQLite на коротких транзакциях.

Acceptance:

- quota exceeded блокирует LLM call до provider request;
- successful call пишет `usage_events`;
- failed call возвращает reserve;
- usage отображается через `/api/usage/me`;
- тесты не вызывают реальный LLM provider.

Exit rule:

- нет известного способа обойти лимит через chat/ingest API.

### Этап 3. User Flow Stabilization

Цель: основной сценарий должен проходиться без знания внутренностей системы.

Задачи:

- проверить register/login/logout в UI;
- проверить загрузку документов в multi-user;
- проверить ingest и отображение результата;
- проверить wiki tree/page/search;
- проверить chat и citations;
- проверить usage indicator;
- проверить ошибки auth/quota/user-friendly messages;
- скрыть или заблокировать admin controls для обычного пользователя.

Acceptance:

- новый пользователь может сам пройти путь от регистрации до первого ответа;
- админ может открыть admin settings;
- обычный пользователь не видит или не может выполнить admin действия;
- UI не требует ручной правки env/DB после запуска.

Exit rule:

- demo-сценарий воспроизводится за 5-10 минут.

### Этап 4. Operational Documentation

Цель: проект можно дать тестировщику или показать без устных инструкций.

Документы:

- `docs/mvp_runbook_ru.md`;
- `docs/mvp_test_plan_ru.md`;
- `docs/mvp_known_limits_ru.md`.

Минимальное содержание runbook:

- режимы запуска;
- обязательные env vars;
- настройка admin email;
- запуск локально;
- запуск на VPS;
- health check;
- где лежат workspace и control DB;
- что делать при ошибке LLM provider;
- что нельзя коммитить.

Минимальное содержание test plan:

- personal local smoke;
- personal server smoke;
- multi-user user smoke;
- multi-user admin smoke;
- cross-user negative checks;
- quota checks;
- ingest/query checks;
- rollback/cleanup для тестового окружения.

Exit rule:

- внешний тестировщик может запустить и проверить MVP по документации.

### Этап 5. Release Candidate Pass

Цель: зафиксировать версию, которую можно показывать.

Задачи:

- полный запуск критичных tests;
- ruff;
- ручной smoke test по test plan;
- финальный review security boundary;
- финальный review docs;
- release notes;
- commit/tag или явно названный commit hash для демонстрации.

Acceptance:

- automated checks green;
- smoke test green;
- нет незакрытых P0/P1 багов;
- known limitations перечислены в документации;
- scope freeze принят.

Exit rule:

- версия получает статус `MVP Ready for Closed Testing`.

## 7. Endpoint Classification

Tenant-level endpoints:

- `/api/auth/*`;
- `/api/usage/me`;
- `/api/wiki/*`;
- `/api/chat/*`;
- `/api/ingest`;
- `/api/ingest/batch`;
- `/api/ingest/raw`;
- `/api/ingest/drafts/*`;
- `/api/ingest/rebuild`;
- `/api/ingest/clear`;
- `/api/conflicts/*`;
- `/api/audit/*`, если audit работает только в пределах текущего workspace.

Admin-level endpoints:

- `/api/admin/settings/*`;
- будущие server-level user management endpoints;
- будущие global quota management endpoints;
- будущие system diagnostics, которые раскрывают server paths, provider config,
  global DB state или чужие workspace.

Правило:

- если endpoint действует только на текущий workspace, он tenant-level;
- если endpoint меняет или раскрывает состояние всего процесса/сервера, он
  admin-level;
- если endpoint может повлиять на LLM/provider behavior всех пользователей, он
  admin-level;
- если есть сомнение, endpoint временно считается admin-level до явного решения.

## 8. MVP Readiness Checklist

Security:

- [ ] `multi_user` без token получает `401` на tenant data endpoints.
- [ ] non-admin получает `403` на `/api/admin/*`.
- [ ] user A не читает wiki user B.
- [ ] user A не читает chat sessions user B.
- [ ] user A не читает usage user B.
- [ ] global settings недоступны non-admin.
- [ ] Basic Auth не используется как user system в `multi_user`.

Quota:

- [ ] chat списывает токены.
- [ ] ingest списывает токены.
- [ ] failed LLM call не списывает токены.
- [ ] quota exceeded не вызывает provider.
- [ ] daily bucket расходуется перед welcome bucket.
- [ ] lazy daily reset работает.

Product flow:

- [ ] register работает.
- [ ] login работает.
- [ ] logout инвалидирует token.
- [ ] upload работает.
- [ ] ingest работает.
- [ ] wiki tree/page/search работают.
- [ ] chat отвечает по своей wiki.
- [ ] usage виден пользователю.
- [ ] admin settings доступны админу.
- [ ] UI не показывает обычному пользователю server-level controls.

Ops:

- [ ] `.env.example` или runbook содержит нужные env vars.
- [ ] Docker/local запуск описан.
- [ ] VPS запуск описан.
- [ ] smoke test описан.
- [ ] known limitations описаны.

Quality:

- [ ] ruff green.
- [ ] critical pytest suite green.
- [ ] ручной smoke green.
- [ ] нет известных P0/P1.

## 9. P0/P1/P2 Bug Policy

P0 - блокирует MVP:

- утечка чужих данных;
- обход quota;
- невозможность пройти register/login/upload/ingest/chat;
- падение приложения при обычном сценарии;
- невозможность запустить по документации.

P1 - должен быть закрыт до демонстрации, если влияет на доверие:

- непонятные auth/quota ошибки;
- неработающий admin settings flow;
- нестабильный ingest на небольших документах;
- некорректное отображение usage;
- тесты красные в критичном контуре.

P2 - можно перенести после MVP:

- UI polish;
- расширенные отчеты;
- удобная админка;
- дополнительные форматы документов;
- оптимизация скорости;
- расширенная аналитика.

## 10. Stop Rules

Работу над MVP нельзя расширять без явного решения, если новая задача:

- не закрывает P0/P1;
- не входит в mandatory scope;
- не нужна для demo-сценария;
- требует новой инфраструктуры;
- требует новой роли, кроме `user/admin`;
- требует изменения базовой архитектуры хранения.

Если задача полезна, но не проходит эти правила, она переносится в post-MVP
backlog.

## 11. Решение о готовности

Формулировка для фиксации:

```text
MVP Ready for Closed Testing:
- mandatory scope implemented;
- P0/P1 closed;
- critical tests green;
- manual smoke green;
- runbook/test plan/known limits available;
- version identified by commit hash or tag.
```

После этой точки проект не считается "законченным продуктом". Он считается
достаточно стабильным и понятным, чтобы показать его тестировщикам, собрать
обратную связь и принимать post-MVP решения на основе реального использования.

## 12. Decisions and Debt Log

Принятые архитектурные решения и сознательно отложенный технический долг
фиксируются в отдельном документе:

- [mvp_decisions_and_debt_ru.md](./mvp_decisions_and_debt_ru.md)

Этот документ - не backlog и не change log. Его задача - зафиксировать, какие
компромиссы уже приняты для alpha, чтобы не пересматривать их случайно при
следующих итерациях.
