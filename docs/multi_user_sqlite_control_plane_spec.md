# Multi-User SQLite Control Plane Specification

Дата: 2026-05-21

Статус: спецификация для кодового агента на реализацию единой кодовой базы для
персональной wiki и серверной многопользовательской версии.

## 1. Цель

Сохранить одну кодовую базу Wiki Engine, которая запускается в трех режимах:

```text
APP_MODE=personal_local
APP_MODE=personal_server
APP_MODE=multi_user
```

Режимы:

- `personal_local`: локальная персональная wiki без auth и без control DB.
- `personal_server`: персональная wiki на VPS с optional Basic Auth.
- `multi_user`: серверная версия с аккаунтами, личными workspace, SQLite
  control plane, лимитами и учетом токенов.

Главный принцип: wiki-контент остается plain-text filesystem-first. SQLite
используется только как control plane.

## 2. Non-Goals

- Не переносить wiki pages, raw documents, conflicts, skills или Source Cards в
  SQLite.
- Не добавлять PostgreSQL в MVP.
- Не делать billing/payments в первом этапе.
- Не делать organization/team workspaces в первом этапе.
- Не делать shared public wiki между пользователями.
- Не ломать текущие API personal режима без необходимости.

## 3. Architecture

### 3.1 Data Plane

Wiki data остается файлами:

```text
data/workspaces/<workspace_id>/
  raw/
  wiki/
  conflicts.md
  skills.md
  AGENTS.md
  drafts/
```

В personal modes используется текущий путь:

```text
wiki-data/
```

В multi-user mode каждый workspace получает отдельный root:

```text
WIKI_WORKSPACES_ROOT/data/workspaces/<workspace_id>
```

`WikiFS` не должен знать про пользователей. Он получает только root path.

### 3.2 Control Plane

SQLite хранит только управление:

- users;
- auth sessions;
- workspaces;
- usage events;
- credit buckets;
- daily quota reset state;
- optional admin flags.

Файл:

```text
CONTROL_DB_URL=sqlite:///data/control.db
```

Обязательные SQLite PRAGMA:

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;
```

### 3.3 Dependency Flow

API dependencies должны собирать request context так:

```text
Request
  -> Settings
  -> AppMode
  -> CurrentUser optional
  -> WorkspaceContext
  -> WikiFS(workspace_root)
  -> LLMClient or MeteredLLMClient
  -> Agent
```

Agents не должны знать про HTTP auth. Agents могут получать metered LLM client,
но не должны напрямую читать SQLite.

## 4. Configuration

Добавить settings:

```yaml
app:
  mode: "personal_local"  # personal_local | personal_server | multi_user

control:
  db_url: "sqlite:///data/control.db"
  workspaces_root: "data/workspaces"

multi_user:
  registration_enabled: true
  default_daily_tokens: 30000
  default_welcome_tokens: 200000
  daily_reset_timezone: "UTC"
```

Env overrides:

```text
APP_MODE
CONTROL_DB_URL
WIKI_WORKSPACES_ROOT
REGISTRATION_ENABLED
DEFAULT_DAILY_TOKENS
DEFAULT_WELCOME_TOKENS
DAILY_RESET_TIMEZONE
```

Existing:

```text
WIKI_AUTH_ENABLED
WIKI_AUTH_USERNAME
WIKI_AUTH_PASSWORD
```

Rules:

- `personal_local`: auth ignored unless explicitly enabled.
- `personal_server`: Basic Auth may be enabled; no user accounts.
- `multi_user`: Basic Auth is not the user system; account auth is required.
- `multi_user` must fail fast if SQLite init/migration fails.

## 5. Core Types

### 5.1 AppMode

```python
AppMode = Literal["personal_local", "personal_server", "multi_user"]
```

### 5.2 CurrentUser

```python
@dataclass
class CurrentUser:
    user_id: str
    email: str
    is_admin: bool
    is_active: bool
```

### 5.3 WorkspaceContext

```python
@dataclass
class WorkspaceContext:
    workspace_id: str
    owner_user_id: str | None
    mode: AppMode
    wiki_data_path: Path
    quota_subject_id: str | None
```

Personal modes:

```text
workspace_id = "local"
owner_user_id = None
wiki_data_path = settings.wiki_data_path
quota_subject_id = None
```

Multi-user:

```text
workspace_id = user's default workspace id
owner_user_id = current_user.user_id
wiki_data_path = workspaces_root / workspace_id
quota_subject_id = current_user.user_id
```

## 6. SQLite Schema

Use migrations, not ad hoc table creation in route code. Minimal acceptable
implementation: idempotent migration runner with numbered SQL files.

### 6.1 users

```sql
CREATE TABLE users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  is_admin INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_login_at TEXT
);
```

### 6.2 sessions

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revoked_at TEXT
);
```

### 6.3 workspaces

```sql
CREATE TABLE workspaces (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  root_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(owner_user_id, slug)
);
```

### 6.4 usage_events

```sql
CREATE TABLE usage_events (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  operation TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  total_tokens INTEGER NOT NULL,
  is_estimated INTEGER NOT NULL DEFAULT 1,
  request_id TEXT,
  created_at TEXT NOT NULL
);
```

Allowed operations:

```text
chat | ingest | rebuild | audit | settings_test
```

### 6.5 credit_buckets

```sql
CREATE TABLE credit_buckets (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  bucket_type TEXT NOT NULL,
  token_limit INTEGER NOT NULL,
  tokens_used INTEGER NOT NULL DEFAULT 0,
  reset_at TEXT,
  expires_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

Allowed bucket types:

```text
daily | welcome
```

Rule: spend daily bucket first, then welcome bucket.

## 7. ControlStore Interface

Add an interface in `app/core/control_store.py`:

```python
class ControlStore:
    def get_user_by_email(self, email: str) -> UserRecord | None: ...
    def create_user(self, email: str, password: str) -> UserRecord: ...
    def verify_password(self, email: str, password: str) -> UserRecord | None: ...
    def create_session(self, user_id: str) -> str: ...
    def get_user_by_session_token(self, token: str) -> UserRecord | None: ...
    def revoke_session(self, token: str) -> None: ...
    def get_default_workspace(self, user_id: str) -> WorkspaceRecord: ...
    def create_default_workspace(self, user_id: str) -> WorkspaceRecord: ...
    def get_credit_state(self, user_id: str) -> CreditState: ...
    def consume_tokens(self, user_id: str, amount: int) -> CreditState: ...
    def record_usage(self, event: UsageEvent) -> None: ...
```

Implementations:

- `NoopControlStore`: personal modes.
- `SQLiteControlStore`: multi-user MVP.

No route should execute raw SQL directly.

## 8. Authentication

### 8.1 Personal Server Auth

Keep existing Basic Auth for `personal_server`:

- one username/password from env;
- no sessions;
- no users table;
- useful for private VPS.

### 8.2 Multi-User Auth

Add account auth endpoints:

```text
POST /api/auth/register
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
```

Password requirements:

- hash with `argon2` preferred; `bcrypt` acceptable;
- never log password;
- never return password hash;
- email normalized to lowercase.

Session transport:

- MVP: bearer token is acceptable.
- Better: httpOnly secure cookie for browser app.

Acceptance for MVP can use bearer tokens if tests cover it.

## 9. Workspace Isolation

Every multi-user request must resolve workspace before creating `WikiFS`.

Rules:

- user can only access own workspace;
- workspace root path must be created from server-side workspace id, never from
  user-submitted path;
- `../` and path traversal must be impossible at workspace resolution layer;
- all existing wiki routes operate against resolved workspace root.

API routes do not need duplicated multi-user versions. Existing routes should
continue to call `get_wiki_fs()`, but `get_wiki_fs()` becomes context-aware.

## 10. Token Metering

Wrap LLM calls:

```text
Agent -> MeteredLLMClient -> LLMClient
```

`MeteredLLMClient.call()` responsibilities:

1. Estimate input tokens from system + prompt.
2. Reserve or check quota before LLM call.
3. Call underlying `LLMClient`.
4. Read provider usage if available.
5. Fall back to local estimate if provider usage missing.
6. Record `usage_events`.
7. Consume tokens from credit buckets.

Token estimate may use existing token/char utilities initially.

Failure behavior:

- if quota insufficient before call: return API error `402` or `429`;
- if provider call fails: do not consume tokens;
- if recording usage fails in multi-user mode: fail request, because quota state
  must stay trustworthy.

## 11. Limits

Two default buckets per new user:

```text
daily:
  token_limit = DEFAULT_DAILY_TOKENS
  tokens_used = 0
  reset_at = next daily reset

welcome:
  token_limit = DEFAULT_WELCOME_TOKENS
  tokens_used = 0
  reset_at = null
  expires_at = optional null for MVP
```

Daily reset:

- lazy reset on first quota check after `reset_at`;
- no cron required for MVP;
- reset sets `tokens_used=0` and advances `reset_at`.

Consumption:

- spend daily first;
- if daily remaining insufficient, spend remainder from welcome;
- if combined remaining insufficient, reject before LLM call.

## 12. API Additions

### 12.1 Account

```text
POST /api/auth/register
Request: {email, password}
Response: {user, token}

POST /api/auth/login
Request: {email, password}
Response: {user, token}

POST /api/auth/logout
Response: {ok: true}

GET /api/auth/me
Response: {user, workspace, credits}
```

### 12.2 Usage

```text
GET /api/usage/me
Response:
{
  "daily": {"limit": int, "used": int, "remaining": int, "reset_at": str},
  "welcome": {"limit": int, "used": int, "remaining": int},
  "recent_events": [...]
}
```

## 13. UI Requirements

For multi-user mode:

- login/register screen;
- user menu with email;
- usage indicator with daily and welcome remaining;
- clear error when limit is exceeded;
- existing wiki UI should otherwise behave the same.

For personal modes:

- no login UI in `personal_local`;
- Basic Auth browser prompt is acceptable in `personal_server`;
- no usage panel unless local metering is explicitly enabled later.

## 14. Implementation Details

Detailed phases, tests, checklists, global acceptance criteria and first-task
sequence are in
`docs/multi_user_sqlite_control_plane_implementation.md`.
