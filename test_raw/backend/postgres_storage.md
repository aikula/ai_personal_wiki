# PostgreSQL — Primary Storage

Проект backend использует PostgreSQL 15 как основное хранилище данных.

## Конфигурация

```yaml
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: backend_db
      POSTGRES_USER: app
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pg_data:/var/lib/postgresql/data
```

## Схема базы данных

Основные таблицы:

- `users` — профили пользователей, UUID primary key
- `sessions` — активные сессии (sync с Redis cache)
- `audit_log` — иммутабельный лог всех изменений
- `feature_flags` — флаги фич по проектам

## Миграции

Миграции управляются через Alembic. Правило: **никаких down-миграций** — только forward. Все изменения схемы должны быть обратно совместимы (additive only).

```bash
alembic upgrade head
```

## Подключение

Connection pool через SQLAlchemy AsyncEngine:

```python
engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)
```

## Бэкапы

pg_dump запускается ежедневно в 03:00 UTC. Ретенция — 30 дней. Бэкапы хранятся в S3.

## Важное замечание о сессиях

Таблица `sessions` в PostgreSQL является источником правды. Redis-кеш сессий — это только read-cache для ускорения аутентификации. При расхождении побеждает PostgreSQL.

Подробнее о кешировании сессий — в документации по Redis.
