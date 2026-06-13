# SmartLight API Reference

REST API для управления платформой SmartLight. Базовый URL: `https://api.smartlight.example.com/v1`

## Аутентификация

API-ключ передаётся в заголовке `Authorization: Bearer <token>`.

## Управление устройствами

### GET /devices

Список всех устройств пользователя.

**Параметры:**
- `type` — фильтр по типу устройства
- `online` — только онлайн/офлайн устройства

**Пример ответа:**
```json
[
  {
    "id": "light_001",
    "name": "Гостиная потолок",
    "type": "light",
    "online": true,
    "brightness": 100,
    "color_temp": 4000
  }
]
```

### PUT /devices/:id/state

Управление состоянием устройства.

**Тело запроса:**
```json
{
  "power": "on",
  "brightness": 75,
  "color_temp": 3500
}
```

## Сценарии

### GET /scenarios

Список пользовательских сценариев автоматизации.

### POST /scenarios

Создать новый сценарий. Правило в формате trigger → condition → action.

## Энергоаналитика

### GET /analytics/energy

Статистика энергопотребления.

**Параметры:**
- `period` — `day` | `week` | `month` | `year`
- `group_by` — `device` | `room` | `all`
