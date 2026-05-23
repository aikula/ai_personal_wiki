# MVP Runbook

Дата: 2026-05-22

Этот документ нужен для первого закрытого тестирования. Его задача - дать
минимальный и достаточный порядок запуска, проверки и обслуживания MVP без
устных пояснений.

## 1. Назначение

Система поддерживает три режима:

- `personal_local` - локальная персональная wiki без аккаунтов;
- `personal_server` - персональная wiki на сервере с опциональной Basic Auth;
- `multi_user` - server mode с аккаунтами, workspace, admin role и лимитами.

Для первого закрытого тестирования используется `multi_user`.

## 2. Минимальные требования

- Python 3.11+, если запуск без Docker;
- Docker и Docker Compose, если запуск в контейнере;
- рабочий OpenAI-compatible LLM endpoint;
- доступ на запись в каталог проекта;
- свободный внешний порт для приложения, по умолчанию `8000`.

## 3. Обязательные переменные окружения

Файл: `.env`

Критично для alpha:

```env
LANGUAGE=ru
APP_PORT=8000

APP_MODE=multi_user
WIKI_DATA_PATH=/wiki-data

CONTROL_DB_URL=sqlite:////wiki-data/control.db
WIKI_WORKSPACES_ROOT=/wiki-data/workspaces
MULTI_USER_ADMIN_EMAILS=admin@example.com
MULTI_USER_ADMIN_EMAIL=admin@example.com
REGISTRATION_ENABLED=true
DEFAULT_DAILY_TOKENS=30000
DEFAULT_WELCOME_TOKENS=200000
DAILY_RESET_TIMEZONE=UTC

LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
LLM_API_KEY=replace-me
```

Опционально:

```env
WIKI_AUTH_ENABLED=false
WIKI_AUTH_USERNAME=
WIKI_AUTH_PASSWORD=
```

Примечания:

- в `multi_user` нет default admin login/password;
- `MULTI_USER_ADMIN_EMAILS` - список admin email через запятую;
- `MULTI_USER_ADMIN_EMAIL` - удобный одиночный alias для одного админа;
- admin role выдается автоматически при регистрации аккаунта на email из
  `MULTI_USER_ADMIN_EMAILS` или `MULTI_USER_ADMIN_EMAIL`;
- `CONTROL_DB_URL` в alpha может оставаться SQLite, но путь должен быть
  абсолютным и указывать на `/wiki-data/...`;
- `WIKI_WORKSPACES_ROOT` должен лежать в persistent storage, а не в
  временном каталоге контейнера;
- `APP_PORT` задает внешний порт `docker compose`;
- `LLM_API_KEY=replace-me` надо заменить на реальный ключ до запуска.

## 4. Запуск без Docker

```bash
pip install -e ".[dev]"
uvicorn app.api.main:app --host 0.0.0.0 --port 8000
```

Проверка:

```bash
curl -sS http://localhost:8000/api/health
```

Ожидаемо:

- HTTP `200`;
- `status=ok`;
- поле `llm` присутствует;
- если LLM не настроен, health может быть `ok`, но с warning.

## 5. Запуск через Docker Compose

```bash
docker compose up --build
```

Compose уже прокидывает:

- `APP_MODE`;
- `APP_PORT`;
- `CONTROL_DB_URL`;
- `WIKI_WORKSPACES_ROOT`;
- `MULTI_USER_ADMIN_EMAILS`;
- quota env;
- `WIKI_AUTH_*`;
- `LLM_*`.

Проверка:

```bash
curl -sS http://localhost:${APP_PORT:-8000}/api/health
```

## 6. Первый запуск alpha

Порядок:

1. Проверить `.env`.
2. Запустить приложение.
3. Открыть `http://localhost:${APP_PORT:-8000}`.
4. Убедиться, что в `multi_user` показывается экран входа/регистрации.
5. Зарегистрировать admin пользователя на email из `MULTI_USER_ADMIN_EMAILS`.
6. Проверить, что в `/api/auth/me` возвращается `is_admin=true`.
7. Зарегистрировать обычного пользователя.
8. Пройти пользовательский сценарий ingest/query.

Важно:

- админ не создается отдельной командой и не имеет default password;
- пароль администратора задается в момент первой регистрации аккаунта на
  разрешенный admin email.

## 7. Где лежат данные

В alpha все данные живут рядом с проектом:

- `./wiki-data/control.db` - SQLite control DB;
- `./wiki-data/workspaces/<user_id>/` - пользовательские workspace;
- `./wiki-data/workspaces/<user_id>/wiki/` - wiki страницы;
- `./wiki-data/workspaces/<user_id>/raw/` - загруженные исходники.

Важно:

- `control.db` и `workspaces/` должны сохраняться между рестартами;
- `.env` не должен попадать в git.

## 8. Базовая проверка после запуска

Нужно проверить:

1. `GET /api/health` отвечает.
2. `GET /api/health` в `multi_user` возвращает `mode=multi_user`.
3. UI в анонимном браузере показывает login/register screen.
4. `POST /api/auth/register` создает admin.
5. `POST /api/auth/login` работает.
6. `GET /api/auth/me` показывает пользователя и credits.
7. `GET /api/admin/settings` доступен админу.
8. `GET /api/admin/settings` недоступен обычному пользователю.
9. `POST /api/ingest` загружает документ.
10. `POST /api/chat` отвечает.
11. `GET /api/usage/me` показывает usage.

## 9. Минимальная процедура smoke test

Admin:

1. Открыть UI в чистом browser context.
2. Убедиться, что виден экран login/register.
3. Зарегистрироваться на admin email.
4. Проверить доступ к `/api/admin/settings`.
5. Проверить, что обычный пользователь туда не проходит.

User:

1. Зарегистрироваться.
2. Загрузить 2-3 документа.
3. Выполнить ingest.
4. Открыть wiki tree.
5. Задать 3 вопроса.
6. Проверить usage.
7. Выйти и убедиться, что token больше не работает.

Isolation:

1. Создать user A.
2. Создать user B.
3. Убедиться, что user B не видит данные user A.

## 10. Что делать при сбое

### Приложение не стартует

Проверить:

- корректность `.env`;
- доступность порта `8000`;
- доступность каталога `wiki-data`;
- корректность `CONTROL_DB_URL`;
- корректность `LLM_*`.

### Health `ok`, но chat/ingest не работают

Чаще всего проблема в LLM:

- неверный `LLM_API_KEY`;
- недоступный `LLM_BASE_URL`;
- неподдерживаемый `LLM_MODEL`.

### Регистрация проходит, но admin role не выдается

Проверить:

- email пользователя точно совпадает с `MULTI_USER_ADMIN_EMAILS`;
- если используется только один email, проверить `MULTI_USER_ADMIN_EMAIL`;
- после изменения `.env` приложение было перезапущено.

### UI открылся без экрана входа

Проверить:

- приложение действительно запущено в `APP_MODE=multi_user`;
- `GET /api/health` возвращает `mode=multi_user`;
- в браузере не используется старый build или закешированная вкладка;
- browser smoke test проходит локально.

### Данные не сохраняются после рестарта Docker

Проверить:

- volume `./wiki-data:/wiki-data` действительно примонтирован;
- `CONTROL_DB_URL` указывает на `/wiki-data/...`, а не на временный путь внутри
  контейнера;
- `WIKI_WORKSPACES_ROOT` также указывает на `/wiki-data/...`.
- контейнер должен иметь права на запись в bind mount; в alpha-режиме
  `wiki-engine` запускается как root, чтобы SQLite и workspace-файлы не
  оказывались в read-only слое.

## 11. Перед выдачей доступа ранним пользователям

Нужно убедиться:

- `ruff` green;
- критичные `pytest` green;
- smoke test пройден;
- admin user создан;
- `.env` заполнен реальными LLM credentials;
- known limitations документированы;
- тестировщикам выдан staging URL и краткий test plan.

## 12. Зафиксированные решения и долг

Краткий список принятых архитектурных решений и сознательно отложенного долга
лежит в [mvp_decisions_and_debt_ru.md](./mvp_decisions_and_debt_ru.md).

Если какой-то пункт из этого документа меняется, нужно синхронно обновить и
runbook, и roadmap, чтобы MVP-контур оставался однозначным.
