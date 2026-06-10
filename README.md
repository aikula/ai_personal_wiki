# Enterprise Knowledge Compiler

> [Русская версия](README.ru.md) | [English](README.en.md)

**Enterprise Knowledge Compiler** is an open-source LLM-powered system that converts raw documents into an auditable, cross-linked, conflict-aware knowledge base.

It is not just a "chat with documents" wrapper. The system ingests source files, extracts structure, creates wiki pages, preserves provenance, detects contradictions, accumulates resolution rules, and lets people or AI agents query the resulting knowledge layer with citations.

The public repository contains the open-source edition for individual, local, server, and small multi-user deployments. For corporate customers, the same architectural approach can be extended into a hardened enterprise implementation with PostgreSQL, SSO/RBAC, audit logs, large-volume pipelines, connectors, review workflows, and customer-specific deployment requirements.

## What it does

- **Upload** documents (`.md`, `.txt`, `.py`, `.pdf`, `.docx`, `.pptx`)
- **Compile** them into structured wiki pages with frontmatter, cross-links, and provenance markers
- **Detect** conflicts between sources and accumulate reusable resolution rules
- **Query** the generated knowledge base via LLM chat with `[[slug]]` citations
- **Audit** duplicates, overlaps, broken links, stale pages, and structural issues
- **Open raw source files** via provenance links and the `/api/wiki/raw/{path}` endpoint
- **Run locally, on a server, or in multi-user mode** depending on configuration

> **Inspired by** [Andrej Karpathy's llm-wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), but extended toward a practical knowledge-compilation workflow: source documents → structured pages → conflicts → rules → queryable knowledge.

## Editions and positioning

| Edition | Status | Best for | Storage/control plane |
|---|---|---|---|
| Open-source local edition | Public repository | Personal research, individual knowledge work, demos, local experiments | Plain files in `wiki-data/` |
| Open-source server edition | Public repository | Private VPS/team demo with optional auth | Plain files, optional Basic Auth |
| Open-source multi-user edition | Public repository | Small hosted demo, isolated user workspaces, token metering | Plain files + SQLite control plane |
| Corporate implementation | Adaptation / pilot / enterprise rollout | Regulated and document-heavy organizations | Plain knowledge layer + PostgreSQL control plane, SSO/RBAC, audit, connectors, review workflows |

The corporate implementation is not presented as a finished shrink-wrapped SaaS product. It is delivered through a paid pilot and adapted to the customer's document corpus, infrastructure, security model, and operating processes.

## Why not just RAG?

Standard RAG usually retrieves fragments from existing document chaos. Enterprise Knowledge Compiler first turns that chaos into a governed knowledge layer:

- source cards and provenance;
- generated wiki pages with stable slugs;
- explicit conflicts and review decisions;
- reusable resolution rules in `skills.md`;
- structural audit and duplicate detection;
- agent-readable knowledge that can become the foundation for assistants, copilots, and internal AI agents.

## Quick Start

```bash
pip install -e .
cp .env.example .env  # or: Copy-Item .env.example .env (PowerShell)
uvicorn app.api.main:app --reload --port 8000
```

Open http://localhost:8000 → 📂 Upload → Ask questions → Resolve conflicts → 🔍 Audit

The app reads `.env` on startup and warns in logs if the LLM connection is not configured or cannot be reached.

Docker:

```bash
docker compose up --build
```

The container runs as non-root `appuser`.

## Corporate roadmap

Corporate-scale work is tracked separately from the public open-source edition. See:

- [`docs/corporate_edition_roadmap.md`](docs/corporate_edition_roadmap.md) — enterprise pilot and rollout specification
- [`docs/large_scale_wiki_proposal.md`](docs/large_scale_wiki_proposal.md) — large-source ingest, claims, source cards, section retrieval, typed relations
- [`docs/multi_user_sqlite_control_plane_spec.md`](docs/multi_user_sqlite_control_plane_spec.md) — current public multi-user control plane

## Documentation

Full documentation with user guide, feature descriptions, API reference, and troubleshooting:

- **[README.ru.md](README.ru.md)** — русская версия с пошаговым руководством
- **[README.en.md](README.en.md)** — English version with step-by-step guide

## License

MIT
