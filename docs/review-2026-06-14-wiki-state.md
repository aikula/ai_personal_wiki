# Review: wiki state — 2026-06-14

**Context:** post-refactoring (4 fixes) + prod launch + full ingest (`ai@kulinich.ru`)

---

## 1. Environment

| Parameter | Value |
|---|---|
| Mode | `multi_user` |
| URL | `https://ai-wiki.kulinich.ru` |
| LLM | `privateLLM` — connected |
| Traefik | yes, HTTPS, `tghub-network` |
| Users | 14 registered |
| Tests | 349 passed, ruff clean |

---

## 2. Wiki metrics (ai@kulinich.ru)

| Metric | Value |
|---|---|
| **Total pages** | 159 |
| **Projects** | 1 (`_general`) |
| **Open conflicts** | 0 |
| **Claims** | 172 claim files |
| **Source Cards** | 3 (all `unchanged` drift) |
| **Raw files** | 4 (3 ingested, 1 unprocessed) |
| **Broken wikilinks** | 0 |
| **Page types** | 157 entity, 2 index |
| **Log size** | 2922 / 3500 chars |

---

## 3. Issues found

### 3.1 `_general/index.md` exceeds L1 limit
- **Severity:** medium
- **Actual:** 10422 chars
- **Limit:** 10000 (index_l1_chars)
- **Log:** «trimmed to 10239 chars» — trimming code couldn't fit within limit
- **Cause:** greedy trim stops at first section that would overflow; remaining header lines push it over
- **Fix needed:** improve trim algorithm or raise limit in `config/settings.yaml`

### 3.2 Confidence is monotonous — 150/157 pages at 0.8
- **Severity:** low
- **Detail:** LLM defaults to 0.8 for everything. No page below 0.6, only 9 at 0.9-1.0
- **Impact:** `confidence` field provides no signal for review prioritization
- **Root cause:** prompt guidelines define ranges but model doesn't apply them discriminately

### 3.3 Cross-reference hallucinations in «Related Pages»
- **Severity:** medium
- **Detail:** some pages link to unrelated topics in their `## Related Pages` section
  - Example: `_general/education/lesson-analysis-system.md` → `[[_general/diagnostics/654-al-fseries-comm-lost]]`
- **Impact:** confuses navigation, reduces trust

### 3.4 `supersedes` / `superseded_by` never used
- **Severity:** low
- **Detail:** always `null` on all pages
- **Impact:** no versioning trail when pages overlap

### 3.5 `provenance_state: verified` on all pages
- **Severity:** low
- **Detail:** every page claims `verified` + `complete`. Unrealistic for 159 pages.
- **Impact:** no signal for human review

### 3.6 Skills.md is empty
- **Severity:** medium
- **Detail:** only section headers, no accumulated rules
- **Impact:** ingest/query agents operate without domain guidance

### 3.7 Orphan raw file — `audit-2026-05-28-wiki-formation.md`
- **Severity:** info
- **Detail:** 9451-byte markdown file in `raw/_general/` without a Source Card
- **Reason:** internal audit document (not an ingest source), intentionally skipped

---

## 4. What was fixed (this session)

| Issue | Fix |
|---|---|
| `wiki_fix.py` normalization drops display text & anchor | Now preserves `#anchor` and `|display` |
| `control_store_sqlite.py` dead `TYPE_CHECKING` | Removed |
| `wiki_index.py` format drift between `rebuild_index` and `update_index_entry` | `update_index_entry` now uses `## project (N pages)` format |
| `large_source_ingest.py` at 487 lines (near 500 limit) | `merge_analysis` → `large_source_merge.py` (109 st), ingest down to 374 |
| Dev compose started instead of prod | Switched to `docker-compose-prod.yml` with traefik |

---

## 5. Content quality sample

```yaml
# _general/education/lesson-analysis-system.md
title: Lesson Analysis System
type: entity
confidence: 0.8
sources: 1
tags: [education, media-processing, data-normalization, audio-video-sync]
provenance: ^[raw/_general/Уточненное описание.docx]  # present on every claim
```

**Observations:**
- All pages have `^[raw/...]` provenance markers ✅
- All pages have tags (3–4 each) ✅
- Content is in Russian with English technical terms ✅
- Frontmatter complete on all pages ✅

---

## 6. Recommendations (priority order)

1. **P0** — Fix index trim to enforce limit strictly (cut at char boundary, not section boundary)
2. **P1** — Investigate cross-reference hallucinations (prompt fix or post-generation filter)
3. **P1** — Start accumulating skills via conflict resolutions (seed from past audits)
4. **P2** — Vary confidence in prompt (do not allow default 0.8 for every page)
5. **P2** — Flag first-batch pages as `needs_review: true` for human validation
