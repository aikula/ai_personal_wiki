# Demo Readiness Tasks for Coding Agent

## Goal

Prepare the repository for the first public MVP demonstration. Commit changes directly to `main`. Do not create a pull request.

Primary user path: Windows, Python 3.12, native virtual environment. Docker remains optional.

Supported source formats for this release:

- `.md`
- `.txt`
- `.py`
- `.docx`
- `.pptx`
- `.pdf`

---

## 1. Batch processing

Fix the batch endpoint so it processes all supported source formats and returns clear per-file results.

Checklist:

- [ ] Accept all supported extensions.
- [ ] Use one documented multipart field for multiple files.
- [ ] Optionally support the single-file field name as a compatibility alias.
- [ ] Do not silently skip valid files.
- [ ] Return status for every file: success, source state, created pages, updated pages, conflicts, error.
- [ ] Return clear skipped reasons for invalid files.
- [ ] Add tests for mixed-format batch processing.

Acceptance criteria:

- A mixed batch with markdown, docx, pptx and pdf produces a clear per-file report.
- Unsupported files are visible in skipped results, not silently ignored.

---

## 2. Project handling

Project selection must be consistent across source saving, listing, ingest, search and UI.

Checklist:

- [ ] Add project listing endpoint, for example `GET /api/projects` or `GET /api/wiki/projects`.
- [ ] Always include `_general`.
- [ ] Build project list from raw source folders and wiki metadata.
- [ ] Validate project names consistently.
- [ ] Trim whitespace before validation.
- [ ] Preserve the correct project in error results.
- [ ] Ensure source files are saved under the selected project folder.
- [ ] Add tests for project selection and invalid project names.

Acceptance criteria:

- A document assigned to project `demo` is stored under `wiki-data/raw/demo/` and appears in project-filtered listing.

---

## 3. Frontend source selection

The frontend currently still behaves as if only markdown is allowed. Fix it.

Checklist:

- [ ] Replace markdown-only filtering in `app/ui/index.html`.
- [ ] Allow `.md`, `.txt`, `.py`, `.docx`, `.pptx`, `.pdf`.
- [ ] Update file picker `accept` value.
- [ ] Update drag-and-drop validation.
- [ ] Update visible UI text and error messages.
- [ ] Show file extension/type in selected source list.
- [ ] Add a static UI test to prevent markdown-only regression.

Acceptance criteria:

- User can select and drag DOCX, PPTX and PDF files from the UI.

---

## 4. Project selector UI

Add a normal project selector instead of free-text-only project input.

Upload modal:

- [ ] Existing projects shown as dropdown options.
- [ ] `_general` selected by default.
- [ ] New project can be created from the modal.
- [ ] Invalid project name is rejected before request.
- [ ] Created project is immediately selectable.

Search UI:

- [ ] Existing projects shown as selectable options.
- [ ] `All projects` option exists.
- [ ] Multiple project selection is supported.
- [ ] Selected projects are visible as chips or badges.
- [ ] Search results show project badge.

Acceptance criteria:

- User can upload to an existing project without typing its name.
- User can create a new project during upload.
- User can search across selected projects.

---

## 5. Visible baseline tests

Add a visible and runnable baseline test suite. Tests must not call a real LLM.

Required test files:

- `tests/test_raw_sources.py`
- `tests/test_ingest_upload.py`
- `tests/test_project_handling.py`
- `tests/test_ui_static.py`

Coverage checklist:

- [ ] Binary source save preserves bytes.
- [ ] SHA256 is computed from bytes.
- [ ] Duplicate and unchanged sources are detected.
- [ ] Raw listing includes all supported formats.
- [ ] Missing source returns `None`.
- [ ] Conversion failure returns a clear error.
- [ ] Single source upload stores bytes.
- [ ] Batch processing returns per-file status.
- [ ] Project validation works.
- [ ] Project listing includes `_general` and created projects.
- [ ] UI static test checks DOCX, PPTX and PDF support.
- [ ] UI static test checks that markdown-only accept text is gone.

Acceptance criteria:

- `python -m pytest -v` passes locally.
- At least 15 meaningful tests exist.
- No test requires live LLM credentials.

---

## 6. README update

After code changes, update README.

Checklist:

- [ ] Remove markdown-only positioning.
- [ ] List all supported source formats.
- [ ] Explain that raw source files are stored unchanged.
- [ ] Put Windows Python 3.12 native setup first.
- [ ] Keep Docker as optional.
- [ ] Document document-conversion dependency.
- [ ] Update endpoint descriptions for single and batch processing.
- [ ] Add known limitations: local/trusted use, no production auth, conversion quality depends on source file, LLM required.

Acceptance criteria:

- A new Windows user can start the app using README.
- README matches actual behavior.

---

## 7. Cleanup

Remove repository trash and stale references.

Checklist:

- [ ] Delete empty `app/api/routes/sources_api.py` if still present.
- [ ] Remove all `mrkitdown` references.
- [ ] Remove stale markdown-only UI text.
- [ ] Remove accidental placeholder routes/files.
- [ ] Keep old compatibility methods only if they delegate to current helpers.

Acceptance criteria:

- Search for `mrkitdown` returns nothing.
- Search for markdown-only upload text returns nothing relevant.
- No empty placeholder source files remain.

---

## 8. Docker and Windows path

Windows native launch is the main path. Docker must be clean but secondary.

Checklist:

- [ ] README states Windows native venv is recommended for Windows users.
- [ ] Dockerfile does not pretend to be production if it is dev-oriented.
- [ ] Prefer no reload in default Docker command.
- [ ] Move reload behavior to dev override if needed.
- [ ] Avoid installing dev dependencies in main Docker image unless necessary.
- [ ] Verify Docker build still works after dependency changes.

Acceptance criteria:

- Windows user can run without Docker.
- Docker remains usable for optional local/server testing.

---

## 9. Extra demo polish

Add what is missing for a confident demo.

Checklist:

- [ ] Add `docs/demo_script.md`.
- [ ] Demo script includes setup, happy path, sample source suggestions and fallback plan.
- [ ] Upload and rebuild show visible progress/status.
- [ ] Conversion errors are visible and not mislabeled as missing files.
- [ ] UI warns when LLM settings are missing.
- [ ] Repository contains no `.env`, private raw documents or secrets.

Acceptance criteria:

- Demo can be run from a prepared script without improvising under pressure.

---

## Final acceptance checklist

Backend:

- [ ] Batch mode fixed.
- [ ] Project handling consistent.
- [ ] Source bytes preserved.
- [ ] Rebuild includes all supported formats.
- [ ] Errors are clear.

Frontend:

- [ ] All allowed extensions selectable.
- [ ] Project dropdown exists.
- [ ] New project creation exists.
- [ ] Multi-project search exists.
- [ ] Heavy operations show status.

Tests:

- [ ] Visible tests added.
- [ ] Tests pass on Windows Python 3.12.
- [ ] Tests do not require a real LLM.

Docs:

- [ ] README updated.
- [ ] Docker docs updated.
- [ ] Demo script added.
- [ ] Known limitations documented.

Cleanup:

- [ ] Placeholder files removed.
- [ ] Stale converter references removed.
- [ ] Markdown-only UI references removed.

---

## Suggested commit sequence

1. Fix batch processing.
2. Add project listing and normalize project behavior.
3. Update frontend source selection.
4. Add project selectors for upload and search.
5. Add baseline tests.
6. Remove placeholder files and stale references.
7. Update Docker and Windows-first docs.
8. Update README.
9. Add demo script.

Commit directly to `main`. No PR.
