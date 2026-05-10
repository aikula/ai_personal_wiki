# Coding Agent Plan: DOCX/PPTX/PDF Ingest, Rebuild and Testing

## Goal

Make the project reliably accept, store, convert, ingest, rebuild and test raw source files in these formats:

- `.md`
- `.txt`
- `.py`
- `.docx`
- `.pptx`
- `.pdf`

The current codebase has partial support. The backend has started moving toward binary-safe source handling, but the implementation must be finished, verified, cleaned up and covered with tests.

This is not a cosmetic task. The product currently promises more than it consistently does. Fix that before adding another shiny abstraction that will later demand therapy.

---

## Current Known State

Recent changes already added:

- `app/core/raw_sources.py`
  - binary-safe raw source saving;
  - bytes-based SHA256;
  - allowed extension constants;
  - MarkItDown-based document conversion;
  - raw source listing for all supported extensions.

- `pyproject.toml`
  - `markitdown[docx,pdf,pptx]>=0.1.5` dependency.

- `app/agents/ingest_agent.py`
  - switched to `read_raw_source_file(...)`;
  - rebuild switched to `list_raw_source_files(...)`;
  - `_find_related_pages()` returns up to 10 pages.

- `app/api/routes/ingest.py`
  - intended to be binary-safe through `request.form()` and raw-source helpers.

Known remaining risks:

- `app/ui/index.html` may still filter uploads to `.md` only.
- `app/core/wiki_fs.py` still contains old `mrkitdown` import and old text-only raw methods. These may not be used by the ingest agent anymore, but they are misleading and may be called elsewhere.
- `app/api/routes/sources_api.py` is an accidental placeholder and should be deleted if still present.
- There may be no tests covering binary upload, conversion, source manifest, rebuild discovery, or UI upload filtering.
- MarkItDown conversion behavior must be verified on Windows + Python 3.12.

---

## Non-Negotiable Product Behavior

### Upload

The UI must allow users to select/drop:

```text
.md, .txt, .py, .docx, .pptx, .pdf
```

Single upload endpoint must:

1. accept all supported extensions;
2. read uploaded files as bytes;
3. save files byte-for-byte under `wiki-data/raw/<project>/<filename>`;
4. hash bytes, not decoded strings;
5. skip unchanged files;
6. warn/log duplicate files;
7. pass the relative raw path to `IngestAgent.run(...)`.

Batch upload endpoint must do the same for multiple files.

### Ingest

`IngestAgent.run(raw_relative_path)` must:

1. read text raw files as UTF-8;
2. convert `.docx`, `.pptx`, `.pdf` through MarkItDown;
3. return a clear error if conversion dependency is missing;
4. return a clear error if conversion fails;
5. never misreport conversion failure as `Source file not found`.

### Rebuild

Rebuild must process all supported raw formats, not only markdown.

### UI

The upload modal must not say “only `.md`”. That was true yesterday. Today it is just lying with extra steps.

---

## Implementation Tasks

## 1. Verify and finalize `app/core/raw_sources.py`

### Required constants

Ensure the module defines exactly one shared source of truth:

```python
RAW_ALLOWED_EXTENSIONS = {".md", ".txt", ".py", ".pdf", ".docx", ".pptx"}
TEXT_RAW_EXTENSIONS = {".md", ".txt", ".py"}
DOCUMENT_RAW_EXTENSIONS = {".pdf", ".docx", ".pptx"}
```

### Required functions

Ensure these exist and work:

```python
sha256_bytes(content: bytes) -> str
check_source_state_bytes(state_dir: Path, relative_path: str, content: bytes) -> dict
update_source_manifest_bytes(state_dir: Path, relative_path: str, content: bytes) -> None
save_raw_file_bytes(raw_dir: Path, state_dir: Path, project: str, filename: str, content: bytes) -> Path
list_raw_source_files(raw_dir: Path, project: str | None = None) -> list[Path]
read_raw_source_file(raw_dir: Path, relative_path: str) -> str | None
```

### Required behavior

- `save_raw_file_bytes()` must use `write_bytes()`, never `write_text()`.
- `read_raw_source_file()` must return `None` only for missing files.
- Document conversion errors must raise `RawSourceError`.
- Missing MarkItDown must raise `RawSourceError` with clear install instruction.
- Path traversal must be blocked.
- Windows paths must not leak backslashes into raw relative paths.

---

## 2. Clean up legacy raw handling in `app/core/wiki_fs.py`

### Problem

`WikiFS` still contains older raw source logic:

- old `mrkitdown` import;
- `read_raw_file()` conversion path;
- `save_raw_file()` text-only path;
- `list_raw_files()` markdown-only or duplicate logic;
- text-based source manifest methods.

This creates confusion and increases the chance that future code accidentally calls the wrong path.

### Required cleanup

Preferred approach:

- keep `WikiFS.raw_dir`, `WikiFS.state_dir`, `WikiFS.get_raw_project()`;
- either remove old raw methods or make them thin wrappers over `raw_sources.py`;
- remove `mrkitdown` import entirely;
- remove any `MRKITDOWN_AVAILABLE` logic;
- ensure no code imports or calls `mrkitdown`.

Acceptable compatibility wrappers:

```python
def list_raw_files(self, project: str | None = None) -> list[Path]:
    return list_raw_source_files(self.raw_dir, project)


def read_raw_file(self, relative_path: str) -> str | None:
    return read_raw_source_file(self.raw_dir, relative_path)


def save_raw_file_bytes(self, project: str, filename: str, content: bytes) -> Path:
    return save_raw_file_bytes(self.raw_dir, self.state_dir, project, filename, content)
```

If old `save_raw_file(project, filename, content: str)` is kept, it must encode to UTF-8 and delegate to `save_raw_file_bytes(...)`.

---

## 3. Verify and finalize `app/api/routes/ingest.py`

### Required behavior

Single upload must:

- parse multipart form;
- validate project name;
- validate raw filename;
- read uploaded file bytes;
- check state via `check_source_state_bytes(...)`;
- skip unchanged files;
- save bytes via `save_raw_file_bytes(...)`;
- call `agent.run(raw_path)`.

Batch upload must:

- accept all supported extensions;
- not silently skip non-`.md` files;
- return structured `skipped` list with reasons;
- process files sequentially;
- include conversion/ingest errors per file.

Raw listing endpoint must:

- use `list_raw_source_files(...)`;
- return all supported raw files;
- include extension and size.

### Edge cases

- Empty filename -> HTTP 400.
- Unsupported extension -> HTTP 400.
- Invalid project name -> HTTP 400.
- Duplicate source -> save may still happen if same path changed, but response/log must expose duplicate state.
- Unchanged source -> skip LLM ingest.

---

## 4. Fix UI upload modal in `app/ui/index.html`

### Problem

The UI may still contain markdown-only filtering:

```js
const mds = [...fs].filter(f => f.name.endsWith('.md'));
```

and:

```html
accept=".md"
```

### Required UI change

Add one JS constant near the upload modal:

```js
const RAW_ALLOWED_EXTENSIONS = ['.md', '.txt', '.py', '.pdf', '.docx', '.pptx'];
```

Replace upload filtering with:

```js
const allowed = [...fs].filter(f =>
  RAW_ALLOWED_EXTENSIONS.some(ext => f.name.toLowerCase().endsWith(ext))
);

if (allowed.length < fs.length) {
  toast('Принимаются файлы: .md, .txt, .py, .pdf, .docx, .pptx', 'error');
}

setFiles(prev => [...prev, ...allowed]);
```

Replace file input accept with:

```html
accept=".md,.txt,.py,.pdf,.docx,.pptx"
```

Replace dropzone text with:

```text
Перетащите .md, .txt, .py, .pdf, .docx, .pptx файлы сюда или нажмите для выбора
```

### Acceptance criteria

- UI lets user choose DOCX/PPTX/PDF from file picker.
- Drag-and-drop accepts DOCX/PPTX/PDF.
- Unsupported files show a clear toast.

---

## 5. Remove accidental placeholder file

If present, delete:

```text
app/api/routes/sources_api.py
```

It currently contains only a placeholder and is not wired. Dead placeholder files are how repositories grow mold. Remove it unless it has been repurposed for real routes.

---

## 6. Add tests

Create or update `tests/`.

Tests must not call a real LLM.
Tests must not call external services.
Tests must use `tmp_path` for isolated wiki-data.

## 6.1 Unit tests for `raw_sources.py`

Create:

```text
tests/test_raw_sources.py
```

### Test: binary save preserves bytes

- save bytes containing invalid UTF-8, e.g. `b'\xff\x00docx-ish'`;
- assert file exists;
- assert `read_bytes()` equals original bytes.

### Test: SHA256 uses bytes

- hash same bytes twice -> same hash;
- hash different bytes -> different hash;
- ensure no `.decode()` is needed.

### Test: manifest detects unchanged

- call `update_source_manifest_bytes(...)`;
- call `check_source_state_bytes(...)` with same bytes;
- expect `status == 'unchanged'`.

### Test: manifest detects changed

- same path, different bytes;
- expect `status == 'changed'`.

### Test: manifest detects duplicate

- different path, same bytes;
- expect `status == 'duplicate'` and correct `duplicate_of`.

### Test: list raw source files includes all supported extensions

Create files:

```text
a.md
b.txt
c.py
d.docx
e.pptx
f.pdf
g.exe
```

Expect only supported six files.

### Test: text read works

- write UTF-8 `.md`;
- read through `read_raw_source_file(...)`;
- assert content.

### Test: missing file returns None

- call read on nonexistent path;
- expect `None`.

### Test: document conversion missing dependency is clear

Use monkeypatch to simulate `MARKITDOWN_AVAILABLE = False`; call read on `.docx`; expect `RawSourceError` mentioning `markitdown`.

---

## 6.2 API tests for upload behavior

Create:

```text
tests/test_ingest_upload.py
```

Use FastAPI TestClient if available.
Mock `IngestAgent.run(...)` or override dependency so tests do not call LLM.

### Test: upload DOCX stores bytes

- POST multipart to `/api/ingest` with `test.docx` and binary payload;
- assert response success;
- assert raw file bytes match original.

### Test: upload PDF stores bytes

Same for `.pdf`.

### Test: unsupported file rejected

- upload `.exe`;
- expect HTTP 400.

### Test: unchanged file skipped

- upload same `.docx` twice;
- second response should include `analysis_notes == 'Source unchanged, skipped'`.

### Test: batch accepts mixed supported formats

- send `.md`, `.docx`, `.pptx`, `.pdf`;
- expect all accepted, unless mocked agent intentionally returns failure.

---

## 6.3 Agent/rebuild tests

Create:

```text
tests/test_rebuild_raw_sources.py
```

### Test: rebuild file discovery includes documents

- create raw files with all supported extensions;
- monkeypatch `IngestAgent.run` to record paths and return fake success;
- call `rebuild_from_scratch()`;
- assert `.docx`, `.pptx`, `.pdf` are included.

### Test: conversion error appears as conversion error

- monkeypatch converter to raise;
- call `agent.run('project/file.docx')`;
- expect `success == False`;
- expect error contains conversion failure, not `Source file not found`.

---

## 6.4 UI smoke check

If no frontend test harness exists, do manual static validation:

- grep `index.html` for `accept=".md"`; it must not exist;
- grep for `Only .md` or `только .md`; must not exist;
- grep for `.docx`, `.pptx`, `.pdf`; must exist in upload modal.

Optional lightweight Python test:

```python
def test_upload_modal_mentions_supported_formats():
    html = Path('app/ui/index.html').read_text(encoding='utf-8')
    assert '.docx' in html
    assert '.pptx' in html
    assert '.pdf' in html
    assert 'accept=".md"' not in html
```

---

## 7. Windows + Python 3.12 verification

Run on Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
pip install -e .[dev]
python -m pytest -v
uvicorn app.api.main:app --reload
```

Manual browser/API tests:

1. Upload `.docx` from UI.
2. Upload `.pptx` from UI.
3. Upload `.pdf` from UI.
4. Check `/api/ingest/raw` contains all uploaded files.
5. Run rebuild from UI.
6. Confirm rebuild progress includes `.docx`, `.pptx`, `.pdf`.
7. Confirm no UTF-8 decode error appears.
8. Confirm conversion errors, if any, are explicit.

---

## 8. Manual API test commands

Use PowerShell:

```powershell
curl.exe -F "project=_general" -F "file=@sample.docx" http://localhost:8000/api/ingest
curl.exe -F "project=_general" -F "file=@sample.pdf"  http://localhost:8000/api/ingest
curl.exe -F "project=_general" -F "file=@sample.pptx" http://localhost:8000/api/ingest
curl.exe http://localhost:8000/api/ingest/raw
```

Rebuild:

```powershell
curl.exe -X POST http://localhost:8000/api/ingest/rebuild ^
  -H "Content-Type: application/json" ^
  -d "{\"confirm\":true}"
```

---

## 9. Acceptance Checklist

Backend:

- [ ] `pip install -e .[dev]` works on Windows/Python 3.12.
- [ ] `markitdown[docx,pdf,pptx]` is installed.
- [ ] No import of `mrkitdown` remains.
- [ ] Upload reads files as bytes.
- [ ] Raw files are saved byte-for-byte.
- [ ] SHA256 dedup works for binary files.
- [ ] Batch upload accepts non-markdown supported formats.
- [ ] Rebuild discovers all supported raw formats.
- [ ] Conversion failures are reported clearly.

UI:

- [ ] Upload picker accepts `.md,.txt,.py,.pdf,.docx,.pptx`.
- [ ] Drag-and-drop accepts the same formats.
- [ ] UI text no longer says only `.md`.

Tests:

- [ ] `tests/test_raw_sources.py` added.
- [ ] Upload tests added or dependency override documented.
- [ ] Rebuild discovery tests added.
- [ ] UI static smoke test added.
- [ ] `pytest -v` passes.

Repository hygiene:

- [ ] Remove accidental `app/api/routes/sources_api.py` placeholder if still present.
- [ ] Issue #7 can be updated with implementation status.
- [ ] Commit messages are clear.

---

## Suggested Commit Sequence

1. `Clean legacy raw source handling`
2. `Fix upload UI supported formats`
3. `Add raw source binary tests`
4. `Add ingest upload API tests`
5. `Add rebuild document discovery tests`
6. `Remove unused sources API placeholder`
7. `Document Windows verification steps`

---

## Final Note for the Coding Agent

Do not “fix” this by converting uploaded DOCX/PDF/PPTX to text before saving raw files. Raw sources must remain raw. Conversion happens when reading for ingest. Otherwise we lose the original source artifact, and then provenance becomes theater with better typography.
