# Enterprise Knowledge Compiler

> [Русская версия](README.ru.md)

**Enterprise Knowledge Compiler** is an open-source LLM-powered system that turns raw documents into a structured, auditable, conflict-aware knowledge base.

It ingests source documents (`.md`, `.txt`, `.py`, `.pdf`, `.docx`, `.pptx`), creates cross-linked wiki pages, preserves provenance to raw sources, detects contradictions, accumulates reusable resolution rules, and answers questions via LLM with citations. The public edition keeps the knowledge layer as plain text files and avoids mandatory databases or vector embeddings.

> **Inspired by** [Andrej Karpathy's llm-wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — the idea of an LLM-maintained structured wiki from raw source documents.

## Positioning

This project is not intended to be another generic "chat with documents" wrapper. Standard RAG usually retrieves fragments from the existing document mess. Enterprise Knowledge Compiler first tries to compile that mess into a durable knowledge layer:

- source documents become structured pages;
- generated pages keep stable slugs, frontmatter, links, and provenance;
- contradictions become explicit conflicts;
- expert decisions become reusable rules in `skills.md`;
- structural audits find duplicates, overlaps, broken links, stale facts, and invalid provenance;
- the resulting knowledge base can be queried by people, assistants, and AI agents.

## Editions

| Edition | Status | Best for | Storage/control plane |
|---|---|---|---|
| Open-source local edition | Public repository | Personal research, local knowledge work, demos | Plain files in `wiki-data/` |
| Open-source server edition | Public repository | Private VPS or small team demo | Plain files, optional Basic Auth |
| Open-source multi-user edition | Public repository | Small hosted demo, isolated user workspaces, token metering | Plain files + SQLite control plane |
| Corporate implementation | Paid pilot / adaptation / rollout | Document-heavy and regulated organizations | Plain knowledge layer + PostgreSQL control plane, SSO/RBAC, audit, connectors, review workflows |

The corporate implementation is not described as a finished shrink-wrapped product. It is a hardened version of the same knowledge-compilation approach, adapted through a paid pilot to the customer's documents, infrastructure, security model, and operating processes.

## What It Does

Drop documents into the system. It analyzes them, creates wiki pages with cross-links, finds contradictions, and lets you ask questions across the generated knowledge base.

Public edition capabilities:

- multi-format ingest;
- two-step LLM pipeline: analysis first, page generation second;
- generated wiki pages with structured frontmatter;
- provenance markers linking back to raw files;
- conflict detection and manual resolution;
- reusable rules extracted from resolved conflicts;
- structural linting and audit;
- multi-project knowledge organization;
- local/server/multi-user modes.

## Corporate implementation roadmap

For corporate customers, the project can be extended into an enterprise implementation with:

- PostgreSQL control plane for users, workspaces, audit events, permissions, usage, and job metadata;
- SSO/OIDC/SAML or customer identity integration;
- RBAC and workspace/team access control;
- large-volume document pipelines;
- source cards, claims layer, source drift tracking, and stronger provenance;
- expert review workflows for semantic changes;
- connectors to SharePoint, Google Drive, Confluence, Jira, file shares, object storage, DMS/ECM systems;
- deployment on customer infrastructure, private cloud, or controlled regional cloud;
- observability, job queues, backups, and operational runbooks;
- Arabic/English corporate demo packs and domain-specific pilots.

See [`docs/corporate_edition_roadmap.md`](docs/corporate_edition_roadmap.md) for the enterprise pilot specification.

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

The project filter lives next to the composer input, supports multi-select, limits context to selected projects, and can be cleared in one click.

### 4. Resolve conflicts
If contradictions are found between sources, they appear in the **Conflicts** tab (right panel). Resolution workflow:
1. Understand the contradiction
2. Choose interpretation (options provided)
3. Preview extracted skill
4. Confirm — the system applies changes and adds the rule to `skills.md`

To update a wiki page from a resolved conflict:
1. **Prepare update**: LLM generates candidate content, diff is shown
2. **Apply update**: applies stored candidate (NO repeated LLM call)
3. **Reject update**: deletes draft without modifying the page

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
- **Header buttons**: 📂 Upload, 🔍 Audit, and a **Manage** menu for rebuild/clear actions

### Center — Chat workspace
- Type questions and press Enter
- **Project filter**: multi-select dropdown
- **Citations**: `[[slug]]` links — click to open in the right panel
- **Provenance**: `^[raw/file.md]` markers link to source files
- In multi-user mode, provenance links and direct raw downloads automatically carry `access_token` so downloads keep working without a separate manual login
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

### 7. Duplicate, overlap & structural audit

The **🔍 Audit** button shows a modal with two sections:

**Overlaps/Duplicates** (`GET /api/audit/duplicates`):
- **Duplicates** (score ≥ 0.9): same title in same project
- **Overlaps** (score ≥ 0.6): shared wikilinks within same project
- **Tags** (score ≥ 0.5): 3+ shared tags in same project

**Structural lint** (`GET /api/audit/lint`): 17 checks without LLM — broken wikilinks, missing frontmatter, char limits, orphan pages, invalid provenance, duplicate titles, etc.

### 8. Raw files and provenance
- Pages and chat answers contain provenance markers like `^[raw/path/to/file.md]`
- In multi-user mode, the UI automatically appends `access_token` to raw links so direct downloads work without a separate login step
- Raw download endpoint: `GET /api/wiki/raw/{slug}`

### 9. ReAct query agent
Questions classified (factual/comparison/exploratory/meta). The agent retrieves relevant wiki pages, answers with `[[slug]]` citations, and acknowledges uncertainty.

### 10. Multi-project support
- Documents organized by project (`eywa-demo`, `aurora-demo`, `_general`)
- Cross-project pages (in `_general/`) compare approaches without false conflicts
- Project filter in chat and search

### 11. Wiki cross-links
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

2. **Create a `.env` file**:
   ```powershell
   Copy-Item .env.example .env
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
cp .env.example .env
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

# Set values in .env
# LLM_BASE_URL="http://localhost:11434/v1"
# LLM_MODEL="qwen2.5:14b"
# LLM_API_KEY="ollama"
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
| `POST` | `/api/ingest/batch` | Upload multiple files (form-data: `project` + `files[]` or `file[]`). Empty batch → 400 |
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
| `GET` | `/api/wiki/raw/{path}` | Raw source file for viewing/downloading (`access_token` supported in multi-user) |

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
| `GET` | `/api/audit/lint` | Full structural lint (17 checks) |
| `GET` | `/api/audit/synthesis` | List merge candidates |
| `POST` | `/api/audit/synthesis` | Run synthesis |
| `POST` | `/api/audit/synthesis/{cid}/resolve` | Apply merge |

### Auth (multi-user)
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/register` | Register new user |
| `POST` | `/api/auth/login` | Login and receive Bearer token |
| `POST` | `/api/auth/logout` | Logout and revoke token |
| `GET` | `/api/auth/me` | Current user, workspace, and quota state |

### Usage (multi-user)
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/usage/me` | Current token usage and quotas |

### Health
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |

---

## License

MIT
