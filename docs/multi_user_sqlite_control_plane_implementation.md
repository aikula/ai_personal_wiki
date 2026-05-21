# Multi-User SQLite Control Plane Implementation Plan

Дата: 2026-05-21

Связанный документ: `docs/multi_user_sqlite_control_plane_spec.md`.

## 1. Migration Plan

### Phase 1: Runtime Context

Work:

- add `AppMode`, `ControlSettings`, `MultiUserSettings`;
- add `WorkspaceContext`;
- make `get_wiki_fs()` use workspace context;
- keep personal behavior unchanged by default.

Acceptance:

- existing tests pass unchanged;
- personal mode still reads/writes `./wiki-data`;
- no route constructs `WikiFS(settings)` directly except dependencies/tests.

Checklist:

- [ ] Settings tests for all env overrides.
- [ ] Workspace context tests for all modes.
- [ ] Existing API tests still pass in personal mode.

### Phase 2: SQLite ControlStore

Work:

- add migrations;
- implement SQLite connection factory with WAL/busy_timeout/foreign_keys;
- implement `SQLiteControlStore`;
- implement `NoopControlStore`.

Acceptance:

- migrations are idempotent;
- SQLite file is created automatically in multi-user mode;
- users/workspaces/credits can be created and read;
- personal modes do not require SQLite file.

Checklist:

- [ ] Migration tests on empty DB.
- [ ] Migration idempotency test.
- [ ] ControlStore user/session/workspace tests.
- [ ] Foreign key behavior test.

### Phase 3: Multi-User Auth

Work:

- add auth routes;
- add password hashing;
- add current user dependency;
- add session token validation.

Acceptance:

- register creates user, default workspace, daily bucket, welcome bucket;
- login returns valid token;
- `/api/auth/me` returns current user and workspace;
- invalid token returns `401`;
- inactive user cannot authenticate.

Checklist:

- [ ] Register test.
- [ ] Duplicate email test.
- [ ] Login success/failure tests.
- [ ] Authenticated route test.
- [ ] Password hash is not returned.

### Phase 4: Workspace Isolation

Work:

- resolve workspace from current user;
- route all `WikiFS` creation through workspace context;
- create workspace filesystem skeleton on registration.

Acceptance:

- user A cannot read user B workspace;
- uploaded documents land under correct workspace;
- chat/search/wiki tree only sees current workspace;
- path traversal attempts fail.

Checklist:

- [ ] Two-user isolation API test.
- [ ] Upload isolation test.
- [ ] Wiki tree isolation test.
- [ ] Raw source path traversal regression test.

### Phase 5: Metered LLM Client

Work:

- add `MeteredLLMClient`;
- wrap all agent LLM calls through dependency;
- record usage events;
- consume quota buckets.

Acceptance:

- chat consumes tokens;
- ingest consumes tokens for each LLM call;
- failed LLM call does not consume tokens;
- quota exceeded blocks before provider call;
- usage endpoint returns current buckets and recent events.

Checklist:

- [ ] Unit tests for daily/welcome consumption order.
- [ ] Daily lazy reset test.
- [ ] Metered successful call test.
- [ ] Metered provider failure test.
- [ ] Quota exceeded test.
- [ ] Usage API test.

### Phase 6: UI

Work:

- add login/register screens for multi-user;
- store token securely enough for MVP;
- add usage indicator;
- handle quota exceeded API errors.

Acceptance:

- unauthenticated multi-user visitor sees login/register;
- authenticated user sees own wiki;
- usage values refresh after chat/ingest;
- quota exceeded error is visible and understandable.

Checklist:

- [ ] Playwright login flow.
- [ ] Playwright upload flow.
- [ ] Playwright quota exceeded state.
- [ ] Personal mode UI regression check.

## 2. Testing Strategy

Required test groups:

- settings and env;
- SQLite migrations;
- ControlStore contract;
- auth routes;
- workspace isolation;
- metered LLM;
- quota buckets;
- existing wiki routes in personal mode;
- existing wiki routes in multi-user mode;
- ingest/chat token consumption.

Use mock LLM for all quota tests. Do not call real providers in tests.

## 3. Acceptance Criteria

Global acceptance:

- `personal_local` works without auth and without SQLite.
- `personal_server` works with current Basic Auth.
- `multi_user` requires account auth.
- each user gets isolated workspace files.
- all wiki features use the same agents/core code.
- token usage is recorded for chat and ingest.
- daily and welcome limits are enforced.
- daily limit resets lazily.
- no user can access another user's wiki data.
- all tests pass.

Performance acceptance for SQLite MVP:

- WAL enabled;
- no long-running transaction wraps LLM calls;
- usage recording uses short transactions;
- concurrent requests fail gracefully with busy timeout, not DB corruption.

## 4. Design Constraints

- Do not fork agents into personal and multi-user variants.
- Do not add user_id parameters throughout business logic when dependency
  context can resolve workspace root.
- Do not store wiki pages in SQLite.
- Do not let UI-only checks enforce quota; backend must enforce.
- Do not call LLM directly in agents once `MeteredLLMClient` exists.
- Do not expose filesystem paths or workspace root paths in public API.

## 5. Recommended First Task

Start with Phase 1 and Phase 2 together:

1. Add settings and `WorkspaceContext`.
2. Add `ControlStore` interface.
3. Add SQLite migration runner.
4. Keep personal mode default and green.

Reason: after workspace context and control store exist, auth, quotas and UI can
be added incrementally without rewriting wiki agents.
