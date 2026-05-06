# Redis в проекте Frontend

Проект frontend также использует Redis, но версии **6.2** (не 7.2 как в backend).

## Зачем frontend нужен Redis

Frontend-сервис (Next.js SSR) использует Redis для:

1. **Server-side session store** — сессии пользователей на уровне SSR
2. **Full-page cache** — кеширование HTML-страниц для анонимных пользователей, TTL 5 минут
3. **A/B testing assignments** — распределение пользователей по группам, persistence на 30 дней

## Конфигурация

```yaml
services:
  redis-frontend:
    image: redis:6.2-alpine
    ports:
      - "6380:6379"  # другой внешний порт чтобы не конфликтовать с backend
    command: redis-server --maxmemory 256mb --maxmemory-policy volatile-lru
```

Обрати внимание: `volatile-lru` вместо `allkeys-lru` — вытесняются только ключи с TTL.

## Ключевое отличие от backend Redis

- Backend Redis: порт 6379, maxmemory 512MB, allkeys-lru, Redis 7.2
- Frontend Redis: порт 6380, maxmemory 256MB, volatile-lru, Redis 6.2

Это два отдельных экземпляра Redis с разными задачами. Не путать.

## Планы по обновлению

В следующем спринте планируется обновление до Redis 7.2 для унификации с backend. До этого момента оба инстанса сосуществуют с разными версиями.

## Мониторинг

Мониторинг пока не настроен — это технический долг. Планируется добавить redis-exporter аналогично backend.
