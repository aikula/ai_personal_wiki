# Tech Debt / Backlog

## Ingest
- [ ] **Auto-ingest on upload** — checkbox in UI to immediately process uploaded files and add to wiki without full rebuild
- [ ] **Deduplication** — check uploaded files against existing raw files:
  - Replace if checksum differs (file was updated)
  - Ignore if checksums match (file already exists, unchanged)
  - Skip if content is similar but not identical (potential duplicate, flag for review)
