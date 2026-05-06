# Code Review — Wiki Engine Stage 1

**Дата:** 2026-05-06  
**Ревьюер:** Perplexity AI  
**Ветка:** `main`  
**Коммит:** `69dc71d`  
**Покрытые файлы:**
- `app/config.py`
- `app/core/wiki_fs.py`
- `app/core/linter.py`
- `app/core/interpreter.py`
- `app/core/llm_client.py`
- `app/core/token_budget.py`
- `app/core/utils.py`
- `app/agents/ingest_agent.py`
- `app/agents/query_agent.py` *(обзорно)*
- `app/agents/audit_agent.py` *(обзорно)*
- `app/ui/index.html`

---

## Общая оценка

**Архитектура: отлично.** Контракты между слоями чёткие, `wiki_fs.py` как единственный writer, two-step ingest, typed dataclass pipeline — всё это правильные решения. Первая стадия реализована без архитектурных долгов.

**Критических багов: 2** (P0) — оба блокируют корректную работу ingest в production.  
**Средних проблем: 4** (P1) — влияют на корректность данных и UI.  
**Мелких замечаний: 5** (P2/P3) — качество кода, дублирование.

---

## 🔴 P0 — Критические баги (исправить до первого запуска)

### BUG-01: `write_page` — `is_new` проверяется ПОСЛЕ записи

**Файл:** `app/core/wiki_fs.py`, функция `write_page`, строка ~230

**Проблема:**
```python
# ТЕКУЩИЙ КОД (НЕВЕРНЫЙ)
path.write_text(rendered, encoding="utf-8")
action = "updated" if allow_overwrite and path.exists() else "created"
# path.exists() ВСЕГДА True — файл только что записан
```

Результат: все новые страницы в `log.md` отображаются как `updated`, никогда как `created`. Метрики инжеста неверны.

**Фикс:**
```python
# ПРАВИЛЬНЫЙ КОД
is_new = not path.exists()          # ← проверить ДО записи
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(rendered, encoding="utf-8")
action = "created" if is_new else "updated"
```

---

### BUG-02: `_find_related_pages` — две проблемы в генерируемом коде

**Файл:** `app/agents/ingest_agent.py`, метод `_find_related_pages`

**Проблема 1:** `WIKI_ROOT` инжектируется как строка (`str`), но в коде используется как `Path`:
```python
# В preamble interpreter.py:
WIKI_ROOT = '/path/to/wiki-data'

# В коде агента:
for md in WIKI_ROOT.rglob("*.md"):  # AttributeError: str has no rglob()
```

**Проблема 2:** переменная `result` присваивается, но не выводится в stdout → `result_json` всегда `None` → `wiki_context` в Step 1 всегда пустой → LLM не видит существующие страницы → конфликты **не обнаруживаются**.

**Фикс:**
```python
def _find_related_pages(self, source_content: str, project: str) -> list[WikiPage]:
    code = f"""
import re, json
from pathlib import Path

wiki_dir = Path({str(self.fs.wiki_dir)!r})
source = {source_content[:3000]!r}

stopwords = {{'this', 'that', 'with', 'from', 'have', 'will', 'been',
              'they', 'their', 'what', 'when', 'also', 'into', 'more'}}
words = set(
    w.lower() for w in re.findall(r'\\b[a-zA-Zа-яА-Я]{{5,}}\\b', source)
    if w.lower() not in stopwords
)

candidates = []
for md in wiki_dir.rglob("*.md"):
    try:
        text = md.read_text(encoding="utf-8").lower()
        overlap = sum(1 for w in words if w in text)
        rel = md.relative_to(wiki_dir).with_suffix("").as_posix()
        candidates.append((rel, overlap))
    except Exception:
        pass

result = [slug for slug, score in sorted(candidates, key=lambda x: -x[1])[:5]
          if score > 0]
print(json.dumps(result))  # ← ОБЯЗАТЕЛЬНО
"""
    output = self.interpreter.execute(code)
    slugs: list[str] = output.result_json or []
    # ... остаток метода без изменений
```

**Эффект бага:** при инжесте `frontend/redis_cache.md` LLM не увидит уже созданную `backend/infrastructure/redis-cache` → конфликт `cross_project_difference` **не будет зафиксирован** в `conflicts.md`.

---

## 🟡 P1 — Средние проблемы

### ISSUE-03: `ContextBudget()` создаётся без settings

**Файл:** `app/agents/ingest_agent.py`, метод `__init__`

```python
# ТЕКУЩИЙ КОД
self.budget = ContextBudget()  # дефолтные значения захардкожены

# ПРАВИЛЬНО
self.budget = ContextBudget(settings)  # уважает yaml-конфиг
```

При кастомных лимитах в `settings.yaml` бюджет будет игнорировать их.

---

### ISSUE-04: Step 1 — неверный slot для `wiki_context`

**Файл:** `app/agents/ingest_agent.py`, метод `_step1_analyze`

```python
# ТЕКУЩИЙ КОД
source_content=self.budget.trim(source_content, "wiki_context"),  # 21 000 chars — OK
wiki_context=self.budget.trim(wiki_context, "history"),            # 7 000 chars — МАЛО!
```

`wiki_context` (существующие wiki-страницы для контекста) обрезается до 7 000 символов — это лимит истории чата, не wiki-контекста. При 5–6 связанных страницах контекст будет усечён.

**Фикс:**
```python
# Использовать fit_wiki_pages или правильный slot
wiki_pages = self._find_related_pages(source_content, project)
wiki_context = self._build_wiki_context(wiki_pages)  # fit_wiki_pages внутри
# build_wiki_context уже использует budget.fit_wiki_pages — это правильно
# Убрать дополнительный trim в prompt.format()
```

---

### ISSUE-05: Расходятся wikilink regex в `linter.py` и `WikiPage`

**Файлы:** `app/core/linter.py` и `app/core/wiki_fs.py`

```python
# linter.py
wikilink_re = re.compile(r"\[\[([^\]|#]+)(?:(#)([^\]|]+))?(?:\|[^\]]+)?\]\]")

# wiki_fs.py — WikiPage.wikilinks property
re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]", self.content)
```

Паттерны захватывают части ссылки по-разному. При сложных форматах (`[[slug#anchor|text]]`) могут дать разные результаты. Если `WikiPage.wikilinks` не найдёт ссылку — `_incoming` не построится корректно и `_check_orphans` выдаст ложные срабатывания.

**Фикс:** вынести общий regex в `utils.py`:
```python
# utils.py
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]+)?\]\]")

def extract_wikilinks(text: str) -> list[str]:
    """Extract unique slugs from [[slug]], [[slug|text]], [[slug#anchor]] links."""
    slugs, seen = [], set()
    for m in WIKILINK_RE.finditer(text):
        slug = m.group(1).strip()
        if slug and slug not in seen:
            slugs.append(slug)
            seen.add(slug)
    return slugs
```

Затем использовать `extract_wikilinks` в обоих местах.

---

### ISSUE-06: `_heading_to_anchor` продублирована в `linter.py`

**Файлы:** `app/core/linter.py` (строка ~260) и `app/core/wiki_fs.py`

Одинаковая функция определена дважды. Нужно перенести в `utils.py` как публичную:

```python
# utils.py
def heading_to_anchor(heading: str) -> str:
    """Convert markdown heading text to GitHub-style anchor slug."""
    anchor = heading.lower().strip()
    anchor = re.sub(r"[`*_\[\]()]", "", anchor)
    anchor = re.sub(r"[^\w\s-]", "", anchor)
    anchor = re.sub(r"\s+", "-", anchor)
    return anchor.strip("-")
```

Импортировать в обоих файлах вместо локальной копии.

---

## 🟢 P2 — Мелкие замечания

### NOTE-07: `char_delta` — двойной `read_page` на каждый slug

**Файл:** `app/agents/ingest_agent.py`, метод `run`

```python
# ТЕКУЩИЙ КОД — read_page вызывается дважды для каждого slug
char_delta=sum(
    len(self.fs.read_page(s).raw)
    for s in pages_created + pages_updated
    if self.fs.read_page(s)   # ← первый вызов
),
# ↑ второй вызов в генераторе
```

**Фикс:**
```python
def _page_size(slug: str) -> int:
    p = self.fs.read_page(slug)
    return len(p.raw) if p else 0

char_delta=sum(_page_size(s) for s in pages_created + pages_updated),
```

---

### NOTE-08: `count_chars` продублирован в `token_budget.py`

Есть `count_chars` как module-level функция и как метод `ContextBudget.count_chars`. Оба возвращают `len(text)`. Оставить одну версию — модульную функцию, убрать метод (или оставить метод как `return count_chars(text)`).

---

### NOTE-09: `append_skill` — обратный хронологический порядок

**Файл:** `app/core/wiki_fs.py`, метод `append_skill`

```python
content = content.replace(
    section_header,
    f"{section_header}\n{entry}",  # новый skill вставляется ПЕРВЫМ в секцию
    1
)
```

При множестве добавлений: самые новые скиллы вверху секции. Это может быть намеренно (новые важнее), но не задокументировано. Добавить комментарий или изменить на append-порядок.

---

### NOTE-10: `_update_index_entry` вызывает `list_pages()` при каждом `write_page`

**Файл:** `app/core/wiki_fs.py`

При rebuild из N файлов — O(N²) читаемых страниц. Некритично при малой базе, но стоит зафиксировать:

```python
# TODO: при rebuild кешировать список страниц,
# сейчас list_pages() вызывается при каждом write_page — O(N²) при rebuild
```

---

### NOTE-11: `REQUIRED_FRONTMATTER` и `_check_frontmatter` расходятся

**Файлы:** `app/core/wiki_fs.py` и `app/core/linter.py`

`REQUIRED_FRONTMATTER` в `wiki_fs.py` включает `supersedes`, `superseded_by`, `tags`, `sources`. `_check_frontmatter` в `linter.py` проверяет только: `title`, `project`, `type`, `confidence`, `sources`, `last_confirmed`, `created`.

`supersedes`/`superseded_by` намеренно nullable и не валидируются — добавить комментарий:
```python
# supersedes и superseded_by — nullable, валидируются отдельно
# в _check_superseded_active, не здесь
```

---

## 🖥️ UI — Некликабельные ссылки

### BUG-12: Ссылки `[[slug]]` в ответах чата

**Файл:** `app/ui/index.html`, компонент `Message`

Ссылки рендерятся как `<span>` с `onClick` — это верно. Проблема в том, что при клике `setHighlightedSlug(slug)` вызывается, но правая панель может быть закрыта (`rightOpen === false`) — страница загружается, но не видна.

**Фикс в `App` компоненте:**
```jsx
// Передавать комбо-колбэк вместо setHighlightedSlug напрямую:
const handleSlugClick = useCallback((slug) => {
  setHighlightedSlug(slug);
  setRightOpen(true);  // ← открыть панель автоматически
}, []);

// В JSX:
<Message key={i} msg={msg} onSlugClick={handleSlugClick} />

// Также в send() при cited событии:
} else if (ev.type === 'cited') {
  cited.push(ev.slug);
  setHighlightedSlug(ev.slug);
  setRightOpen(true);  // ← добавить
}
```

---

### BUG-13: Ссылки `[[slug]]` в правой панели (page viewer)

**Файл:** `app/ui/index.html`, компонент `WikiPanel`

Обработчик кликов ищет `<a.wikilink>` с `href="#wiki/slug"` в `content_html`. Но бэкенд, скорее всего, не рендерит `[[slug]]` в такой формат.

**Диагностика:** открыть консоль браузера и выполнить:
```js
fetch('/api/wiki/page/SLUG').then(r=>r.json()).then(d=>console.log(d.content_html))
```

**Фикс на фронтенде** (не зависит от бэкенда):
```jsx
const openPage = async (slug) => {
  setActivePage(slug);
  try {
    const data = await api.get(`/wiki/page/${slug}`);
    // Постобработка: конвертировать [[slug]] → кликабельные ссылки
    data.content_html = (data.content_html || '').replace(
      /\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]/g,
      (_, s, label) => {
        const display = label || s.split('/').pop().replace(/-/g, ' ');
        return `<a href="#wiki/${s}" class="wikilink">${display}</a>`;
      }
    );
    setPageData(data);
  } catch { setPageData(null); }
};
```

Обработчик делегации в `page-content` уже корректен — он ищет `closest('a.wikilink')` и парсит `href.replace('#wiki/', '')`. После постобработки выше он заработает.

---

## Итоговая таблица

| # | Приоритет | Файл | Проблема | Трудоёмкость |
|---|-----------|------|----------|--------------|
| BUG-01 | 🔴 P0 | `wiki_fs.py:~230` | `is_new` после записи | 2 мин |
| BUG-02 | 🔴 P0 | `ingest_agent.py` | `_find_related_pages`: WIKI_ROOT not Path, нет print | 10 мин |
| ISSUE-03 | 🟡 P1 | `ingest_agent.py` | `ContextBudget()` без settings | 1 мин |
| ISSUE-04 | 🟡 P1 | `ingest_agent.py` | `wiki_context` trim в неверный slot | 5 мин |
| ISSUE-05 | 🟡 P1 | `linter.py` + `wiki_fs.py` | расходящийся wikilink regex | 15 мин |
| ISSUE-06 | 🟡 P1 | `linter.py` + `wiki_fs.py` | дублирование `_heading_to_anchor` | 5 мин |
| BUG-12 | 🟡 P1 | `index.html` | slug-клик не открывает правую панель | 5 мин |
| BUG-13 | 🟡 P1 | `index.html` | `[[slug]]` в page viewer не кликабельны | 10 мин |
| NOTE-07 | 🟢 P2 | `ingest_agent.py` | двойной `read_page` в char_delta | 3 мин |
| NOTE-08 | 🟢 P2 | `token_budget.py` | дубль `count_chars` | 2 мин |
| NOTE-09 | 🟢 P3 | `wiki_fs.py` | обратный порядок в `append_skill` | 1 мин |
| NOTE-10 | 🟢 P3 | `wiki_fs.py` | O(N²) `list_pages` при rebuild | TODO-comment |
| NOTE-11 | 🟢 P3 | `linter.py` | расхождение REQUIRED_FRONTMATTER | комментарий |

**Суммарно:** ~1 час работы закрывает все P0+P1 и делает систему production-ready для Stage 1.
