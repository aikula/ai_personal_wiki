# Coding Agent Plan: Review Fixes and Implementation Tasks

## Purpose

This document converts the latest repository review into an implementation plan for the coding agent.

Work directly in `main`. Do not create pull requests.

The goal is to make the current MVP safer and more demo-ready without overengineering the system.

---

## Decisions from product owner

### Do now

1. Fix remaining ingest and batch issues.
2. Fix project attribution on ingest/conversion errors.
3. Add visible baseline tests.
4. Replace CDN React/Babel runtime dependencies with local/vendor assets or a minimal bundled frontend approach.
5. Improve conflict resolution update flow so it is reviewable and not a hidden overwrite.
6. Continue implementation of claim-level provenance/conflict layer, but with strict scope.
7. Update documentation where implementation changes user behavior.

### Keep as technical debt for now

1. LLM context truncation is too rough, but leave it as-is for now.
2. CORS still uses `*` during local testing. Do not change it in this task.
3. Production hardening remains out of scope.
4. Full real sandbox for Python execution remains out of scope.

### Do not do now

1. Do not redesign the entire architecture.
2. Do not split `wiki_fs.py` in this iteration unless a small extraction is required for tests.
3. Do not add vector search.
4. Do not add a large frontend framework migration if a smaller reliable fix is enough.
5. Do not change the product model from file-based wiki to database-backed wiki.

---

## Priority overview

### P0 before demo

- Batch ingest must not silently ignore files.
- Ingest conversion errors must preserve the selected project.
- Baseline tests must exist and pass.
- Conflict resolution page update must be reviewable.
- Frontend must not depend on public CDN at runtime for core React/Babel assets.

### P1 after P0

- Claim-level provenance/conflict layer minimal implementation.
- Documentation update.
- Demo smoke script update.

### Technical debt only

- Smarter LLM context packing/truncation.
- Production CORS policy.
- Real sandboxing.
- Production Docker profile.

---

# 1. Fix batch ingest

## Problem

`POST /api/ingest/batch` currently processes only multipart items with key `files`. If a client sends repeated `file` fields or sends a malformed batch, the endpoint can return an empty success-looking response instead of a clear error.

This is a demo-killer because the UI/API may look fine while doing nothing. The most annoying kind of bug, naturally.

## Required behavior

Endpoint:

```text
POST /api/ingest/batch
```

Supported multipart fields:

- official: `files`
- compatibility alias: repeated `file`

Supported extensions:

- `.md`
- `.txt`
- `.py`
- `.docx`
- `.pptx`
- `.pdf`

## Required response shape

Return clear aggregate counters:

```json
{
  "total": 3,
  "processed": 2,
  "skipped": 1,
  "successes": 2,
  "failures": 0,
  "details": [],
  "skipped_details": []
}
```

Each detail should include at least:

- filename;
- source_file / raw path;
- project;
- success;
- pages_created;
- pages_updated;
- pages_superseded;
- conflict_ids;
- analysis_notes;
- error.

## Edge cases

- Empty batch: return HTTP 400.
- Unsupported extension: do not fail the whole batch; add to `skipped_details`.
- Invalid project: return HTTP 400 before processing.
- One file fails conversion: record failure for that file, keep processing others.
- Unchanged file: report success with `analysis_notes="Source unchanged, skipped"`.

## Checklist

- [ ] Batch accepts repeated `files`.
- [ ] Batch accepts repeated `file` as alias.
- [ ] Empty batch returns HTTP 400.
- [ ] Unsupported files appear in `skipped_details`.
- [ ] Supported files are never silently ignored.
- [ ] Mixed batch test covers markdown + PDF/DOCX/PPTX mocked or fixture-based path.

## Acceptance criteria

- `curl -F "project=eywa-demo" -F "files=@a.md" -F "files=@b.docx" /api/ingest/batch` works.
- `curl -F "project=eywa-demo" -F "file=@a.md" -F "file=@b.docx" /api/ingest/batch` works or returns a documented compatibility error. Prefer working.
- Sending no valid file fields returns HTTP 400, not `{total: 0}`.

---

# 2. Fix project attribution on ingest errors

## Problem

If document conversion fails before `project` is computed from the raw path, the ingest result may report project `_general` even when the file belongs to another project.

## Required behavior

Project must be inferred before reading/converting the raw source.

Recommended helper:

```python
def infer_project_from_raw_relative_path(raw_relative_path: str) -> str:
    normalized = raw_relative_path.replace("\\", "/").strip("/")
    if "/" not in normalized:
        return "_general"
    return normalized.split("/", 1)[0] or "_general"
```

Use it in all early error branches.

## Checklist

- [ ] RawSourceError result preserves project.
- [ ] Missing source result preserves project.
- [ ] Log message includes raw path and inferred project.
- [ ] Test covers failed conversion for `eywa-demo/bad.pdf`.

## Acceptance criteria

If `eywa-demo/bad.pdf` fails conversion, API returns:

```json
{
  "project": "eywa-demo",
  "source_file": "eywa-demo/bad.pdf",
  "success": false
}
```

---

# 3. Add visible baseline tests

## Problem

The repository declares pytest tooling, but the visible baseline test suite is still missing or insufficient. This must be fixed before further refactoring.

## Required test files

Create or complete:

```text
tests/test_raw_sources.py
tests/test_ingest_upload.py
tests/test_batch_ingest.py
tests/test_project_handling.py
tests/test_conflicts.py
tests/test_linter.py
tests/test_ui_static.py
```

Do not call real LLM endpoints. Use fake LLM clients and temporary wiki-data directories.

## Minimum coverage

### Raw sources

- [ ] Binary save preserves bytes.
- [ ] SHA256 is computed from bytes.
- [ ] Duplicate content is detected.
- [ ] Changed same path is detected.
- [ ] Raw listing includes all supported extensions.
- [ ] Unsupported extensions are excluded.
- [ ] Missing raw source returns `None`.
- [ ] Conversion failure raises a clear RawSourceError.

### Ingest/upload

- [ ] Single upload saves raw bytes under selected project.
- [ ] Unsupported extension returns HTTP 400.
- [ ] Unchanged file skips LLM ingest.
- [ ] Conversion error preserves project.

### Batch

- [ ] Batch with `files` processes all files.
- [ ] Batch with repeated `file` alias works.
- [ ] Empty batch returns HTTP 400.
- [ ] Unsupported file appears in skipped details.

### Project handling

- [ ] `_general` always exists.
- [ ] Valid project names are accepted.
- [ ] Invalid project names are rejected.
- [ ] Project listing includes raw-only projects.
- [ ] Search supports one or multiple projects.

### Conflicts

- [ ] Conflict parser separates OPEN and RESOLVED.
- [ ] Cross-project difference is marked and not counted as actionable conflict if that logic exists.
- [ ] Resolution creates or prepares a reviewable draft/update.
- [ ] Applying update does not call LLM again if a prepared candidate exists.
- [ ] Rejecting draft keeps page unchanged.

### UI static

- [ ] UI does not reference CDN React/Babel scripts after CDN fix.
- [ ] Upload UI contains `.docx`, `.pptx`, `.pdf`.
- [ ] Markdown-only upload text is absent.
- [ ] Project selector markers exist.
- [ ] Conflict update/draft UI markers exist if implemented.

## Acceptance criteria

- `python -m pytest -v` passes on Windows Python 3.12.
- Tests do not require real LLM credentials.
- At least 25 meaningful tests exist.

---

# 4. Replace CDN React/Babel runtime dependency

## Problem

The UI currently loads React, ReactDOM and Babel from public CDN. This is fragile for local/offline/on-prem use and bad for demos in restricted networks.

## Goal

The app must be able to start and render UI without internet access.

## Acceptable implementation options

### Option A: Vendor static browser assets

Store local copies under:

```text
app/ui/vendor/react.production.min.js
app/ui/vendor/react-dom.production.min.js
app/ui/vendor/babel.min.js
```

Then update `index.html`:

```html
<script src="/vendor/react.production.min.js"></script>
<script src="/vendor/react-dom.production.min.js"></script>
<script src="/vendor/babel.min.js"></script>
```

Mount `/vendor` in FastAPI if needed.

### Option B: Minimal frontend build

Introduce a small build pipeline and commit built assets, but do not overbuild.

Only choose this if it remains simple and documented.

## Important constraints

- Do not break the current single-page app behavior.
- Do not require Node.js for ordinary Python startup unless documented and justified.
- Prefer local vendored assets for fastest demo readiness.
- Do not commit font files unless they are already project-owned or legally safe.

## Checklist

- [ ] No `https://unpkg.com/...` script tags remain in runtime UI.
- [ ] UI works without internet.
- [ ] FastAPI serves local vendor assets.
- [ ] README documents any frontend asset/build requirement.
- [ ] Static UI test verifies no CDN dependency.

## Acceptance criteria

With network disabled, `python -m uvicorn app.api.main:app --reload --port 8000` opens the UI successfully.

---

# 5. Make conflict resolution updates genuinely reviewable

## Problem

Current conflict update flow risks applying LLM-generated changes directly. The desired behavior is draft/diff first, apply only after review.

## Required behavior

Conflict resolution flow:

1. User resolves a conflict.
2. System records resolution and optional skill.
3. System prepares an update draft for affected wiki page.
4. Draft contains:
   - affected slug;
   - old content;
   - proposed new content;
   - unified diff;
   - semantic summary;
   - resolution text;
   - source context.
5. UI/API lets user apply or reject the draft.
6. Applying draft writes the already prepared candidate content.
7. Applying draft rebuilds index and metrics.
8. Rejecting draft leaves wiki page unchanged.

## Do not do

- Do not call LLM again inside `apply-update` to generate fresh content.
- Do not overwrite page without a stored candidate.
- Do not delete the draft before successful write.
- Do not lose frontmatter.

## Suggested backend design

Prepare endpoint:

```text
POST /api/conflicts/{id}/prepare-update
```

Should create:

```text
wiki-data/drafts/conflict-CONFLICT-001/meta.json
wiki-data/drafts/conflict-CONFLICT-001/old.md
wiki-data/drafts/conflict-CONFLICT-001/new.md
wiki-data/drafts/conflict-CONFLICT-001/diff.patch
```

Apply endpoint:

```text
POST /api/conflicts/{id}/apply-update
```

Should read `new.md` and apply it. No LLM call here.

Reject endpoint:

```text
POST /api/conflicts/{id}/reject-update
```

Should archive/delete draft without changing page.

## Checklist

- [ ] `prepare-update` creates candidate content and diff.
- [ ] `apply-update` applies stored candidate, not fresh LLM output.
- [ ] `reject-update` exists.
- [ ] Existing page frontmatter is preserved unless deliberately updated.
- [ ] Index is rebuilt after apply.
- [ ] Tests cover prepare/apply/reject.

## Acceptance criteria

A resolved conflict can be converted into a visible draft, reviewed, applied, and verified in the page content. Applying the draft must not call the LLM.

---

# 6. Implement minimal claim-level provenance layer

## Decision

Proceed with claim-level implementation, but keep the first version deliberately narrow.

The goal is not to build a perfect knowledge graph. The goal is to stop confusing direct contradictions, version changes and cross-project differences.

## Minimal scope

### Claim model

A claim should represent one factual unit:

```text
claim_id
project
source_path
source_sha256
quote
normalized
related_slugs
confidence
status
created
last_seen
contradicted_by
superseded_by
```

Allowed statuses:

```text
active
superseded
contradicted
unresolved
ignored
```

### Claim extraction

Step 1 already asks the LLM for claims. Ensure these claims are actually persisted.

Persist claims under:

```text
wiki-data/wiki/_claims/<project>/<source_id>/<chunk_or_source_id>/<claim_id>.md
```

or equivalent existing convention.

### Claim comparison rules

Only compare claims as direct contradictions when:

- same project or explicitly same entity scope;
- same subject/topic;
- same property/attribute;
- incompatible values;
- neither claim is clearly scoped to different phase/version/environment.

Do not treat as direct conflict when:

- claims belong to different projects;
- one claim is pilot scope and the other is production target;
- one claim is historical/baseline and another is revised/superseding;
- one claim is a general description and another is a specific implementation detail.

### Conflict generation

If contradiction is found, create conflict with:

- `conflict_type`: `factual_contradiction`, `version_mismatch`, or `cross_project_difference`;
- `is_cross_project` boolean;
- related claim IDs;
- source quotes;
- affected page slug;
- suggested resolution options in configured language.

## Required behavior on conflict resolution

When user resolves a conflict:

- update affected claim statuses;
- mark contradicted/superseded claims correctly;
- keep original quotes and source refs;
- do not delete claims;
- create/update skill if requested;
- prepare page update draft if affected page content should change.

## Checklist

- [ ] Claims from Step 1 are persisted.
- [ ] Claims include source SHA and quote.
- [ ] Claims link to related wiki page slugs.
- [ ] Claim statuses can be updated.
- [ ] Same-project contradictions can reference claim IDs.
- [ ] Cross-project differences are not treated as urgent conflicts.
- [ ] Conflict resolution updates claim statuses.
- [ ] Linter checks orphan/contradicted claims.
- [ ] Tests cover claim persistence and status transitions.

## Acceptance criteria

Using the controlled test sources:

1. EYWA baseline creates active claims.
2. EYWA revised source creates version mismatch or contradiction claims against baseline.
3. AURORA creates separate claims under `aurora-demo`.
4. AURORA differences do not overwrite or contradict EYWA claims by default.
5. Resolving EYWA conflict updates related claim statuses.

---

# 7. Keep rough LLM context truncation as tech debt

## Decision

Do not implement smarter context packing in this iteration.

## Required action

Add or update technical debt documentation:

```text
LLM prompt/context truncation is currently coarse. It estimates tokens roughly and may truncate large prompts without semantic section prioritization. Future work: structured prompt packing that preserves schema, language rules, final instructions, and high-priority source sections.
```

## Checklist

- [ ] Tech debt note exists in issue or docs.
- [ ] No new implementation work is done for this item.

## Acceptance criteria

No code complexity added for context packing in this iteration.

---

# 8. Leave CORS unchanged for now

## Decision

CORS remains permissive during local testing.

Do not modify CORS in this task.

## Required action

Add/keep technical debt note:

```text
CORS is permissive for local testing. Restrict origins before personal_server or multi_user public deployment.
```

## Checklist

- [ ] No CORS behavior change in this iteration.
- [ ] Tech debt note exists.

---

# 9. Documentation updates

Update documentation after implementation.

## Required docs

- README.ru.md
- README.en.md if behavior changed
- docs/demo_script.md if present
- docs/demo_readiness_tasks.md or successor doc if still relevant

## Required content

- Local UI no longer depends on external CDN.
- Batch endpoint behavior is documented.
- Conflict resolution draft/diff flow is documented.
- Claim-level layer is described as early/minimal if implemented.
- Known limitations remain honest:
  - local/trusted usage;
  - CORS still permissive in dev;
  - context truncation still coarse;
  - production hardening not complete.

## Checklist

- [ ] README does not overpromise production readiness.
- [ ] Windows startup remains first-class.
- [ ] Demo steps reflect actual UI/API.
- [ ] Known limitations are visible.

---

# 10. Final manual smoke test

Run this manually after implementation.

## Environment

Windows Python 3.12 preferred:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
pip install -e .[dev]
python -m pytest -v
python -m uvicorn app.api.main:app --reload --port 8000
```

## UI smoke

- [ ] UI opens without internet access.
- [ ] Upload modal works.
- [ ] Project selector works.
- [ ] DOCX/PDF/PPTX are accepted in UI.
- [ ] Query works with wiki citations.
- [ ] Conflict panel opens.
- [ ] Conflict prepare-update creates visible draft/diff.
- [ ] Apply draft updates page.
- [ ] Reject draft leaves page unchanged.

## Test sources smoke

Use:

```text
docs/test_sources/01_eywa_baseline_source.md
docs/test_sources/02_eywa_conflict_source.md
docs/test_sources/03_aurora_cross_project_source.md
```

Expected:

- baseline EYWA creates pages without conflicts;
- revised EYWA creates conflicts/version mismatches;
- AURORA creates separate project pages;
- AURORA differences are not urgent EYWA conflicts;
- resolving EYWA conflict updates claims and prepares page update draft.

---

# Final acceptance checklist

## P0

- [ ] Batch ingest fixed.
- [ ] Project attribution on conversion error fixed.
- [ ] Visible tests added and passing.
- [ ] CDN runtime dependency removed.
- [ ] Conflict update flow is draft/diff/apply, not hidden overwrite.

## P1

- [ ] Minimal claim-level persistence implemented.
- [ ] Claim status transitions implemented.
- [ ] Claims participate in conflict metadata.
- [ ] Documentation updated.

## Explicitly not done

- [ ] Smarter LLM context packing is not implemented and is documented as tech debt.
- [ ] CORS remains unchanged and is documented as dev/testing limitation.
- [ ] Production sandbox is not implemented.

---

## Suggested commit sequence

1. Fix batch ingest behavior.
2. Fix project attribution on ingest errors.
3. Add baseline tests for raw sources, ingest, batch, projects.
4. Remove frontend CDN runtime dependency.
5. Make conflict update flow draft/diff/apply.
6. Implement minimal claim persistence and status transitions.
7. Add conflict/claim/linter tests.
8. Update documentation and demo notes.

Commit directly to `main`. Keep commits small and boring. Exciting commits are how repositories become crime scenes.
