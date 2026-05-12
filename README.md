# Wiki Engine

LLM-powered personal wiki from markdown documents. Ingests raw documents (`.md`, `.txt`, `.py`, `.pdf`, `.docx`, `.pptx`), builds a structured knowledge base with cross-linked pages, detects conflicts between sources, accumulates reusable rules, and answers questions via LLM — all stored as plain text files. **No databases. No vector embeddings.** Pure markdown knowledge base.

> **Inspired by** [Andrej Karpathy's llm-wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — the idea of an LLM-maintained structured wiki from raw source documents.

## Architecture

```
Raw sources (immutable)  →  Wiki layer (LLM-maintained)  →  Query interface
```

Three data layers, all plain text:

| Layer | Path | Description |
|-------|------|-------------|
| **Raw** | `wiki-data/raw/` | Source documents, never modified by the engine |
| **Wiki** | `wiki-data/wiki/` | Generated pages — entities, concepts, indexes with frontmatter, wikilinks, and confidence scores |
| **Rules** | `wiki-data/skills.md`, `AGENTS.md` | Accumulated rules that govern future agent behaviour |

## UI Walkthrough

Open `http://localhost:8000` after starting the server. The interface has three zones:

### Left panel — Sessions & Controls
- **Chat history**: click any session to resume, delete sessions with ✕
- **Header buttons**: 📂 Upload, 🔄 Rebuild from raw, 🗑️ Clear wiki

### Center — Chat workspace
- Type questions about your wiki and press Enter
- **Project filter**: dropdown with checkboxes to filter answers/context by project(s)
- **Citations**: answers include `[[slug]]` links — click to open the page in the right panel
- **Provenance**: `^[raw/source.md]` markers link back to original source files
- Session context is preserved across messages

### Right panel — Wiki explorer
- **Wiki tab**: collapsible project tree, click pages to view; search bar for full-text search
- **Conflicts tab**: open/resolved/cross-project conflicts with guided resolution flow
- **Drafts tab**: pending ingest drafts for review

## Key Features

### 1. Multi-format document ingest
Upload documents in 6 formats: `.md`, `.txt`, `.py`, `.pdf`, `.docx`, `.pptx`. Documents are converted to text via markitdown (for binary formats) and processed through the ingest pipeline.

### 2. Two-step ingest (Plan → Execute)
- **Step 1 — Analysis**: LLM reads source, identifies entities/concepts, finds conflicts with existing wiki, and plans pages
- **Step 2 — Generation**: LLM generates wiki pages with frontmatter, wikilinks, source references, and provenance markers

Every page includes structured frontmatter: title, project, type, tags, confidence, sources, dates.

### 3. Conflict detection & guided resolution
When ingest finds contradictions between sources or between source and wiki:
- Conflicts are written to `conflicts.md` with full context (both sides, suggested options)
- **Cross-project differences** are identified separately — not treated as errors
- Resolution workflow: understand → choose interpretation → preview skill → confirm
- Extracted skills are added to `skills.md` as binding rules for future ingest

### 4. Skill accumulation
Resolved conflicts produce reusable rules in `skills.md`. Example:
> *When a version mismatch occurs between wiki content and source documentation, always prioritize the source file as the single source of truth.*

### 5. Draft/diff workflow
Conflict resolutions create reviewable drafts applying changes to affected wiki pages. Inspect diffs before applying. No silent page overwrites.

### 6. Structural linting
The linter checks: broken wikilinks, orphan pages, duplicate titles, stale low-confidence pages, char limit violations, missing frontmatter, superseded pages.

### 7. ReAct query agent
Questions are classified (factual/comparison/exploratory/meta). The agent retrieves relevant wiki pages, answers in Russian, cites sources as `[[slug]]`, and acknowledges uncertainty when appropriate.

### 8. Wiki cross-links
During ingest, pages are automatically linked via `auto_link` (up to 10 candidates per page). The query agent uses wikilinks for citations. All links use `[[slug]]` or `[[slug|display text]]` format.

### 9. Multi-project support
- Documents belong to projects (e.g. `eywa-demo`, `aurora-demo`, `_general`)
- Cross-project pages can compare approaches without creating false conflicts
- Project filter in chat limits context to selected projects
- Multi-project search compares implementations side-by-side

### 10. Provenance tracking
Factual claims are annotated with `^[raw/source.md]` markers pointing to the source document. Click to view the original raw file.

## Project Structure

```
wiki-engine/
├── app/
│   ├── agents/
│   │   ├── ingest_agent.py       # Plan-and-Execute ingest pipeline
│   │   ├── ingest_helpers.py     # JSON parsing, page rendering helpers
│   │   ├── ingest_prompts.py     # LLM prompt templates (Step1, Step2, Skill)
│   │   ├── ingest_types.py       # Data types (AnalysisResult, IngestResult)
│   │   ├── query_agent.py        # Policy-driven ReAct query agent
│   │   ├── query_prompts.py      # Query prompt templates
│   │   ├── query_types.py        # Chat message types
│   │   └── audit_agent.py        # Parallel structural audit
│   ├── core/
│   │   ├── wiki_fs.py            # Filesystem operations (single write source)
│   │   ├── raw_sources.py        # Raw file listing, conversion, state tracking
│   │   ├── linter.py             # WikiLinter: structural checks
│   │   ├── interpreter.py        # Sandboxed Python code interpreter
│   │   ├── llm_client.py         # OpenAI-compatible client wrapper
│   │   ├── token_budget.py       # Character budget management
│   │   └── utils.py              # Shared: JSON parsing, wikilinks, slug validation, auto_link
│   ├── api/
│   │   ├── main.py               # FastAPI app entrypoint
│   │   ├── models.py             # Pydantic request/response models
│   │   ├── dependencies.py       # DI: agents, sessions, settings
│   │   └── routes/
│   │       ├── ingest.py         # Upload, batch, rebuild, clear, drafts
│   │       ├── chat.py           # Chat SSE streaming, session management
│   │       ├── wiki.py           # Tree, page render, search, projects
│   │       ├── conflicts.py      # List, resolve, comment, draft apply
│   │       └── settings.py       # Configuration, LLM test
│   ├── ui/
│   │   └── index.html            # Single-file React SPA (no build step)
│   └── config.py                 # Settings loader (yaml + env)
├── wiki-data/                    # Data volume — never commit
│   ├── raw/                      # Source documents
│   ├── wiki/                     # Generated wiki pages
│   ├── drafts/                   # Pending update drafts
│   ├── conflicts.md              # Conflict queue
│   └── skills.md                 # Accumulated rules
├── config/
│   └── settings.yaml             # LLM URL, model, limits, thresholds
├── docs/
│   ├── test_processing_expected_behavior.md  # Test scenarios & expected results
│   └── wiki_engine_project_roadmap.md        # Full development plan
├── tests/                        # 104 tests: WikiFS, Linter, API, Interpreter, Utils
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## Requirements

- Python 3.11+
- OpenAI-compatible LLM endpoint (OpenAI, Ollama, vLLM, Azure, LM Studio, etc.)
- For PDF/DOCX/PPTX ingestion: `markitdown` (installed automatically with `pip install -e .`)

## Quick Start

### Option 1: Windows (native)

1. **Clone and install:**
   ```powershell
   git clone https://github.com/aikula/ai_personal_wiki.git
   cd ai_personal_wiki
   pip install -e .
   ```

2. **Set environment variables** (or create `.env` file):
   ```powershell
   set OPENAI_API_KEY=sk-...
   set LLM_BASE_URL=https://api.openai.com/v1
   set LLM_MODEL=gpt-4o
   set LANGUAGE=ru
   ```

3. **Run the server:**
   ```powershell
   python -m uvicorn app.api.main:app --reload --port 8000
   ```

4. **Open in browser:** http://localhost:8000

5. **Upload documents** (📂 button) → **Ask questions** in chat → **Resolve conflicts** (right panel) → **Rebuild** as needed

### Option 2: Linux / macOS (native)

```bash
git clone https://github.com/aikula/ai_personal_wiki.git
cd ai_personal_wiki
pip install -e .

export OPENAI_API_KEY="sk-..."
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_MODEL="gpt-4o"
export LANGUAGE="ru"

uvicorn app.api.main:app --reload --port 8000
```

### Option 3: Docker Compose

```bash
cp .env.example .env
# Edit .env with your credentials
docker compose up --build
```

Open http://localhost:8000

> **Windows + Docker note:** Docker Desktop volume mounts to OneDrive may have performance issues. Use native start (Option 1) if `wiki-data` is on OneDrive.

## Using with Local LLMs (Ollama)

```bash
# Start Ollama
ollama run qwen2.5:14b

# Set environment (Linux/macOS)
export LLM_BASE_URL="http://localhost:11434/v1"
export LLM_MODEL="qwen2.5:14b"
export OPENAI_API_KEY="ollama"

# Docker + Ollama
# Use http://host.docker.internal:11434/v1 as LLM_BASE_URL
```

> **Model recommendations for local LLMs**: Use models with strong Russian + reasoning capabilities. Recommended minimum: `qwen2.5:14b`, `llama3.1:8b`, or `gemma3:12b`.

## Configuration

Edit `config/settings.yaml` or override via environment variables:

```yaml
llm:
  base_url: "https://api.openai.com/v1"
  model: "gpt-4o"
  temperature: 0.1
  timeout_seconds: 60

limits:
  entity_page_chars: 3500
  concept_page_chars: 5250
  index_l0_chars: 10500
  conflicts_md_chars: 35000

ingest:
  max_pages_per_source: 10
  auto_lint_after_ingest: true

query:
  context_budget_chars: 21000
  max_wiki_pages_in_context: 6
  allow_code_execution: false

language: "ru"   # UI and generated content language (ru / en)
```

Environment variables take priority over `settings.yaml`:

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | LLM API key | — |
| `LLM_BASE_URL` | LLM endpoint URL | `https://api.openai.com/v1` |
| `LLM_MODEL` | Model name | `gpt-4o` |
| `WIKI_DATA_PATH` | Path to wiki-data directory | `./wiki-data` |
| `LANGUAGE` | Interface language (`ru` / `en`) | `ru` |

## API Endpoints

### Ingest
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/ingest` | Upload single file (form-data: `project` + `file`) |
| `POST` | `/api/ingest/batch` | Upload multiple files (form-data: `project` + `files[]`) |
| `POST` | `/api/ingest/rebuild` | Rebuild wiki from raw (JSON: `{"confirm": true}`, SSE) |
| `POST` | `/api/ingest/clear` | Reset wiki to clean state (JSON: `{"confirm": true}`) |
| `GET` | `/api/ingest/raw` | List raw files (`?project=` optional) |
| `GET` | `/api/ingest/drafts` | List pending drafts |
| `GET` | `/api/ingest/drafts/{id}` | Get draft details |
| `POST` | `/api/ingest/drafts/{id}/apply` | Apply a draft |
| `POST` | `/api/ingest/drafts/{id}/reject` | Reject a draft |

### Chat
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chat` | Send question, receive SSE stream |
| `GET` | `/api/chat/sessions` | List all sessions |
| `GET` | `/api/chat/sessions/{id}` | Get session history |
| `DELETE` | `/api/chat/sessions/{id}` | Delete session |
| `POST` | `/api/chat/sessions/{id}/crystallize` | Save session as wiki page |

### Wiki
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/wiki/tree` | Full wiki tree for navigation |
| `GET` | `/api/wiki/page/{slug}` | Render single page (HTML + raw) |
| `GET` | `/api/wiki/search` | Full-text search (`?q=...&project=...&projects=...`) |
| `GET` | `/api/wiki/raw/{slug}` | Raw markdown |
| `GET` | `/api/wiki/projects` | List projects with wiki/raw counts |
| `GET` | `/api/wiki/metrics` | Graph metrics (orphans, links, etc.) |

### Conflicts
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/conflicts` | List open + resolved conflicts (with `is_cross_project` flag) |
| `GET` | `/api/conflicts/{id}` | Get single conflict |
| `POST` | `/api/conflicts/{id}/resolve` | Resolve with choice + comment + skill extraction |
| `POST` | `/api/conflicts/{id}/comment` | Add comment without resolving |
| `POST` | `/api/conflicts/{id}/prepare-update` | Prepare draft update for affected wiki page |
| `POST` | `/api/conflicts/{id}/apply-update` | Apply prepared conflict resolution to wiki |

### Settings
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/settings` | Get current settings (API key masked) |
| `POST` | `/api/settings` | Update LLM connection settings |
| `GET` | `/api/settings/test` | Test LLM connectivity |

### Health
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |

## Supported File Formats

| Format | Extension | Processing |
|--------|-----------|------------|
| Markdown | `.md` | Direct ingestion |
| Plain text | `.txt` | Direct ingestion |
| Python | `.py` | Direct ingestion |
| PDF | `.pdf` | Converted via markitdown |
| Word | `.docx` | Converted via markitdown |
| PowerPoint | `.pptx` | Converted via markitdown |

## Development

### Run tests
```bash
pip install -e ".[dev]"
pytest tests/ -v    # 104 tests covering WikiFS, Linter, API, Interpreter, Utils
```

### Lint
```bash
ruff check app/ tests/
```

### Project docs
- `docs/wiki_engine_project_roadmap.md` — full development plan, phases, MVP criteria
- `docs/agents_sheme.md` — data flow diagram
- `docs/test_processing_expected_behavior.md` — test scenarios and expected behavior

## License

MIT
