# Test Source 02: EYWA Revised Requirements with Intentional Conflicts

## Purpose

This document is a controlled test input for Wiki Engine conflict detection. It intentionally contradicts parts of `01_eywa_baseline_source.md`.

Use this document second, after the baseline EYWA document has already been ingested into project `eywa-demo`.

Expected project for upload: `eywa-demo`

---

## Source Content

The updated EYWA MVP requirements change several earlier assumptions. The first release is now planned as a web-only pilot for the building management team and selected residents. Native mobile applications are no longer included in the first MVP. Mobile applications may be considered after successful web pilot validation.

The MVP language scope is reduced. The first release must support Russian and English only. Arabic is moved to a later release and is no longer mandatory for day-one launch.

Deployment is also changed. The pilot may run in a managed cloud environment controlled by the vendor for the first three months. Customer-controlled infrastructure is still the target production model, but it is not required for the pilot MVP.

The backend stack remains FastAPI. However, the revised proposal uses SQLite for the first pilot instead of PostgreSQL. PostgreSQL is planned only after the pilot if the number of residents exceeds 500 active users.

The voice interface is removed from the first MVP. Voice input and voice output are now classified as phase-two features. The first MVP focuses only on text chat and admin-managed FAQ content.

The concierge module is also simplified. Instead of creating real service requests in the building management system, the MVP stores concierge requests as internal draft tickets visible only in the admin panel. The fields are: category, resident comment, apartment number, and status. Contact phone and priority are not required in the first MVP.

The FAQ assistant remains in scope, but it must answer only from curated admin-approved FAQ entries. It must not answer from unapproved resident documents or uploaded drafts.

Marketplace ordering, payment processing, biometric access control, and deep engineering integrations remain out of scope for the first MVP.

---

## Expected Behavior After Ingest

### Expected conflicts

The engine should detect several conflicts or version mismatches against the baseline document.

Expected conflict areas:

1. **Platform scope conflict**
   - Baseline: mobile and web interface are included in MVP.
   - Revised: first MVP is web-only; native mobile apps are postponed.

2. **Language scope conflict**
   - Baseline: Russian, English, and Arabic are required from day one.
   - Revised: only Russian and English are required; Arabic postponed.

3. **Deployment model conflict**
   - Baseline: on-premise or customer-controlled infrastructure preferred for MVP.
   - Revised: pilot may run in vendor-managed cloud for first three months.

4. **Database technology conflict**
   - Baseline: PostgreSQL and pgvector are used.
   - Revised: SQLite is used for pilot; PostgreSQL later.

5. **Voice scope conflict**
   - Baseline: basic voice interaction is part of MVP.
   - Revised: voice is removed from first MVP and moved to phase two.

6. **Concierge data model conflict**
   - Baseline: category, description, apartment number, contact phone, priority, status.
   - Revised: category, resident comment, apartment number, status only.

### Expected page update behavior

Existing EYWA pages should not be silently overwritten without a visible draft or diff if the safe update workflow is implemented.

If the current engine still writes directly, check carefully whether older facts were removed or mixed without clear conflict tracking.

### Expected generated pages

The engine may create or update pages such as:

- `eywa-demo/scope/mvp-scope`;
- `eywa-demo/deployment/pilot-cloud-deployment`;
- `eywa-demo/features/faq-assistant`;
- `eywa-demo/features/concierge-module`.

Exact slugs may differ.

### Expected cross-links

New or updated pages should link back to earlier architecture, FAQ, concierge, deployment, and MVP scope pages.

### Expected queries

After ingest, these queries should expose uncertainty or conflict:

1. Is mobile included in the EYWA MVP?
2. Which languages are required for day-one launch?
3. Is voice part of the first MVP?
4. Does the pilot use PostgreSQL or SQLite?
5. Is customer-controlled infrastructure mandatory for the pilot?

Expected answer style:

- do not present one answer as final if conflict exists;
- mention that documents disagree;
- cite both old and revised wiki pages if possible;
- answer in Russian;
- point to open conflicts if the UI exposes them.

### Expected conflict resolution test

Resolve one conflict manually with this rule:

`For EYWA MVP scope, the revised requirements document supersedes the baseline document for pilot-stage platform, language, voice, database, and deployment decisions. The baseline remains useful for later production target architecture.`

Expected result:

- conflict status changes to resolved;
- a reusable skill/rule is added or previewed;
- future answers should distinguish pilot MVP from production target.
