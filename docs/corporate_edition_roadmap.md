# Corporate Edition Roadmap

Date: 2026-06-10

Status: roadmap for paid pilots and enterprise adaptation.

## 1. Positioning

Enterprise Knowledge Compiler has two tracks:

1. **Open-source edition**: public repository for personal knowledge work, local demos, private server deployment, and small multi-user experiments.
2. **Corporate implementation**: paid pilot and enterprise rollout based on the same architecture, extended for large document volumes, regulated environments, customer infrastructure, corporate security, and operational governance.

The corporate implementation should not be described as a finished shrink-wrapped SaaS product. The honest message is:

> The open-source product demonstrates the core knowledge-compilation approach. Corporate deployments are delivered as paid pilots and adapted to the customer's documents, infrastructure, security model, language requirements, and operating processes.

## 2. Core promise

Most enterprise AI initiatives are limited not only by model quality, but by the state of corporate knowledge:

- duplicate documents;
- outdated versions;
- contradictory policies and procedures;
- unclear source of truth;
- weak provenance;
- poor suitability for AI agents;
- no review workflow for semantic changes.

The corporate implementation turns fragmented documents into an AI-ready knowledge layer:

```text
raw documents
  -> source registry
  -> chunk and section analysis
  -> claims and provenance
  -> conflict detection
  -> reviewed wiki pages
  -> typed relationships
  -> queryable knowledge layer
  -> assistants and AI agents
```

## 3. Public vs corporate boundary

| Capability | Open-source edition | Corporate implementation |
|---|---|---|
| Local document ingest | Included | Included |
| PDF/DOCX/PPTX conversion | Included | Hardened and monitored |
| Wiki page generation | Included | Included with review workflow |
| Conflict detection | Included | Extended with expert assignment and workflow |
| Skills and rules | Included | Governance rules, approval, audit |
| Multi-user mode | SQLite MVP | PostgreSQL control plane |
| Workspace isolation | Basic | Organization/team/project model |
| Authentication | Basic Auth / Bearer tokens | SSO/OIDC/SAML, enterprise identity integration |
| Authorization | Basic workspace isolation | RBAC, team permissions, document scopes |
| Audit trail | Limited | Full audit logs and compliance reports |
| Large document volume | Partially implemented | Required production capability |
| Claims layer | Architecture and implementation base | First-class persisted artifact |
| Source drift | Architecture and partial support | Production source registry and drift dashboard |
| Connectors | Manual upload / raw folder | SharePoint, Google Drive, Confluence, Jira, DMS/ECM, file shares, object storage |
| Operational deployment | Docker/local | Customer infra, private cloud, regional cloud, backups, observability |

## 4. Corporate pilot offer

### 4.1 Goal

Deploy a working demonstrator on a limited customer corpus and prove that the knowledge-compilation approach can:

- structure documents into a navigable knowledge base;
- reveal contradictions and duplicates;
- preserve provenance to sources;
- support expert review;
- answer questions with citations;
- prepare the customer for AI assistants and agents.

### 4.2 Recommended pilot scope

Duration: 6-10 weeks.

Input corpus:

- 300-3,000 documents, or
- one bounded business domain, such as policies, engineering procedures, compliance documentation, project documentation, product knowledge, or operating manuals.

Recommended industries:

- government and semi-government organizations;
- EPC, construction, infrastructure, and engineering companies;
- energy, oil and gas, utilities;
- banking, insurance, fintech compliance;
- large holdings and portfolio organizations;
- universities and research institutions.

### 4.3 Pilot deliverables

- deployed pilot instance;
- configured LLM endpoint, provided by customer or billed separately;
- document ingest pipeline for pilot corpus;
- generated knowledge wiki;
- source cards and provenance links;
- conflict and duplicate report;
- expert review workflow prototype;
- query assistant with citations;
- pilot evaluation report;
- rollout backlog and enterprise architecture proposal.

### 4.4 Commercial model

The pilot is paid. Customer infrastructure and LLM usage are either provided by the customer or billed separately.

Recommended pricing logic:

- fixed-fee pilot for adaptation, deployment, configuration, evaluation, and reporting;
- infrastructure, cloud, GPUs, API tokens, commercial LLM usage, and third-party software are excluded unless explicitly included;
- additional connectors and enterprise security integrations are separate work packages;
- production rollout is estimated after the pilot based on actual corpus, users, security requirements, and integration scope.

## 5. Technical workstreams

### Phase 0: Public demo hardening

Goal: make the open-source demonstrator credible and stable.

Work:

- update README and public positioning;
- create public English demo dataset;
- create demo script and screenshots;
- ensure local and Docker startup are reliable;
- prepare sample questions and expected outputs;
- mark known limitations honestly.

Acceptance:

- new user can run the demo locally;
- public demo shows ingest, generated pages, conflicts, provenance, and Q&A;
- no enterprise-only claims are presented as already available in the public edition.

### Phase 1: Large-source reliability

Goal: remove silent knowledge loss and make large documents safe.

Work:

- ensure large-source ingest uses outline/section-aware chunking;
- eliminate page generation based on arbitrary truncated global context;
- persist chunk outcomes;
- improve nested section retrieval;
- make failed chunks visible and retryable;
- add tests for 1M char source and documents with headings, tables, code blocks, and long sections.

Acceptance:

- no source section is silently dropped;
- page generation is based on selected sections/chunks, not arbitrary first-N characters;
- failed chunks are reported;
- answers can retrieve facts from middle and end of long pages.

### Phase 2: Claims and source registry

Goal: make provenance and source-of-truth logic production-worthy.

Work:

- persist claims as first-class artifacts;
- connect claims to source cards and generated pages;
- deduplicate claims;
- track claim status: active, superseded, contradicted, unresolved, ignored;
- expose claim/source provenance in UI and API;
- detect source drift with sha256 and show affected pages/claims.

Acceptance:

- each factual page section can be traced to source and claim;
- duplicate claims are not written repeatedly;
- changed source files trigger drift status;
- contradictions can be traced to exact claims and source sections.

### Phase 3: Review workflow

Goal: prevent large semantic changes from being applied silently.

Work:

- enforce review thresholds;
- route large updates to drafts;
- show deterministic diffs before apply;
- separate safe auto-fixes from expert review changes;
- support reviewer comments and approve/reject decisions;
- record review events.

Acceptance:

- semantic page rewrites above threshold require review;
- arbitrary LLM patches are rejected;
- safe technical fixes can be applied automatically;
- expert decisions are auditable.

### Phase 4: Corporate control plane

Goal: replace SQLite MVP control plane with enterprise-grade PostgreSQL.

Work:

- introduce PostgreSQL schema for users, organizations, teams, workspaces, roles, permissions, jobs, usage, audit events, and connector state;
- keep the knowledge layer filesystem/object-storage friendly;
- define migration path from SQLite demo mode;
- add job queue for ingest/rebuild/audit;
- add background workers and job status API;
- add backup and restore strategy.

Acceptance:

- PostgreSQL stores control metadata only, not generated knowledge pages unless explicitly required by deployment;
- all long-running operations run as jobs;
- job status, errors, and retries are visible;
- audit events are written for sensitive operations.

### Phase 5: Enterprise security

Goal: make the system acceptable for regulated and document-heavy organizations.

Work:

- SSO/OIDC/SAML integration;
- RBAC with organization, workspace, project, and document scopes;
- audit log for login, ingest, query, export, review, delete, and admin actions;
- encryption and secret management strategy;
- deployment behind customer reverse proxy;
- data residency and no-training assurances depending on selected LLM provider;
- security runbook.

Acceptance:

- users authenticate through customer identity provider;
- permissions are enforced at API and workspace layers;
- audit log is queryable and exportable;
- secrets are not stored in source control or exposed through settings APIs.

### Phase 6: Connectors and ingestion operations

Goal: move from manual upload to repeatable corporate ingestion.

Connector priority:

1. SharePoint / OneDrive
2. Google Drive
3. Confluence
4. Jira
5. SMB/SFTP/file shares
6. S3-compatible object storage
7. DMS/ECM through customer API

Acceptance:

- sources can be synchronized repeatedly;
- changed/deleted files are detected;
- source drift is visible;
- connector failures do not corrupt the knowledge layer.

### Phase 7: Domain packs and Middle East readiness

Goal: make the product easy to demonstrate and adapt in GCC markets.

Work:

- English demo pack;
- optional Arabic/English bilingual demo pack;
- domain templates for EPC, compliance, government services, and operations;
- sample KPIs and pilot evaluation criteria;
- deployment narrative for on-prem/private cloud/regional cloud;
- sales deck and one-page pilot offer.

Acceptance:

- demo can be shown without customer confidential data;
- demo includes contradictions, duplicate policy versions, provenance, and review;
- pilot proposal clearly separates implementation fee from infrastructure and LLM usage.

## 6. Demo dataset direction

Recommended public demo options:

1. **AI governance and risk management documents**: good fit for corporate AI buyers, policy compilation, risk controls, source-of-truth, and conflicts.
2. **Infrastructure, construction, and safety documents**: good fit for EPC and government buyers, procedures, requirements, versions, and compliance.
3. **Sustainability / ESG / climate reporting documents**: useful for strategy and reporting demos.
4. **Public government service guides**: useful for citizen-service assistant demos and future bilingual scenarios.

Recommended first demo: public AI governance and corporate policy documents. It is directly relevant to AI transformation buyers and easier to parse than engineering drawings or highly formatted technical PDFs.

Recommended second vertical demo: EPC/infrastructure documentation.

## 7. Naming

Recommended repository names:

1. `knowledge-compiler` — best open-source name: short, broad, clean.
2. `enterprise-knowledge-compiler` — best commercial clarity, but too enterprise-heavy for public open source.
3. `ai-knowledge-compiler` — clear, but slightly generic.
4. `knowledge-layer-compiler` — architectural, less memorable.
5. `corp-knowledge-compiler` — too narrow.

Recommendation:

- public repository: `knowledge-compiler`;
- commercial product/deck: **Enterprise Knowledge Compiler**;
- company offering: **Kulinich.AI Enterprise Knowledge Compiler**.

Renaming the GitHub repository is preferable to recreating it. GitHub redirects old links after rename, while keeping stars, issues, and history.

## 8. Open decisions

- exact pilot price bands by geography and industry;
- whether corporate implementation lives in the same repo as private modules or in a separate private repository;
- exact PostgreSQL schema and migration strategy;
- whether object storage becomes mandatory for corporate raw files;
- first public demo domain;
- whether Arabic support is included in the first GCC demo or positioned as Phase 2.
