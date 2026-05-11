# Test Source 03: AURORA Assistant Cross-Project Comparison

## Purpose

This document is a controlled test input for cross-project behavior. It describes a second fictional assistant project named AURORA.

Use this document third, after both EYWA documents have been ingested.

Expected project for upload: `aurora-demo`

This document intentionally overlaps with EYWA topics but should not be treated as a direct conflict in most cases, because it belongs to another project.

---

## Source Content

AURORA is an internal AI assistant for a logistics company. The first MVP is designed for operations managers, dispatchers, and analysts who need fast access to transport procedures, route exceptions, and internal policy explanations.

Unlike EYWA, AURORA is not a residential assistant and does not include resident services, concierge requests, apartment data, or building management workflows.

The first AURORA MVP is web-only. Mobile applications are not included in the first release because the pilot users work mainly from desktop computers in the operations center.

The MVP must support Russian only. English support may be added later for international logistics teams, but it is not part of the first release.

The backend uses FastAPI. PostgreSQL is used as the main structured database from the beginning because operational records, user roles, query logs, and document metadata must be stored reliably. SQLite is not approved for the pilot.

The knowledge base is built from curated markdown and PDF documents. Uploaded documents include operating procedures, route exception rules, safety instructions, and internal policy notes. The assistant must cite internal wiki pages when answering operational questions.

Deployment is planned on customer-controlled infrastructure from the first pilot. The logistics company does not allow vendor-managed cloud deployment for internal operational documents.

Voice interaction is explicitly excluded from the first MVP. The first version supports only text chat and document search.

The MVP includes role-based access for operations manager, dispatcher, analyst, and administrator. Users see only documents and answers allowed for their role.

The assistant must never create real transport orders, approve route changes, or change operational systems directly. It can explain procedures and prepare draft recommendations, but final decisions remain with human managers.

---

## Expected Behavior After Ingest

### Expected created pages

The engine should create pages under project `aurora-demo`, not under `eywa-demo`.

Possible pages:

- `aurora-demo/architecture/overview`;
- `aurora-demo/scope/mvp-scope`;
- `aurora-demo/deployment/customer-controlled-infrastructure`;
- `aurora-demo/features/role-based-access`;
- `aurora-demo/features/knowledge-base`;
- `aurora-demo/limitations/no-operational-actions`.

Exact slugs may differ.

### Expected cross-project behavior

The engine may notice similarities with EYWA:

- both use FastAPI;
- both have web MVP discussion;
- both discuss deployment model;
- both discuss voice exclusion or inclusion;
- both discuss knowledge-base answers and citations.

However, these should generally be treated as **cross-project differences**, not factual contradictions, because they belong to different projects.

Expected examples:

- EYWA may have Russian/English/Arabic or Russian/English depending on version; AURORA has Russian only.
- EYWA concerns residents and concierge; AURORA concerns logistics operations.
- EYWA may have vendor-managed pilot cloud in revised scope; AURORA requires customer-controlled infrastructure from first pilot.
- EYWA has no role model as detailed as AURORA; AURORA requires role-based access.

### Expected conflicts

No direct conflict should be created merely because AURORA differs from EYWA.

Acceptable behavior:

- create cross-project comparison notes;
- create cross-links if the engine supports them;
- mark differences as `cross_project_difference` only if conflict system explicitly distinguishes it from actual contradiction.

Unacceptable behavior:

- overwriting EYWA pages with AURORA requirements;
- claiming EYWA is a logistics assistant;
- claiming AURORA has concierge requests;
- treating Russian-only AURORA scope as contradiction of EYWA multilingual scope.

### Expected queries

After ingest, these queries should be answerable:

1. What is AURORA and who uses it?
2. What actions is AURORA not allowed to perform?
3. Does AURORA use PostgreSQL or SQLite?
4. Compare EYWA and AURORA MVP scope.
5. Which project requires customer-controlled infrastructure from the first pilot?
6. Which project includes concierge requests?

Expected answer style:

- answer in Russian;
- clearly separate `eywa-demo` and `aurora-demo`;
- cite pages from both projects for comparison questions;
- do not merge project assumptions;
- if EYWA has unresolved conflicts, mention uncertainty for EYWA instead of treating old and new facts as one final truth.

### Expected search behavior

Search for `FastAPI` should return pages from both projects.

Search filtered to `aurora-demo` should return only AURORA pages.

Search filtered to `eywa-demo` should not return AURORA-only pages.

### Expected project selector behavior

If the project selector UI is implemented:

- upload should offer `eywa-demo`, `aurora-demo`, and `_general` after documents are loaded;
- search should allow selecting only `aurora-demo`;
- search should allow selecting both `eywa-demo` and `aurora-demo` for comparison.
