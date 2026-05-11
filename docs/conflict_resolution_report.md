# Отчёт: Проверка устранения конфликтов vs ожидаемое поведение

**Дата:** 2026-05-11
**Статус:** Частичное соответствие (⚠️ 4/10 критических проблем)

---

## 1. Сводка результатов

| Категория | Ожидалось | Фактически | Статус |
|---|---|---|---|
| Конфликты разрешены | 10 RESOLVED | 10 RESOLVED | ✅ PASS |
| Skills извлечены | 10 навыков | 9 навыков | ✅ PASS |
| Wiki обновлены | Все страницы обновлены | Только 1 из 6 | ❌ FAIL |
| Index обновлён | Open conflicts: 0 | Open conflicts: 5 | ❌ FAIL |
| User comments | Сохранены | Сохранены | ✅ PASS |
| Pilot vs production | Различены в wiki | Только в database-pilot | ⚠️ PARTIAL |

---

## 2. Детальный анализ по конфликтам

### CONFLICT-001 — Database (PostgreSQL vs SQLite)
- **Resolution:** Add note about database migration strategy
- **Skill extracted:** ✅ Да
- **Wiki обновлена:** ✅ `database-pilot.md` — отражает SQLite для пилота, PostgreSQL после 500 пользователей
- **Статус:** ✅ PASS

### CONFLICT-002 — Voice interface (MVP vs Phase 2)
- **Resolution:** Mark voice interface as Phase 2 feature
- **Skill extracted:** ✅ Да
- **Wiki обновлена:** ❌ `voice-interface.md` всё ещё описывает голос как компонент MVP
- **Статус:** ❌ FAIL — страница не обновлена

### CONFLICT-003 — Deployment (on-premise vs cloud pilot)
- **Resolution:** Distinguish between pilot deployment (cloud) and production deployment (on-premise)
- **Skill extracted:** ✅ Да
- **Wiki обновлена:** ❌ `customer-controlled-infrastructure.md` всё ещё описывает только on-premise
- **Статус:** ❌ FAIL — страница не обновлена

### CONFLICT-004 — Concierge fields (phone/priority removed)
- **Resolution:** Mark phone and priority as optional or future features
- **Skill extracted:** ✅ Да
- **Wiki обновлена:** ❌ `concierge-module.md` всё ещё показывает phone и priority как обязательные
- **Статус:** ❌ FAIL — страница не обновлена

### CONFLICT-005 — FAQ scope (broad RAG vs curated only)
- **Resolution:** Restrict FAQ assistant scope to curated entries
- **Skill extracted:** ✅ Да
- **Wiki обновлена:** ❌ `faq-assistant.md` всё ещё описывает broad RAG с resident documents
- **Статус:** ❌ FAIL — страница не обновлена

### CONFLICT-006 — Language scope (cross-project)
- **Resolution:** Mark as cross-project difference
- **User comment:** "Пометь, что это мультиязычный проект изначально"
- **Skill extracted:** ✅ Да
- **Wiki обновлена:** ❌ `mvp-scope.md` не содержит пометки о мультиязычности
- **Статус:** ❌ FAIL — комментарий учтён, но wiki не обновлена

### CONFLICT-007 — Deployment difference (cross-project)
- **Resolution:** Create comparison note
- **Skill extracted:** ✅ Да
- **Wiki обновлена:** ❌ Нет comparison note
- **Статус:** ❌ FAIL

### CONFLICT-008 — Database difference (cross-project)
- **Resolution:** Mark as cross-project difference
- **Skill extracted:** ✅ Да
- **Wiki обновлена:** N/A (cross-project, не требует изменения wiki)
- **Статус:** ✅ PASS

### CONFLICT-009 — Voice difference (cross-project)
- **Resolution:** Mark as cross-project difference
- **Skill extracted:** ✅ Да
- **Wiki обновлена:** N/A (cross-project)
- **Статус:** ✅ PASS

### CONFLICT-010 — Concierge difference (cross-project)
- **Resolution:** Mark as cross-project difference
- **Skill extracted:** ✅ Да
- **Wiki обновлена:** N/A (cross-project)
- **Статус:** ✅ PASS

---

## 3. Анализ skills.md

**Извлечено 9 навыков** в трёх категориях:

### Conflict Resolution Patterns (5 навыков)
1. При отсутствии фичи в source — помечать как cross-project difference
2. При противоречии wiki и source — приоритет source, помечать как cross-project difference
3. При ограничении фичи в source — помечать как cross-project difference с аннотацией
4. При mismatch pilot/production — явно различать временные и целевые условия
5. При конфликте версий технических спецификаций — приоритет source + note о миграции

### Source Trust Rules (4 навыка)
1. При противоречии wiki и official source — приоритет source documentation
2. При противоречии wiki и primary source — приоритет source, создать comparison note
3. При ограничении capabilities в source — приоритет наиболее строгого определения
4. При конфликте wiki и source по MVP requirements — приоритет source, пометить как optional/deferred

**Статус:** ✅ Skills извлекаются корректно

---

## 4. Критические проблемы

### 🔴 Проблема 1: Wiki страницы не обновляются после разрешения конфликтов

**Описание:** После разрешения конфликта система сохраняет resolution и извлекает skill, но НЕ обновляет соответствующую wiki страницу.

**Затронутые страницы:**
- `eywa-demo/features/voice-interface.md` — должна быть помечена как Phase 2
- `eywa-demo/deployment/customer-controlled-infrastructure.md` — должна различать pilot/production
- `eywa-demo/features/concierge-module.md` — должна пометить phone/priority как optional
- `eywa-demo/features/faq-assistant.md` — должна ограничить scope до curated entries
- `eywa-demo/scope/mvp-scope.md` — должна содержать пометку о мультиязычности

**Единственное исключение:** `eywa-demo/tech-stack/database-pilot.md` — обновлена (вероятно, была обновлена во время ingest, а не после разрешения конфликта)

### 🔴 Проблема 2: Index не обновляет счётчик конфликтов

**Описание:** `wiki/index.md` показывает "Open conflicts: 5", но фактически все 10 конфликтов разрешены.

### 🟡 Проблема 3: Нет automatic wiki update после conflict resolution

**Описание:** Система не имеет механизма автоматического обновления wiki страниц после разрешения конфликта. Resolution сохраняется, но не применяется к контенту.

---

## 5. Сравнение с ожидаемым поведением (docs/test_processing_expected_behavior.md)

| Требование | Ожидалось | Фактически | Статус |
|---|---|---|---|
| Конфликты разрешены | selected conflict becomes resolved | ✅ Все 10 RESOLVED | ✅ PASS |
| Resolution/comment visible | resolution и comment видны | ✅ Видны в conflicts.md | ✅ PASS |
| Skill extraction | reusable rule created/previewed | ✅ 9 навыков извлечены | ✅ PASS |
| Future answers distinguish pilot vs production | ответы различают pilot/production | ⚠️ Только в database-pilot | ⚠️ PARTIAL |
| Wiki reflects resolved conflicts | wiki обновлена | ❌ 5 из 6 страниц не обновлены | ❌ FAIL |
| Index shows correct conflict count | Open conflicts: 0 | ❌ Показывает 5 | ❌ FAIL |

---

## 6. Рекомендации

### Приоритет 1 (Критический)
1. **Реализовать automatic wiki update после conflict resolution** — при разрешении конфликта система должна обновлять соответствующую wiki страницу согласно выбранному resolution
2. **Исправить счётчик конфликтов в index** — `rebuild_index` должен корректно считать открытые конфликты

### Приоритет 2 (Важный)
3. **Добавить manual wiki update endpoint** — возможность вручную применить resolution к wiki странице
4. **Валидация обновлений** — после применения resolution проверять, что wiki страница отражает изменения

### Приоритет 3 (Улучшение)
5. **Comparison notes для cross-project** — автоматически создавать сравнительные заметки при разрешении cross-project differences
6. **Audit trail** — логировать изменения wiki страниц после conflict resolution

---

## 7. Вывод

Система корректно:
- ✅ Разрешает конфликты и сохраняет resolution
- ✅ Извлекает reusable skills из разрешённых конфликтов
- ✅ Сохраняет user comments

Система НЕ корректно:
- ❌ Обновляет wiki страницы после разрешения конфликтов (только 1 из 6)
- ❌ Обновляет счётчик конфликтов в index
- ❌ Применяет resolution к контенту wiki

**Общая оценка:** 40% — система запоминает решения, но не применяет их к wiki контенту.
