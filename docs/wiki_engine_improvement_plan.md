# Wiki Engine Improvement Plan

## Purpose

This plan consolidates the project review findings into a practical implementation roadmap. The goal is to improve Wiki Engine as a reliable LLM-maintained markdown knowledge base without turning it into an over-engineered enterprise swamp. The system should remain local-first, markdown-first, inspectable, and useful for real knowledge work.

## Current strategic direction

Wiki Engine should not be positioned as a generic RAG application. Its stronger identity is:

> A local-first LLM-maintained knowledge base for curated markdown sources.

The core product loop is:

```text
raw markdown sources -> generated wiki layer -> query/chat -> review/diff -> conflict resolution -> skills/rules -> better future ingest
```

The highest-value architectural idea is the persistent wiki layer: answers, links, conflicts, provenance, and rules should accumulate instead of disappearing into chat history.

---

## Work already recorded as technical debt

These items are important but intentionally deferred for now:

1. CodeInterpreter is not a real sandbox.
2. No authentication / authorization.
3. Retrieval is too primitive.
4. Docker defaults are development-oriented, not production-ready.

Tracked in GitHub issue #1.

---

## Architectural decision: no silent full-page overwrites

### Problem

The current ingest flow can ask the LLM to regenerate a full existing page and overwrite it when Step 1 chooses `action="update"` or `action="supersede"`. This is dangerous because the model can accidentally drop old information, blur source attribution, or hide contradictions.

### Decision

Existing pages must not be silently rewritten during ingest.

Instead, update flow should become:

```text
new source
-> analysis plan
-> candidate page / candidate patch
-> diff preview
-> conflict/provenance report
-> human approve / edit / reject
-> apply write
```

### Target mechanism

#### 1. Analysis plan

Step 1 still produces:

- pages_to_create;
- pages_to_update;
- pages_to_supersede;
- conflicts;
- related_pages_read;
- analysis_notes.

But this is now a plan, not permission to overwrite.

#### 2. Candidate update generation

For every planned update, Step 2 writes a draft artifact instead of overwriting `wiki/` directly:

```text
wiki-data/drafts/ingest-YYYYMMDD-HHMMSS/
  plan.json
  pages/
    project/page.md
  diffs/
    project__page.diff.md
  conflicts.json
  provenance_report.md
```

#### 3. Diff-first update

For an existing page, the system should produce:

- old page raw markdown;
- candidate new page markdown;
- unified diff;
- summary of semantic changes;
- list of claims added/removed/changed;
- affected wikilinks;
- provenance coverage.

#### 4. Apply only after approval

UI actions:

- Approve and apply;
- Edit candidate and apply;
- Reject;
- Mark as needs follow-up;
- Convert to conflict only.

#### 5. Safe update policy before review UI exists

Until review UI is implemented:

- creates may still be written directly if low risk;
- updates/supersedes should be stored as drafts or logged as pending review;
- conflicts should still be recorded.

### Acceptance criteria

- Ingest no longer silently overwrites existing pages.
- Every update candidate has a visible diff.
- User can approve/reject page updates before applying.
- Removed or changed claims are visible in the diff summary.
- Conflicts remain separate from ordinary updates.

---

## Workstreams

## 1. Cross-linking and knowledge graph quality

### Problem

Generated wiki pages contain too few internal `[[slug]]` links. The project rules already say that WikiLinks are the navigation layer, but the generation pipeline does not give the agent a strong enough map of existing pages and concepts.

### Root causes

1. Step 2 generation only receives the planned page, source sections, and existing page content.
2. Related page discovery is simple word-overlap and returns too narrow a context.
3. The prompt asks the model to use links but does not provide a compact candidate list.
4. The linter detects broken links but does not flag likely missing links.

### Target outcome

Generated pages should consistently link known entities and concepts on first meaningful mention without inventing slugs or overlinking.

### Implementation plan

#### 1.1 Add `## Related Pages` / `## Связанные страницы`

Every normal `entity`, `concept`, `comparison`, and `synthesis` page should end with a related pages section:

```markdown
## Связанные страницы
- [[project/foo]] — почему связано
- [[_general/bar]] — общий концепт
```

Rules:

- minimum 2 related pages for normal pages when candidates exist;
- do not invent links just to satisfy the minimum;
- link project-local pages first;
- use `_general` for shared concepts;
- no repeated links already used heavily in the body unless they are central.

Lint should flag:

- pages with no outgoing links;
- pages with no `## Связанные страницы` section;
- pages with related pages that do not exist.

#### 1.2 Add `synopsis` or `## Synopsis`

Every generated page should have a short routing summary.

Preferred frontmatter field:

```yaml
synopsis: "2-3 sentence summary used for routing, search, and preview."
```

Visible section is also allowed:

```markdown
## Synopsis
Короткое описание страницы: что здесь собрано, когда читать, какие вопросы закрывает.
```

Usage:

- query routing reads index + synopsis before full page;
- right inspector shows synopsis before body;
- search results use synopsis as preview;
- future progressive loading uses synopsis to reduce context cost.

#### 1.3 Add link candidate map

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
  "type": "entity|concept|comparison|synthesis|index|log",
  "tags": ["tag"],
  "aliases": ["Human title", "page", "page alias"],
  "synopsis": "short summary if available"
}
```

Initial aliases:

- page title;
- slug last segment with hyphens replaced by spaces;
- slug last segment as raw text;
- tags that are specific enough;
- optional frontmatter `aliases` later.

#### 1.4 Inject link candidates into Step 2 prompt

Extend `STEP2_PROMPT` with:

```text
## Known Wiki Pages / Link Candidates
- [[project/storage/redis]] — Redis; aliases: Redis, redis cache
- [[project/backend/fastapi]] — FastAPI; aliases: FastAPI, API backend
```

Add binding rules:

```text
- Link known entities/concepts on first meaningful mention.
- Add a `## Связанные страницы` section when related pages exist.
- Prefer project-local links.
- Use `_general` links for shared concepts.
- Do not link every repeated mention.
- Do not invent slugs that are not in the candidate list.
```

#### 1.5 Add conservative auto-linker

After LLM page generation, run deterministic post-processing:

- find first mention of known aliases;
- insert `[[slug|display text]]`;
- skip headings, code blocks, URLs, existing wikilinks, markdown links;
- avoid generic aliases shorter than a safe threshold;
- cap auto-added links per page, e.g. 5 to 8.

This should be conservative and optional by config.

#### 1.6 Add missing-link lint

Add new lint kind:

```python
missing_wikilink
```

Detection logic:

- known page title or alias appears as plain text in another page;
- target page is not already linked;
- flag as `info` or low-severity `warning`;
- exclude current page, index/log pages, code blocks, and overly generic aliases.

#### 1.7 Add graph metrics

Expose or compute:

- outgoing links per page;
- incoming links per page;
- orphan pages;
- low-link pages;
- average outgoing links per non-index page;
- broken links ratio;
- missing links count;
- pages without related pages section.

### Acceptance criteria

- New generated pages link relevant existing pages when known entities are mentioned.
- Normal pages include `synopsis` and `## Связанные страницы` when candidates exist.
- No invented slugs are created just to satisfy link density.
- Linter can flag likely missing internal links.
- Tests cover link candidate generation, auto-linking, related pages, synopsis, and missing-link lint.

---

## 2. Related page discovery during ingest

### Problem

`_find_related_pages()` currently uses simple word overlap and returns too few pages. This weakens updates, conflict detection, and cross-link generation.

### Immediate decision

Increase related-page retrieval to at least 10 pages.

This is a quick improvement, not the final retrieval architecture. It gives Step 1 more context, which should improve detection of:

- existing pages that need updates;
- conflicts;
- related pages;
- duplicate or overlapping pages.

### Recommended quick change

- Change top related pages from 5 to 10.
- Prefer project-local pages but include `_general` pages.
- Keep a hard context budget so Step 1 does not become a document landfill with confidence issues.
- Log related slugs in `analysis_notes` or ingest result for audit.

### Better follow-up

Replace overlap-only retrieval with weighted scoring:

1. exact title/slug/alias matches;
2. project-local boost;
3. `_general` boost for shared concepts;
4. tag overlap;
5. heading/body lexical score;
6. later: BM25/FTS.

### Acceptance criteria

- Step 1 receives at least 10 candidate pages when 10 candidates exist.
- Related page list is visible in ingest diagnostics.
- Context budget is still respected.
- Tests cover candidate retrieval count and ordering behavior.

---

## 3. Source deduplication and source manifest

### Problem

Repeated uploads of the same document can trigger duplicate ingest and duplicate wiki updates.

### Target outcome

The engine should identify duplicate or unchanged sources before LLM ingest.

### Implementation plan

#### 3.1 SHA256 source manifest

Create source state file:

```text
wiki-data/.state/source_manifest.json
```

This is engineering state, not knowledge content. It does not need to be markdown.

Suggested structure:

```json
{
  "raw/project/file.md": {
    "sha256": "...",
    "size": 12345,
    "first_seen": "2026-05-07T12:00:00",
    "last_seen": "2026-05-07T12:00:00",
    "last_ingested": "2026-05-07T12:05:00",
    "status": "active",
    "ingest_runs": ["ingest-20260507-120500"]
  }
}
```

#### 3.2 Ingest behavior

Before analysis:

- compute SHA256;
- if exact same file already ingested, skip or ask whether to force re-ingest;
- if same path changed, mark as changed and create diff/review candidate;
- if same hash under different path, flag duplicate source.

#### 3.3 UI behavior

Show:

- new source;
- changed source;
- duplicate source;
- unchanged source;
- force re-ingest option.

### Acceptance criteria

- Exact duplicate sources do not trigger LLM ingest by default.
- Changed sources are detected.
- Duplicate source warning is visible in ingest result.
- Tests cover same path unchanged, same path changed, different path same hash.

---

## 4. Provenance and epistemic metadata

### Problem

The current page-level `confidence` field is useful but insufficient. The system needs stronger source attribution and explicit knowledge state.

### Target outcome

Every generated page should make it clear what is extracted, inferred, ambiguous, contradicted, and which source supports each important claim.

### Implementation plan

#### 4.1 Claim-level provenance

Add claim/paragraph source markers in page content.

Minimum form:

```markdown
Факт утверждения. ^[raw/project/source.md]
```

Preferred form with source range when available:

```markdown
Факт утверждения. ^[raw/project/source.md#L42-L58]
```

Rules:

- factual claims should have provenance marker;
- synthesis/inference claims should cite contributing sources;
- unsupported background prose should be marked as inferred or unverified;
- linter validates that referenced raw source exists;
- later linter validates line ranges if raw line mapping exists.

#### 4.2 Page metadata fields

Extend required or recommended frontmatter:

```yaml
confidence: 0.82
provenance_state: extracted | merged | inferred | ambiguous | mixed
contradicted_by:
  - project/page-a
  - project/page-b
needs_review: false
source_coverage: full | partial | weak
synopsis: "Short routing summary."
aliases: []
```

Field meaning:

- `confidence`: confidence in the page as a synthesized knowledge artifact.
- `provenance_state`: how the page was produced.
- `contradicted_by`: pages or conflicts that contradict this page.
- `needs_review`: true when human review is required.
- `source_coverage`: whether the content is well supported by sources.
- `synopsis`: short routing summary.
- `aliases`: explicit names used by linker/search.

#### 4.3 Confidence tags in content

Allow paragraph-level tags:

```markdown
[EXTRACTED] Прямой факт из источника. ^[raw/project/source.md]
[INFERRED] Вывод на основе нескольких источников. ^[raw/project/a.md] ^[raw/project/b.md]
[AMBIGUOUS] Источник формулирует это неоднозначно. ^[raw/project/source.md]
[UNVERIFIED] Фоновое знание без прямого источника.
```

Query behavior:

- if answer relies on `INFERRED`, say it is inferred;
- if answer relies on `AMBIGUOUS`, say it is ambiguous;
- avoid using `UNVERIFIED` as factual support.

#### 4.4 Lint rules

Add checks:

- missing provenance markers on factual pages;
- invalid raw source references;
- low confidence pages;
- contradicted pages;
- pages marked `needs_review`;
- excess unverified paragraphs.

### Acceptance criteria

- New pages include provenance markers for important claims.
- Frontmatter supports `provenance_state`, `contradicted_by`, `needs_review`, `source_coverage`, `synopsis`, and `aliases`.
- Linter flags invalid provenance and low-confidence/contradicted pages.
- Query answers can surface inferred/ambiguous status.

---

## 5. Duplicate detection, collapse audit, and synthesis queue

### Problem

As the wiki grows, multiple pages may describe the same concept, partially overlap, or become obsolete. Immediate auto-merge is risky; ignoring duplicates slowly rots the wiki.

### Target outcome

Add a separate audit function that detects possible duplicate/overlapping pages and proposes collapse/synthesis actions without silently merging content.

### Implementation plan

#### 5.1 Add audit command/function

Add a separate audit mode, for example:

```python
audit_duplicates_and_collapse_candidates(project: str | None = None)
```

or API route:

```text
POST /api/audit/duplicates
```

It should produce a structured report, not directly rewrite pages.

#### 5.2 Detection signals

Start deterministic/cheap:

- same or similar title;
- same aliases;
- same slug last segment;
- high tag overlap;
- high outgoing link overlap;
- same source references;
- highly similar synopsis;
- pages that cite each other but repeat content.

Later optional LLM audit:

- are these duplicates?
- should they be merged, cross-linked, or kept separate?
- what is the proposed target synthesis page?

#### 5.3 Collapse candidate types

The audit should classify:

- duplicate page: likely same concept;
- overlapping page: related but not identical;
- stale page: older version likely superseded;
- synthesis candidate: multiple pages should feed a new overview/synthesis page;
- split candidate: one page covers too many concepts.

#### 5.4 Synthesis queue

Do not merge automatically. Create queue items:

```text
wiki-data/synthesis_queue/
  cluster-001.yaml
```

Suggested structure:

```yaml
id: cluster-001
status: open
kind: duplicate | overlap | synthesis | stale
pages:
  - project/a
  - project/b
reason: "Similar title, shared sources, high synopsis overlap"
recommended_action: "create synthesis page and mark old page superseded after review"
created: 2026-05-07
```

#### 5.5 UI behavior

Dashboard should show:

- duplicate candidates;
- synthesis queue;
- stale/supersede candidates;
- review actions: create synthesis draft, mark not duplicate, merge after diff, supersede.

### Acceptance criteria

- Audit reports possible duplicates without modifying wiki pages.
- Synthesis/collapse candidates are stored as reviewable queue items.
- No automatic merge happens without approval.
- Tests cover duplicate title, alias overlap, shared source, and not-duplicate cases.

---

## 6. Clickable links in chat and right sidebar

### Problem

Wiki links are visually present but not reliably clickable. Backend page rendering converts `[[slug]]` into HTML anchors, and CSS styles `.wikilink`, but UI navigation is not unified.

### Target outcome

Clicking any wiki link in chat, cited chips, or page content opens the target page in the right sidebar and highlights it in the wiki tree.

### Implementation plan

1. Centralize page opening via `openWikiPage(slug)`.
2. Render chat wikilinks explicitly.
3. Add delegated click handling for page content.
4. Add hash route support for `#wiki/project/page`.
5. Render cited slugs as clickable chips.

### Acceptance criteria

- Clicking `[[slug]]` in chat opens the wiki page.
- Clicking a wikilink inside page content opens the linked page.
- Cited slugs are visible as clickable chips.
- `#wiki/project/page` opens the correct page.
- Missing target pages show a clear toast.

---

## 7. UI / UX redesign

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
3. Right inspector: page, links, conflicts, sources, provenance.

### Conflict resolution redesign

Replace compact conflict cards with a guided detail view.

Steps:

1. Understand the conflict: current wiki knowledge vs new source claim.
2. Choose interpretation: trust current, trust source, both true in context, needs follow-up, ignore.
3. Preview generated skill/rule.
4. Preview page/diff/provenance changes.
5. Confirm explicit action.

### UI components

- `ConflictList`;
- `ConflictDetail`;
- `SkillPreviewEditor`;
- `PageInspector`;
- `CitationChips`;
- `DiffViewer`;
- `ProvenancePanel`;
- `SynthesisQueue`.

### Acceptance criteria

- UI is light, readable, and less visually dense.
- Chat citations and wikilinks are clickable.
- Conflict resolution is understandable without reading backend code.
- User can preview and edit generated rules before saving them.
- User can review diffs before applying page updates.
- Right inspector clearly shows page, links, sources, provenance, and conflicts.

---

## 8. Baseline automated tests

### Problem

The project declares pytest tooling, but baseline tests are not clearly present. This makes changes risky.

### Target outcome

A deterministic test suite covering core filesystem, linting, mocked agent flows, and minimal API smoke tests.

### Test plan

#### 8.1 WikiFS tests

- create/read page with valid frontmatter;
- reject missing frontmatter;
- enforce char limits;
- save/read/list raw files;
- append and resolve conflict;
- slug validation when added;
- draft write/read/apply flow;
- source manifest read/write.

#### 8.2 WikiLinter tests

- broken wikilink;
- missing anchor;
- duplicate title;
- stale page;
- superseded active page;
- missing wikilink;
- missing related pages;
- invalid provenance marker;
- low confidence / contradicted page.

#### 8.3 Agent tests with mocked LLMClient

- IngestAgent happy path;
- update produces draft/diff, not direct overwrite;
- malformed JSON retry/failure;
- QueryAgent factual query;
- citations in answer.

#### 8.4 API smoke tests

- `/api/health`;
- wiki tree endpoint with temp `wiki-data`;
- invalid ingest upload rejection;
- duplicate source detection;
- draft listing/apply flow when implemented.

### Acceptance criteria

- `pytest tests/ -v` passes locally.
- No external calls during tests.
- At least WikiFS, WikiLinter, and one mocked ingest flow are covered.

---

## 9. Small code quality fixes

### Already fixed

1. `LLMClient` now preserves explicit `temperature=0.0` instead of falling back to default temperature.
2. `QueryAgent` now uses `ContextBudget(settings)` instead of default context budgets.

### Remaining small fixes

#### 9.1 Increase related pages count

Change `_find_related_pages()` from top 5 to at least top 10.

This is safe and useful as an immediate improvement, but it must be treated as temporary until retrieval is improved.

#### 9.2 Do not silently swallow page parse errors

`WikiFS._parse_page()` currently catches all exceptions and returns `None`. Missing file can return `None`, but parse errors should be visible in logs.

Plan:

- log parse errors with page path and exception;
- keep non-strict behavior for now to avoid breaking UI;
- optionally add strict mode later.

#### 9.3 Reduce O(N²) index update behavior

`WikiFS._update_index_entry()` calls `list_pages()` on every page write. During rebuild this can become expensive.

Plan:

- add a deferred index update mode for batch ingest/rebuild;
- rebuild index once after batch operation;
- keep current behavior for normal single-page writes.

#### 9.4 Prepare slug validation

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

- related page retrieval returns at least 10 candidates when available;
- parse errors are visible in logs;
- rebuild can defer index updates;
- slug helper has tests.

---

## Recommended implementation order

### Sprint 1: Stabilize basics

1. Add baseline tests.
2. Add slug validation helper and tests.
3. Improve parse error logging.
4. Increase `_find_related_pages()` to at least 10 pages.
5. Add clickable wiki links in UI.
6. Add cited chips.

### Sprint 2: Page structure and graph quality

1. Add `synopsis` field/section.
2. Add `## Связанные страницы` section.
3. Add link candidate map.
4. Inject candidates into Step 2 prompt.
5. Add conservative auto-linker.
6. Add missing-link lint.
7. Add graph metrics.

### Sprint 3: Safe ingest updates

1. Stop silent overwrite for existing page updates.
2. Add draft/candidate directory.
3. Add unified diff generation.
4. Add update approval/apply flow.
5. Show changed/added/removed claims.

### Sprint 4: Source identity and provenance

1. Add SHA256 source manifest.
2. Add duplicate/unchanged source detection.
3. Add claim-level provenance markers.
4. Add provenance validation lint.
5. Add page metadata: `provenance_state`, `contradicted_by`, `needs_review`, `source_coverage`, `aliases`.

### Sprint 5: Conflict UX and duplicate audit

1. Add conflict detail modal/panel.
2. Add editable skill preview.
3. Add duplicate/collapse audit.
4. Add synthesis queue.
5. Add UI for synthesis/collapse candidates.

### Sprint 6: Visual redesign

1. Switch default to light theme.
2. Improve typography and spacing.
3. Rework right sidebar into contextual inspector.
4. Add page links/backlinks/sources/provenance view.
5. Consider splitting UI into components.

### Sprint 7: Deferred technical debt

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
- Pages without `## Связанные страницы`.
- Pages without synopsis.

### Provenance and trust

- Percentage of factual paragraphs with provenance markers.
- Invalid provenance marker count.
- Low-confidence page count.
- Contradicted page count.
- Pages marked `needs_review`.

### Source management

- Duplicate source count.
- Skipped unchanged ingest count.
- Changed source count.
- Force re-ingest count.

### Query quality

- Percentage of answers with citations.
- Percentage of citations that are clickable and resolve to real pages.
- Number of user fallbacks to raw source files.
- Number of answers that explicitly mark inferred/ambiguous content.

### Conflict and review workflow

- Open conflicts count.
- Average time to resolve conflict.
- Percentage of resolved conflicts with approved skill/rule.
- Number of page updates approved vs rejected.
- Number of duplicate/synthesis candidates resolved.

### UX

- Upload -> ingest -> review diff -> apply -> ask -> inspect cited page completion rate.
- Number of clicks needed to resolve a conflict.
- User-visible errors during normal workflow.

---

## Non-goals for now

- Do not add vector DB immediately.
- Do not add enterprise multi-user permissions yet.
- Do not add PDF/DOCX ingestion as first-class until markdown flow is reliable.
- Do not auto-resolve conflicts without human approval.
- Do not silently merge duplicate pages.
- Do not silently overwrite existing wiki pages during ingest.
- Do not add frontend build tooling until the single-file UI becomes the bottleneck.
