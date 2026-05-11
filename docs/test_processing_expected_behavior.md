# Test Processing Expected Behavior Guide

## Purpose

This document explains how a coding agent or tester should verify processing results for the controlled test sources in `docs/test_sources/`.

The goal is to check not only whether files upload, but whether the wiki engine behaves correctly:

- creates pages in the right project;
- detects conflicts when documents contradict each other;
- does not treat different projects as the same project;
- keeps project separation;
- supports comparison queries;
- exposes uncertainty instead of inventing one final answer.

Use this guide after implementing or changing ingest, project selection, conflicts, search, rebuild, or UI upload behavior.

---

## Test files

Use these files in this exact order:

1. `docs/test_sources/01_eywa_baseline_source.md`
2. `docs/test_sources/02_eywa_conflict_source.md`
3. `docs/test_sources/03_aurora_cross_project_source.md`

Upload projects:

| File | Project |
|---|---|
| `01_eywa_baseline_source.md` | `eywa-demo` |
| `02_eywa_conflict_source.md` | `eywa-demo` |
| `03_aurora_cross_project_source.md` | `aurora-demo` |

---

## Before testing

Start from a clean test state if possible.

Recommended:

1. Use a temporary or empty `wiki-data` directory.
2. Confirm LLM settings are valid.
3. Start the app.
4. Open the UI.
5. Confirm upload supports `.md`, `.txt`, `.py`, `.docx`, `.pptx`, `.pdf`.
6. Confirm project selector can create or select `eywa-demo` and `aurora-demo`.

Windows command example:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
pip install -e .[dev]
uvicorn app.api.main:app --reload --port 8000
```

Open:

```text
http://localhost:8000
```

---

## Scenario 1: EYWA baseline ingest

### Action

Upload:

```text
docs/test_sources/01_eywa_baseline_source.md
```

Project:

```text
eywa-demo
```

### Expected observations

The engine should create several EYWA pages. Exact slugs may differ, but the generated wiki should clearly cover:

- architecture overview;
- MVP scope;
- voice interface;
- concierge module;
- FAQ assistant;
- deployment / customer-controlled infrastructure;
- security or personal data handling.

Expected page metadata:

- project is `eywa-demo`;
- confidence is reasonably high for directly stated facts;
- source references point to the baseline document;
- page types are meaningful, for example `entity` or `concept`.

Expected links:

- voice page should link to architecture or MVP scope when possible;
- concierge page should link to MVP scope or building management topic;
- FAQ page should link to knowledge base or source/citation topic;
- deployment page should link to data control/security if those pages exist.

### Expected conflicts

No conflicts should be created from the first baseline document alone.

### What must not happen

- Do not create pages under `_general` unless the content is truly shared/general.
- Do not create pages under `aurora-demo`.
- Do not create conflicts from a single source without existing contradictory content.
- Do not invent features such as marketplace, payments, biometric access, or full engineering integration as MVP features.

### Query checks

Ask:

```text
Какие технологии используются в архитектуре EYWA MVP?
```

Expected:

- answer in Russian;
- mentions FastAPI, React, PostgreSQL, pgvector, Docker Compose if captured;
- cites EYWA pages with `[[slug]]`.

Ask:

```text
Какие языки нужны в EYWA с первого дня?
```

Expected:

- Russian, English, Arabic;
- citation to an EYWA page.

Ask:

```text
Что не входит в первый MVP EYWA?
```

Expected:

- marketplace ordering;
- payment processing;
- biometric access control;
- deep engineering integrations;
- citation to EYWA MVP/scope page.

---

## Scenario 2: EYWA revised conflicting source

### Action

Upload:

```text
docs/test_sources/02_eywa_conflict_source.md
```

Project:

```text
eywa-demo
```

### Expected observations

The engine should identify that the revised source describes the same EYWA project but changes earlier assumptions.

It may create new pages, draft updates, or update pages depending on the current implementation. If safe update/diff workflow exists, existing pages should not be silently overwritten.

### Expected conflicts

The engine should detect conflicts or version mismatches in these areas:

1. Platform scope
   - baseline: mobile and web;
   - revised: web-only pilot.

2. Language scope
   - baseline: Russian, English, Arabic;
   - revised: Russian and English only.

3. Deployment model
   - baseline: customer-controlled / on-premise preferred for MVP;
   - revised: vendor-managed cloud allowed for first three months of pilot.

4. Database technology
   - baseline: PostgreSQL and pgvector;
   - revised: SQLite for pilot, PostgreSQL later.

5. Voice scope
   - baseline: basic voice interaction in MVP;
   - revised: voice moved to phase two.

6. Concierge request fields
   - baseline: category, description, apartment number, contact phone, priority, status;
   - revised: category, resident comment, apartment number, status.

### Expected conflict quality

Each conflict should ideally include:

- conflict id;
- status `OPEN`;
- project `eywa-demo`;
- source file reference to the revised document;
- existing wiki page or old claim;
- new source claim;
- suggested resolution options.

### What must not happen

- Do not silently delete baseline facts without a conflict, draft, or visible update trail.
- Do not merge old and new facts into one confusing final statement.
- Do not claim both SQLite and PostgreSQL are simultaneously the same pilot database without context.
- Do not treat the revised document as a separate unrelated project.
- Do not store this under `aurora-demo` or `_general`.

### Query checks

Ask:

```text
Входит ли мобильное приложение в EYWA MVP?
```

Expected:

- answer should mention disagreement or version difference;
- baseline says mobile and web;
- revised says web-only pilot;
- answer should not pretend there is a single uncontested fact.

Ask:

```text
Какие языки требуются для запуска EYWA?
```

Expected:

- answer should mention conflict between baseline and revised source;
- old scope: RU/EN/AR;
- revised scope: RU/EN only.

Ask:

```text
EYWA использует PostgreSQL или SQLite?
```

Expected:

- answer should distinguish baseline architecture from revised pilot requirement;
- if conflicts are visible, mention conflict or uncertainty.

### Conflict resolution test

Resolve one conflict with this rule:

```text
For EYWA MVP scope, the revised requirements supersede the baseline for pilot-stage platform, language, voice, database, and deployment decisions. The baseline remains useful for later production target architecture.
```

Expected after resolution:

- selected conflict becomes resolved;
- resolution/comment is visible;
- if skill extraction exists, a reusable rule is created or previewed;
- future answers should distinguish pilot MVP from production target.

---

## Scenario 3: AURORA cross-project source

### Action

Upload:

```text
docs/test_sources/03_aurora_cross_project_source.md
```

Project:

```text
aurora-demo
```

### Expected observations

The engine should create pages under `aurora-demo`.

Expected topics:

- AURORA overview;
- MVP scope;
- logistics operations users;
- role-based access;
- customer-controlled infrastructure;
- PostgreSQL usage;
- no operational system changes;
- no voice in first MVP.

### Expected cross-project behavior

The engine may notice that EYWA and AURORA both discuss:

- FastAPI;
- web MVP;
- deployment model;
- voice scope;
- knowledge base answers;
- project limitations.

These are cross-project similarities or differences, not automatic contradictions.

### What must not happen

- Do not overwrite EYWA pages with AURORA facts.
- Do not create EYWA conflicts just because AURORA requirements differ.
- Do not claim AURORA is a residential assistant.
- Do not claim AURORA has concierge requests.
- Do not claim EYWA is a logistics assistant.
- Do not mix Russian-only AURORA scope with EYWA language requirements.

### Acceptable behavior

Acceptable outcomes include:

- pages in `aurora-demo`;
- cross-links to shared concepts if implemented;
- comparison answer across EYWA and AURORA;
- optional `cross_project_difference` only if the system clearly distinguishes it from a direct contradiction.

### Query checks

Ask:

```text
Что такое AURORA и кто её использует?
```

Expected:

- internal AI assistant for logistics company;
- users: operations managers, dispatchers, analysts;
- cite AURORA pages.

Ask:

```text
Сравни MVP EYWA и AURORA.
```

Expected:

- separate sections for EYWA and AURORA;
- EYWA answer may mention unresolved conflict or revised scope;
- AURORA answer should mention logistics operations, Russian-only, PostgreSQL, no voice;
- citations from both projects.

Ask:

```text
В каком проекте есть concierge requests?
```

Expected:

- EYWA has concierge module;
- AURORA does not;
- cite both projects if possible.

Ask:

```text
Какой проект требует customer-controlled infrastructure с первого пилота?
```

Expected:

- AURORA clearly requires it;
- EYWA has baseline/revised difference and may allow vendor-managed cloud for pilot in revised source;
- answer should preserve that nuance.

---

## Search and project filter checks

### Global search

Search:

```text
FastAPI
```

Expected:

- results from both `eywa-demo` and `aurora-demo`.

### EYWA-only search

Search `FastAPI` with project filter:

```text
eywa-demo
```

Expected:

- only EYWA pages.

### AURORA-only search

Search `FastAPI` with project filter:

```text
aurora-demo
```

Expected:

- only AURORA pages.

### Multi-project search

If multi-project filter exists, select:

```text
eywa-demo, aurora-demo
```

Expected:

- results from both selected projects;
- no unrelated projects.

---

## Rebuild checks

After all three files are uploaded, run rebuild from raw sources.

Expected:

- all three raw files are processed;
- `eywa-demo` pages are rebuilt from first two documents;
- `aurora-demo` pages are rebuilt from the third document;
- conflicts between EYWA baseline and revised source are recreated or preserved according to current rebuild policy;
- AURORA differences are not treated as EYWA direct contradictions.

What must not happen:

- rebuild processes only markdown if other supported raw formats exist in raw folder;
- rebuild moves AURORA facts into EYWA pages;
- rebuild loses project assignment.

---

## Pass / fail summary

### Pass if

- documents upload to selected projects;
- baseline creates EYWA pages without conflicts;
- revised EYWA source creates conflicts or clear version differences;
- AURORA creates separate project pages;
- comparison queries keep projects separate;
- search filters by project;
- answers cite wiki pages;
- unresolved conflicts are visible or reflected in answers.

### Fail if

- UI rejects allowed source files;
- files go to wrong project;
- conflicts are not detected for the revised EYWA document;
- AURORA facts overwrite EYWA facts;
- answer presents contradictory EYWA facts as one final truth;
- search ignores project filters;
- rebuild loses project separation;
- conversion errors are reported as missing files.

---

## Minimal manual checklist

- [ ] Upload baseline EYWA into `eywa-demo`.
- [ ] Confirm pages created under `eywa-demo`.
- [ ] Confirm no conflicts after first file.
- [ ] Upload revised EYWA into `eywa-demo`.
- [ ] Confirm conflicts or visible version differences.
- [ ] Ask language, mobile, voice, database questions.
- [ ] Upload AURORA into `aurora-demo`.
- [ ] Confirm pages created under `aurora-demo`.
- [ ] Ask comparison question.
- [ ] Search globally for `FastAPI`.
- [ ] Search only in `eywa-demo`.
- [ ] Search only in `aurora-demo`.
- [ ] Run rebuild.
- [ ] Confirm project separation survives rebuild.

---

## Notes for coding agent

If behavior differs from this guide, do not immediately change the guide to match the code. First decide whether the code is wrong. A test expectation document is supposed to be annoying; that is how it earns its rent.
