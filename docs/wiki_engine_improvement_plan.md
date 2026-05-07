# Wiki Engine Improvement Plan

## Purpose

This plan consolidates the project review findings into a practical implementation roadmap. The goal is to improve Wiki Engine as a reliable LLM-maintained markdown knowledge base without turning it into an over-engineered enterprise swamp. The system should remain local-first, markdown-first, inspectable, and useful for real knowledge work.

## Current strategic direction

Wiki Engine should not be positioned as a generic RAG application. Its stronger identity is:

> A local-first LLM-maintained knowledge base for curated markdown sources.

The core product loop is:

```text
raw markdown sources -> generated wiki layer -> query/chat -> conflict resolution -> skills/rules -> better future ingest
```

The highest-value architectural idea is the persistent wiki layer: answers, links, conflicts, and rules should accumulate instead of disappearing into chat history.

---

## Work already recorded as technical debt

These items are important but intentionally deferred for now:

1. CodeInterpreter is not a real sandbox.
2. No authentication / authorization.
3. Retrieval is too primitive.
4. Docker defaults are development-oriented, not production-ready.

Tracked in GitHub issue #1.

---

## Workstreams

## 1. Cross-linking and knowledge graph quality

### Problem

Generated wiki pages contain too few internal `[[slug]]` links. The project rules already say that WikiLinks are the navigation layer, but the generation pipeline does not give the agent a strong enough map of existing pages and concepts.

### Root causes

1. Step 2 generation only receives the planned page, source sections, and existing page content.
2. Related page discovery is simple word-overlap and only returns a narrow context.
3. The prompt asks the model to use links but does not provide a compact candidate list.
4. The linter detects broken links but does not flag likely missing links.

### Target outcome

Generated pages should consistently link known entities and concepts on first meaningful mention without inventing slugs or overlinking.

### Implementation plan

#### 1.1 Add link candidate map

Add a method to `WikiFS`:

```python
def build_link_candidates(project: str | None = None) -> list[dict]:
    ...
```

Each candidate should include:

```json
{
  "slug": "project/category/page",
  "title": "Human title",
  "project": "project",
  "type": "entity|concept|index|log",
  "tags": ["tag"],
  "aliases": ["Human title", "page", "page alias"]
}
```

Initial aliases:
- page title;
- slug last segment with hyphens replaced by spaces;
- slug last segment as raw text;
- tags that are specific enough.

Later aliases can move into frontmatter as an optional `aliases` field.

#### 1.2 Inject link candidates into Step 2 prompt

Extend `STEP2_PROMPT` with:

```text
## Known Wiki Pages / Link Candidates
- [[project/storage/redis]] — Redis; aliases: Redis, redis cache
- [[project/backend/fastapi]] — FastAPI; aliases: FastAPI, API backend
```

Add binding rules:

```text
- Link known entities/concepts on first meaningful mention.
- Prefer project-local links.
- Use `_general` links for shared concepts.
- Do not link every repeated mention.
- Do not invent slugs that are not in the candidate list.
```

#### 1.3 Add conservative auto-linker

After LLM page generation, run deterministic post-processing:

- find first mention of known aliases;
- insert `[[slug|display text]]`;
- skip headings, code blocks, URLs, existing wikilinks, markdown links;
- avoid generic aliases shorter than a safe threshold;
- cap auto-added links per page, e.g. 5 to 8.

This should be conservative and optional by config.

#### 1.4 Add missing-link lint

Add new lint kind:

```python
missing_wikilink
```

Detection logic:

- known page title or alias appears as plain text in another page;
- target page is not already linked;
- flag as `info` or low-severity `warning`;
- exclude current page, index/log pages, code blocks, and overly generic aliases.

#### 1.5 Add graph metrics

Expose or compute:

- outgoing links per page;
- incoming links per page;
- orphan pages;
- low-link pages;
- average outgoing links per non-index page;
- broken links ratio;
- missing links count.

### Acceptance criteria

- New generated pages link relevant existing pages when known entities are mentioned.
- No invented slugs are created just to satisfy link density.
- Linter can flag likely missing internal links.
- Tests cover link candidate generation, auto-linking, and missing-link lint.

---

## 2. Clickable links in chat and right sidebar

### Problem

Wiki links are visually present but not reliably clickable. Backend page rendering converts `[[slug]]` into HTML anchors, and CSS styles `.wikilink`, but UI navigation is not unified.

### Root causes

1. Chat answers may contain raw `[[slug]]` text that is rendered as text.
2. Page viewer receives rendered HTML, but React handlers are not attached inside `dangerouslySetInnerHTML` output.
3. HTML wikilinks use `href="#wiki/slug"`, while the app needs explicit state updates to open the page in the right inspector.

### Target outcome

Clicking any wiki link in chat, cited chips, or page content opens the target page in the right sidebar and highlights it in the wiki tree.

### Implementation plan

#### 2.1 Centralize page opening

Create one UI function:

```js
async function openWikiPage(slug, options = {}) {
  ...
}
```

It should:
- fetch `/api/wiki/page/{slug}`;
- set selected/open page state;
- open the right sidebar if needed;
- highlight the page in the tree;
- update hash if requested;
- show a toast if the page does not exist.

#### 2.2 Render chat wikilinks explicitly

Parse assistant messages for:

- `[[slug]]`;
- `[[slug|text]]`;
- `[[slug#anchor]]`.

Render them as clickable React elements.

#### 2.3 Add delegated click handling for page content

For rendered HTML page content:

```js
function handlePageContentClick(e) {
  const link = e.target.closest('a.wikilink');
  if (!link) return;
  e.preventDefault();
  const href = link.getAttribute('href') || '';
  const slug = href.replace(/^#wiki\//, '');
  openWikiPage(slug, { updateHash: true });
}
```

#### 2.4 Add hash routing

On initial load and `hashchange`:

```js
if (window.location.hash.startsWith('#wiki/')) {
  openWikiPage(window.location.hash.replace('#wiki/', ''));
}
```

#### 2.5 Render cited slugs as chips

The SSE stream emits cited slug events. Store them per assistant message and show clickable chips below the answer.

### Acceptance criteria

- Clicking `[[slug]]` in chat opens the wiki page.
- Clicking a wikilink inside page content opens the linked page.
- Cited slugs are visible as clickable chips.
- `#wiki/project/page` opens the correct page.
- Missing target pages show a clear toast.

---

## 3. UI / UX redesign

### Problem

The current single-file UI is functional but visually outdated, dense, dark by default, and not clear enough for knowledge work. Conflict resolution is especially confusing: users cannot easily understand what they are resolving, what rule is created, and what changes afterwards.

### Target outcome

A modern, readable knowledge workspace with clear navigation, clickable citations, page inspection, and a guided conflict resolution flow.

### Visual direction

Default theme should be light.

Recommended palette:

```text
background: #F7F8FA
surface:    #FFFFFF
border:     #E5E7EB
primary:    #2563EB or #4F46E5
text:       #111827
muted:      #6B7280
success:    #16A34A
warning:    #D97706
danger:     #DC2626
```

Dark mode can remain later as an option, but not as the default.

### Layout direction

Use three zones:

1. Left sidebar: sessions, projects, filters.
2. Center workspace: chat and answers.
3. Right inspector: page, links, conflicts, sources.

The right sidebar should act as a contextual inspector, not a miscellaneous storage closet with a scrollbar.

### Conflict resolution redesign

Replace compact conflict cards with a guided detail view.

#### Step 1: Understand the conflict

Show side-by-side evidence cards:

- Current wiki knowledge;
- New source claim.

Include:
- conflict type;
- source file;
- affected wiki page;
- current context;
- source context;
- dates/confidence if available.

#### Step 2: Choose interpretation

Clear choices:

- Trust current wiki.
- Trust new source.
- Both are true in different contexts.
- Needs manual follow-up.
- Ignore / not a real conflict.

Each choice should explain what it means.

#### Step 3: Preview generated skill/rule

Before saving, show a generated draft rule and allow editing.

Example:

```text
For project X: Redis and PostgreSQL are not conflicting storage choices; Redis is used for cache, PostgreSQL for persistent data.
```

The user must be able to edit the rule before it is saved to `skills.md`.

#### Step 4: Preview changes

Show what will happen:

- conflict status becomes `RESOLVED`;
- rule is appended to `skills.md` if enabled;
- affected pages are listed;
- automatic page updates, if not implemented, are explicitly not performed.

#### Step 5: Confirm

Use explicit buttons:

- Resolve conflict and save rule;
- Resolve without rule;
- Add note only;
- Mark as needs follow-up.

### UI components

- `ConflictList`: filters, counters, status chips.
- `ConflictDetail`: side-by-side evidence, choice selection, action summary.
- `SkillPreviewEditor`: generated rule preview, editable text, section selector.
- `PageInspector`: metadata, outgoing links, backlinks, sources, related conflicts.
- `CitationChips`: cited pages under assistant answers.

### Implementation phases

#### Phase 1: Improve current single-file UI

- light theme variables;
- better typography and spacing;
- clickable wiki links;
- citation chips;
- conflict detail modal with skill preview.

#### Phase 2: Split UI into modules

Target structure:

```text
app/ui/
  index.html
  src/
    App.jsx
    api.js
    components/
      Chat.jsx
      WikiSidebar.jsx
      PageInspector.jsx
      ConflictList.jsx
      ConflictDetail.jsx
      SkillPreviewEditor.jsx
```

#### Phase 3: Optional frontend build tooling

Only add build tooling if the single-file UI becomes too painful to maintain. Do not add complexity just to make the repo look more grown-up. Repositories do not need ceremonies, despite what frontend culture suggests.

### Acceptance criteria

- UI is light, readable, and less visually dense.
- Chat citations and wikilinks are clickable.
- Conflict resolution is understandable without reading backend code.
- User can preview and edit generated rules before saving them.
- Right inspector clearly shows page, links, sources, and conflicts.

---

## 4. Baseline automated tests

### Problem

The project declares pytest tooling, but baseline tests are not clearly present. This makes changes risky.

### Target outcome

A deterministic test suite covering core filesystem, linting, mocked agent flows, and minimal API smoke tests.

### Test plan

#### 4.1 WikiFS tests

- create/read page with valid frontmatter;
- reject missing frontmatter;
- enforce char limits;
- save/read/list raw files;
- append and resolve conflict;
- slug validation when added.

#### 4.2 WikiLinter tests

- broken wikilink;
- missing anchor;
- duplicate title;
- stale page;
- superseded active page;
- missing wikilink after it is added.

#### 4.3 Agent tests with mocked LLMClient

- IngestAgent happy path;
- malformed JSON retry/failure;
- QueryAgent factual query;
- citations in answer.

#### 4.4 API smoke tests

- `/api/health`;
- wiki tree endpoint with temp `wiki-data`;
- invalid ingest upload rejection.

### Requirements

- Use `tmp_path` for isolated wiki-data.
- No real LLM or network calls.
- Keep tests deterministic.

### Acceptance criteria

- `pytest tests/ -v` passes locally.
- No external calls during tests.
- At least WikiFS, WikiLinter, and one mocked ingest flow are covered.

---

## 5. Small code quality fixes

### Already fixed

1. `LLMClient` now preserves explicit `temperature=0.0` instead of falling back to default temperature.
2. `QueryAgent` now uses `ContextBudget(settings)` instead of default context budgets.

### Remaining small fixes

#### 5.1 Do not silently swallow page parse errors

`WikiFS._parse_page()` currently catches all exceptions and returns `None`. Missing file can return `None`, but parse errors should be visible in logs.

Plan:

- log parse errors with page path and exception;
- keep non-strict behavior for now to avoid breaking UI;
- optionally add strict mode later.

#### 5.2 Reduce O(N²) index update behavior

`WikiFS._update_index_entry()` calls `list_pages()` on every page write. During rebuild this can become expensive.

Plan:

- add a deferred index update mode for batch ingest/rebuild;
- rebuild index once after batch operation;
- keep current behavior for normal single-page writes.

#### 5.3 Prepare slug validation

Add helper:

```python
def validate_slug(slug: str) -> None:
    ...
```

Rules:

- no absolute paths;
- no `..`;
- no backslashes;
- allowed characters: lowercase letters, numbers, `_`, `-`, `/`;
- no empty segments;
- no leading/trailing slash.

### Acceptance criteria

- parse errors are visible in logs;
- rebuild can defer index updates;
- slug helper has tests.

---

## Recommended implementation order

### Sprint 1: Stabilize basics

1. Add baseline tests.
2. Add slug validation helper and tests.
3. Improve parse error logging.
4. Add clickable wiki links in UI.
5. Add cited chips.

### Sprint 2: Cross-linking

1. Add link candidate map.
2. Inject candidates into Step 2 prompt.
3. Add conservative auto-linker.
4. Add missing-link lint.
5. Add graph metrics.

### Sprint 3: Conflict UX

1. Add conflict detail modal/panel.
2. Add side-by-side evidence cards.
3. Add resolution choice explanations.
4. Add editable skill preview.
5. Add explicit action summary before save.

### Sprint 4: Visual redesign

1. Switch default to light theme.
2. Improve typography and spacing.
3. Rework right sidebar into contextual inspector.
4. Add page links/backlinks/sources view.
5. Consider splitting UI into components.

### Sprint 5: Deferred technical debt

1. Real sandboxing or disable code execution in query flow.
2. Auth and authorization.
3. Retrieval upgrade.
4. Production Docker profile.

---

## Success metrics

### Knowledge graph

- Average outgoing wikilinks per non-index page.
- Orphan page percentage.
- Missing-link lint count.
- Broken-link ratio.

### Query quality

- Percentage of answers with citations.
- Percentage of citations that are clickable and resolve to real pages.
- Number of user fallbacks to raw source files.

### Conflict workflow

- Open conflicts count.
- Average time to resolve conflict.
- Percentage of resolved conflicts with approved skill/rule.
- Number of later conflicts prevented by existing skills.

### UX

- Upload -> ingest -> ask -> inspect cited page completion rate.
- Number of clicks needed to resolve a conflict.
- User-visible errors during normal workflow.

---

## Non-goals for now

- Do not add vector DB immediately.
- Do not add enterprise multi-user permissions yet.
- Do not add PDF/DOCX ingestion as first-class until markdown flow is reliable.
- Do not auto-resolve conflicts without human approval.
- Do not add frontend build tooling until the single-file UI becomes the bottleneck.
