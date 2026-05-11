# Test Source 01: EYWA Baseline Architecture

## Purpose

This document is a controlled test input for Wiki Engine ingest. It describes the initial baseline for the fictional EYWA residential AI assistant project.

Use this document first.

Expected project for upload: `eywa-demo`

---

## Source Content

EYWA is a residential AI assistant for a premium apartment complex. The first MVP focuses on a mobile and web interface, basic voice interaction, resident FAQ, and simple concierge request creation.

The MVP must support three languages from day one: Russian, English, and Arabic. Users may switch language manually in the interface. Automatic language detection is planned for later releases, but it is not required for the first MVP.

The system stores resident operational data on customer-controlled infrastructure. The preferred deployment model is an on-premise or customer-controlled server environment. External cloud services may be used only for optional integrations and must not become the primary storage location for resident data.

The architecture uses FastAPI for backend services, React for the web interface, PostgreSQL for structured data, and pgvector for knowledge-base retrieval experiments. Docker Compose is used for local deployment and pilot infrastructure setup.

Voice input is handled by mobile platform speech recognition where possible. The server generates assistant responses and may provide streaming text and voice output. The target user experience is a first visible response within 1.5 seconds for common FAQ questions.

The concierge module creates resident requests for building management. In the MVP, the integration is basic: requests are created with category, description, apartment number, contact phone, priority, and status. Complex SLA workflows and vendor assignment are planned for later releases.

The FAQ assistant answers questions about building rules, services, payments, access, parking, and common resident procedures. Answers should cite internal knowledge-base pages whenever possible.

The first MVP does not include marketplace ordering, payment processing, biometric access control, or deep integration with all engineering systems. These capabilities are planned for later phases.

Security expectations for the MVP include protected admin access, separation of resident and admin views, audit logging for request status changes, and careful handling of personal data. Full enterprise-grade security hardening is planned after pilot validation.

---

## Expected Behavior After Ingest

### Expected created or updated wiki pages

The engine should create or update pages similar to:

- `eywa-demo/architecture/overview` or similar architecture overview page;
- `eywa-demo/features/voice-interface` or similar voice feature page;
- `eywa-demo/features/concierge-module` or similar concierge page;
- `eywa-demo/features/faq-assistant` or similar FAQ/RAG page;
- `eywa-demo/deployment/customer-controlled-infrastructure` or similar deployment page.

Exact slugs may differ, but the generated structure should clearly separate architecture, features, deployment, and scope boundaries.

### Expected metadata

Generated pages should have:

- project: `eywa-demo`;
- confidence above 0.75 for directly stated facts;
- source reference to this document;
- page type such as `entity` or `concept`.

### Expected cross-links

The generated pages should link related concepts, for example:

- voice interface linked to architecture overview;
- concierge module linked to MVP scope;
- FAQ assistant linked to knowledge base or retrieval;
- deployment linked to data control and security.

### Expected conflicts

No conflicts should be created from this document alone.

### Expected queries

After ingest, these queries should be answerable:

1. What technologies are used in the EYWA MVP architecture?
2. Which languages are required from day one?
3. What is excluded from the first MVP?
4. Where should resident operational data be stored?
5. What data fields are created by the concierge module?

Expected answer style:

- answer in Russian;
- cite generated wiki pages using `[[slug]]`;
- do not invent features not present in this source.
