# Wiki Engine

> [Русская версия](README.ru.md)

LLM-powered personal wiki from markdown documents. Ingests raw documents (`.md`, `.txt`, `.py`, `.pdf`, `.docx`, `.pptx`), builds a structured knowledge base with cross-linked pages, detects conflicts between sources, accumulates reusable rules, and answers questions via LLM — all stored as plain text files. **No databases. No vector embeddings.** Pure markdown knowledge base.

> **Inspired by** [Andrej Karpathy's llm-wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — the idea of an LLM-maintained structured wiki from raw source documents.

## What It Does

Drop your documents into a folder — the system analyzes them, creates wiki pages with cross-links, finds contradictions, and lets you ask questions across the entire knowledge base.

## How to Use — Step by Step

### 1. Start the server
```powershell
python -m uvicorn app.api.main:app --reload --port 8000
```
Open `http://localhost:8000`.

### 2. Upload documents
Click **📂 Upload** in the header. Select a project and files. The system will:
— Extract text (including from PDF, DOCX, PPTX)
— Analyze content (Step 1)
— Create wiki pages with cross-links (Step 2)
— Run structural linting

### 3. Ask questions
Type questions in the central chat area. The system finds relevant pages and answers with `[[slug]]` citations.

The **project filter** dropdown (with checkboxes) limits context to selected projects.

### 4. Resolve conflicts
If contradictions are found between sources, they appear in the **Conflicts** tab (right panel). Resolution workflow:
1. Understand the contradiction
2. Choose interpretation (options provided)
3. Preview extracted skill
4. Confirm — the system applies changes and adds the rule to `skills.md`

### 5. Rebuild when needed
The **🔄 Rebuild** button reprocesses all source files from `raw/`. Useful after:
— Changing source documents
— Updating `skills.md` or `AGENTS.md`
— Switching LLM models

### 6. Audit duplicates
The **🔍 Audit** button checks for duplicate and overlapping pages. Shows exact duplicates by default (same title, same project). The **+ Overlaps** toggle reveals pages with shared references and tags.

---

## Interface

Three-zone layout:

### Left panel — Sessions & Controls
- **Chat history**: switch sessions, delete (✕)
- **Header buttons**: 📂 Upload, 🔄 Rebuild, 🔍 Audit, 🗑️ Clear

### Center — Chat workspace
- Type questions and press Enter
- **Project filter**: multi-select dropdown
- **Citations**: `[[slug]]` links — click to open in the right panel
- **Provenance**: `^[raw/file.md]` markers link to source files
- Session context persists across messages

### Right panel — Wiki explorer
- **Wiki tab**: project tree, page viewer, full-text search
- **Conflicts tab**: open/resolved/cross-project conflicts with step-by-step resolution
- **Drafts tab**: pending changes awaiting review

---

## Key Features

### 1. Multi-format ingest
6 formats supported: `.md`, `.txt`, `.py`, `.pdf`, `.docx`, `.pptx`. Binary formats converted via markitdown.

### 2. Two-step ingest (Plan → Execute)
- **Step 1 — Analysis**: LLM reads source, identifies entities/concepts, finds conflicts, plans pages
- **Step 2 — Generation**: LLM generates pages with frontmatter, wikilinks, sources, and provenance markers

Each page has structured frontmatter: `title`, `project`, `type`, `tags`, `confidence`, `sources`, `last_confirmed`.

### 3. Post-processing pipeline
After generation, the system fixes LLM artifacts:
- Removes nested wikilinks (`[[a/[[b]]` → `[[b]]`)
- Replaces backslashes with forward slashes (`\` → `/`)
- Strips raw-file paths from wikilinks
- Enforces correct `project` field from slug

### 4. Conflict detection & skill extraction
On detecting contradictions:
- Conflict written to `conflicts.md` with full context
- **Cross-project differences** identified separately — not errors
- Resolution extracts a rule (skill) added to `skills.md`

### 5. Skill accumulation
Resolved conflicts produce reusable rules:
```
Source Trust Rules:
- For eywa-demo: deploy_guide.md is authoritative for infrastructure
Conflict Resolution Patterns:
- Dependency management and security are separate concerns
```

### 6. Structural linting
Runs automatically after each ingest. Checks: broken wikilinks, orphan pages, duplicate titles, stale pages, char limits, missing frontmatter, invalid provenance.

### 7. Duplicate & overlap audit
**🔍 Audit** button (`GET /api/audit/duplicates`) runs detection:
- **Duplicates** (score ≥ 0.9): same title in same project
- **Overlaps** (score ≥ 0.6): shared wikilinks within same project
- **Tags** (score ≥ 0.5): 3+ shared tags in same project

No LLM — purely structural analysis.

### 8. ReAct query agent
Questions classified (factual/comparison/exploratory/meta). The agent retrieves relevant wiki pages, answers with `[[slug]]` citations, and acknowledges uncertainty.

### 9. Multi-project support
- Documents organized by project (`eywa-demo`, `aurora-demo`, `_general`)
- Cross-project pages (in `_general/`) compare approaches without false conflicts
- Project filter in chat and search

### 10. Wiki cross-links
During ingest, pages auto-linked via `auto_link` (up to 10 candidates per page). Format: `[[slug]]` or `[[slug|display text]]`.

---

## Quick Start

### Windows (native)

1. **Clone and install:**
   ```powershell
   git clone https://github.com/aikula/ai_personal_wiki.git
   cd ai_personal_wiki
   pip install -e .
   ```

2. **Set environment variables** (or `.env` file):
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

4. **Open browser:** http://localhost:8000

5. **Upload documents** (📂) → **Ask questions** → **Resolve conflicts** → **Rebuild** as needed

### Linux / macOS

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

### Docker

```bash
cp .env.example .env
# Edit .env with your credentials
docker compose up --build
```

Open `http://localhost:8000`.

> **Windows + Docker:** Docker Desktop volume mounts to OneDrive may have performance issues. Use native start (Option 1) if `wiki-data` is on OneDrive.

---

## Local LLMs (Ollama)

```bash
# Start Ollama
ollama run qwen2.5:14b

# Set environment (Linux/macOS)
export LLM_BASE_URL="http://localhost:11434/v1"
export LLM_MODEL="qwen2.5:14b"
export OPENAI_API_KEY="ollama"
```

> **Model recommendations**: for strong reasoning with multilingual support — `qwen2.5:14b`, `llama3.1:8b`, or `gemma3:12b`.

---

## Troubleshooting

### Audit shows old data after server restart
**Cause:** browser caches the old index.html.
**Fix:** hard refresh the page (Ctrl+F5).

### LLM returns `unsupported_country_region_territory` (403)
**Cause:** OpenAI API unavailable in your region.
**Fix:** use a local LLM via Ollama or another API provider.

### `Could not parse JSON from LLM response`
**Cause:** LLM hit the `max_tokens` output limit.
**Fix:** use a model with larger context window, or reduce `entity_page_chars` / `concept_page_chars` in `config/settings.yaml`.

### Pages have wrong `project` field in frontmatter
**Fix:** run `🔄 Rebuild` — after rebuild, `project` is automatically corrected from the page slug.

---

## API Endpoints

### Ingest
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/ingest` | Upload single file |
| `POST` | `/api/ingest/batch` | Upload multiple files |
| `POST` | `/api/ingest/rebuild` | Rebuild wiki from raw (SSE) |
| `POST` | `/api/ingest/clear` | Reset wiki to clean state |
| `GET` | `/api/ingest/raw` | List raw files |
| `GET/POST` | `/api/ingest/drafts/*` | Draft operations |

### Chat
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chat` | Send question (SSE stream) |
| `GET` | `/api/chat/sessions` | List sessions |
| `GET/DELETE` | `/api/chat/sessions/{id}` | Get / delete session |
| `POST` | `/api/chat/sessions/{id}/crystallize` | Save session as wiki page |

### Wiki
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/wiki/tree` | Full wiki tree |
| `GET` | `/api/wiki/page/{slug}` | Render page |
| `GET` | `/api/wiki/search` | Full-text search |
| `GET` | `/api/wiki/projects` | Project list |

### Conflicts
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/conflicts` | List conflicts |
| `GET` | `/api/conflicts/{id}` | Get conflict details |
| `POST` | `/api/conflicts/{id}/resolve` | Resolve conflict |
| `POST` | `/api/conflicts/{id}/prepare-update` | Prepare draft |
| `POST` | `/api/conflicts/{id}/apply-update` | Apply resolution |

### Audit
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/audit/duplicates` | Find duplicates and overlaps |
| `GET` | `/api/audit/synthesis` | List merge candidates |

### Health
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |

---

## Supported Formats

| Format | Extension | Processing |
|--------|-----------|------------|
| Markdown | `.md` | Direct ingest |
| Plain text | `.txt` | Direct ingest |
| Python | `.py` | Direct ingest |
| PDF | `.pdf` | Converted via markitdown |
| Word | `.docx` | Converted via markitdown |
| PowerPoint | `.pptx` | Converted via markitdown |

---

## Project Structure

```
wiki-engine/
├── app/
│   ├── agents/              # LLM agents: ingest, query, audit
│   ├── core/                # wiki_fs, linter, llm_client, utils
│   ├── api/                 # FastAPI: routes, models, dependencies
│   └── ui/
│       └── index.html       # React SPA (no build step)
├── wiki-data/               # Data volume (Docker mount)
│   ├── raw/                 # Source documents
│   ├── wiki/                # Generated pages
│   ├── drafts/              # Pending drafts
│   ├── conflicts.md         # Conflict queue
│   └── skills.md            # Accumulated rules
├── config/settings.yaml     # Configuration
└── tests/                   # 104 tests
```

---

## Development

### Tests
```bash
pip install -e ".[dev]"
pytest tests/ -v    # 104 tests: WikiFS, Linter, API, Interpreter, Utils
```

### Lint
```bash
ruff check app/ tests/
```

---

## License

MIT
