# Wiki Engine

> [Русская версия](README.ru.md) | [English](README.en.md)

LLM-powered personal wiki from markdown documents. **No databases. No vector embeddings.** Pure markdown knowledge base.

- **Upload** documents (`.md`, `.txt`, `.py`, `.pdf`, `.docx`, `.pptx`)
- **Auto-generate** structured wiki pages with cross-links, frontmatter, and provenance markers
- **Detect** conflicts between sources and accumulate reusable resolution rules
- **Query** the wiki via LLM chat with `[[slug]]` citations
- **Audit** duplicates and overlapping pages

> **Inspired by** [Andrej Karpathy's llm-wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

## Quick Start

```bash
pip install -e .
cp .env.example .env  # or: Copy-Item .env.example .env (PowerShell)
uvicorn app.api.main:app --reload --port 8000
```

Open http://localhost:8000 → 📂 Upload → Ask questions → Resolve conflicts → 🔍 Audit

The app reads `.env` on startup and warns in logs if the LLM connection is not configured or cannot be reached.

Docker: `docker compose up --build` (container runs as non-root `appuser`).

## Documentation

Full documentation with user guide, feature descriptions, API reference, and troubleshooting:

- **[README.ru.md](README.ru.md)** — русская версия с пошаговым руководством
- **[README.en.md](README.en.md)** — English version with step-by-step guide

## License

MIT
