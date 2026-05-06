# Wiki Engine

LLM-powered personal wiki from markdown documents. Ingests raw `.md` files, builds a structured knowledge base, detects conflicts, and answers questions — all stored as plain text. No databases. No vector embeddings.

## Architecture

```
Raw sources (immutable)  →  Wiki layer (LLM-maintained)  →  Query interface
```

Three layers:
- **`raw/`** — uploaded markdown files, never modified by the agent
- **`wiki/`** — generated entity/concept pages with frontmatter, wikilinks, and confidence scores
- **`skills.md` / `AGENTS.md`** — binding rules that govern agent behavior

## Key Features

- **Two-step ingest** — analysis pass → generation pass, never writes blindly
- **Conflict detection** — contradictions recorded in `conflicts.md` without blocking ingest
- **Skill accumulation** — resolved conflicts produce reusable rules in `skills.md`
- **Structural linting** — broken links, orphan pages, char limits, stale content
- **ReAct query agent** — classifies questions (factual/comparison/exploratory/meta), retrieves wiki pages, cites sources as `[[slug]]`
- **Session crystallization** — Q&A sessions can be distilled into wiki pages

## Project Structure

```
wiki-engine/
├── app/
│   ├── agents/
│   │   ├── ingest_agent.py       # Plan-and-Execute ingest pipeline
│   │   ├── query_agent.py        # Policy-driven ReAct query agent
│   │   └── audit_agent.py        # Parallel structural + semantic audit
│   ├── core/
│   │   ├── wiki_fs.py            # Filesystem operations (single write source)
│   │   ├── linter.py             # WikiLinter: structural checks
│   │   ├── interpreter.py        # Sandboxed Python code interpreter
│   │   ├── llm_client.py         # OpenAI-compatible client wrapper
│   │   ├── token_budget.py       # Character budget management
│   │   └── utils.py              # Shared utilities (JSON parsing, wikilinks)
│   ├── api/
│   │   ├── main.py               # FastAPI app entrypoint
│   │   ├── models.py             # Pydantic request/response models
│   │   ├── dependencies.py       # DI: agents, sessions, settings
│   │   └── routes/
│   │       ├── ingest.py         # POST /ingest, /batch, /rebuild; GET /raw
│   │       ├── chat.py           # POST /chat (SSE); session management
│   │       ├── wiki.py           # GET /tree, /page, /search, /raw
│   │       ├── conflicts.py      # GET/POST /conflicts, resolve, comment
│   │       └── settings.py       # GET/POST /settings, LLM test
│   ├── ui/
│   │   └── index.html            # Single-file SPA (no build step)
│   └── config.py                 # Settings loader (yaml + env)
├── wiki-data/                    # MOUNTED VOLUME — never commit
│   ├── raw/                      # Source documents
│   ├── wiki/                     # Generated wiki pages
│   ├── conflicts.md              # Conflict queue
│   └── skills.md                 # Accumulated rules
├── config/
│   └── settings.yaml             # LLM URL, model, limits, thresholds
├── tests/
├── docs/
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## Requirements

- Python 3.11+
- OpenAI-compatible LLM endpoint (OpenAI, Ollama, vLLM, Azure, etc.)

## Quick Start

### Option 1: Windows (native)

1. **Install dependencies:**
   ```powershell
   pip install -e .
   ```

2. **Set environment variables:**
   ```powershell
   set OPENAI_API_KEY=sk-...
   set LLM_BASE_URL=https://api.openai.com/v1
   set LLM_MODEL=gpt-4o
   set WIKI_DATA_PATH=./wiki-data
   ```

   Or create a `.env` file:
   ```env
   OPENAI_API_KEY=sk-...
   LLM_BASE_URL=https://api.openai.com/v1
   LLM_MODEL=gpt-4o
   ```

3. **Run the server:**
   ```powershell
   python -m uvicorn app.api.main:app --reload --port 8000
   ```

4. **Open in browser:** http://localhost:8000

### Option 2: Linux / macOS (native)

1. **Install dependencies:**
   ```bash
   pip install -e .
   ```

2. **Set environment variables:**
   ```bash
   export OPENAI_API_KEY="sk-..."
   export LLM_BASE_URL="https://api.openai.com/v1"
   export LLM_MODEL="gpt-4o"
   export WIKI_DATA_PATH="./wiki-data"
   ```

3. **Run the server:**
   ```bash
   uvicorn app.api.main:app --reload --port 8000
   ```

4. **Open in browser:** http://localhost:8000

### Option 3: Docker Compose (recommended for Linux/macOS)

1. **Create `.env` file:**
   ```bash
   cp .env.example .env
   # Edit .env and set your LLM credentials
   ```

2. **Build and start:**
   ```bash
   docker compose up --build
   ```

3. **Open in browser:** http://localhost:8000

4. **Stop:**
   ```bash
   docker compose down
   ```

> **Note for Windows + Docker:** Docker Desktop on Windows works but volume mounts to OneDrive may have performance issues. Use native Windows start (Option 1) if `wiki-data` is on OneDrive.

### Option 4: Docker (manual)

```bash
docker build -t wiki-engine .
docker run -p 8000:8000 \
  -v "$(pwd)/wiki-data:/wiki-data" \
  -v "$(pwd)/config:/app/config" \
  -e OPENAI_API_KEY="sk-..." \
  -e LLM_BASE_URL="https://api.openai.com/v1" \
  -e LLM_MODEL="gpt-4o" \
  wiki-engine
```

## Using with Ollama (local LLM)

1. **Start Ollama:**
   ```bash
   ollama run qwen2.5:14b
   ```

2. **Set environment:**
   ```bash
   # Linux/macOS
   export LLM_BASE_URL="http://localhost:11434/v1"
   export LLM_MODEL="qwen2.5:14b"
   export OPENAI_API_KEY="ollama"

   # Windows
   set LLM_BASE_URL=http://localhost:11434/v1
   set LLM_MODEL=qwen2.5:14b
   set OPENAI_API_KEY=ollama
   ```

3. **Docker + Ollama:** use `http://host.docker.internal:11434/v1` as `LLM_BASE_URL`.

## API Endpoints

### Ingest
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/ingest` | Upload single `.md` file (form-data: `project` + `file`) |
| `POST` | `/api/ingest/batch` | Upload multiple files (form-data: `project` + `files[]`) |
| `GET` | `/api/ingest/raw` | List raw files (`?project=` optional) |
| `POST` | `/api/ingest/rebuild` | Rebuild wiki from raw (JSON: `{"confirm": true}`, SSE stream) |

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
| `GET` | `/api/wiki/search` | Full-text search (`?q=...&project=...`) |
| `GET` | `/api/wiki/raw/{slug}` | Raw markdown (for editing) |

### Conflicts
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/conflicts` | List open + resolved conflicts |
| `GET` | `/api/conflicts/{id}` | Get single conflict |
| `POST` | `/api/conflicts/{id}/resolve` | Resolve with choice + comment |
| `POST` | `/api/conflicts/{id}/comment` | Add comment without resolving |

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
```

Environment variables always take priority over `settings.yaml`:
- `OPENAI_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`
- `WIKI_DATA_PATH`

## Development

### Run tests
```bash
pip install -e ".[dev]"
pytest tests/ -v
```

### Lint
```bash
ruff check app/ tests/
```

### Project roadmap
See `docs/wiki_engine_project_roadmap.md` for the full development plan, phases, and MVP criteria.

### Agent schema
See `docs/agents_sheme.md` for the data flow diagram.

## License

MIT
