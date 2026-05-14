# Simple Auth Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add minimal email/password login and registration, allowing only QQ and 163 email addresses, and isolate monitor accounts and dashboard data per user.

**Architecture:** Add a local `MonitorUser` table plus signed cookie sessions. Add `owner_user_id` to `MonitorAccount`, require auth for monitor APIs, and filter account-scoped queries to the current user. The static monitor page shows a compact auth panel until `/api/auth/me` succeeds.

**Tech Stack:** FastAPI, SQLAlchemy async sessions, Pydantic, Python stdlib password hashing and HMAC signing, static HTML/JS, pytest/httpx.

---

### Task 1: Auth Model And Service

**Files:**
- Modify: `database/models.py`
- Create: `api/services/auth.py`
- Create: `api/routers/auth.py`
- Modify: `api/main.py`
- Test: `tests/test_monitor_auth.py`

- [ ] Add `MonitorUser` model with email, password hash, active flag, and created timestamp.
- [ ] Implement email validation for `qq.com` and `163.com`.
- [ ] Implement PBKDF2 password hashing and signed cookie session helpers.
- [ ] Add auth endpoints for register, login, logout, and me.

### Task 2: User Isolation

**Files:**
- Modify: `database/models.py`
- Modify: `api/routers/monitor.py`
- Modify: `api/services/monitor_sync.py`
- Test: `tests/test_monitor_auth.py`

- [ ] Add `owner_user_id` to `MonitorAccount`.
- [ ] Require current user in monitor API endpoints.
- [ ] Filter account list, dashboard, matrix, snapshots, videos, export, account crawl, and sync by current user.
- [ ] Ensure an authenticated user cannot read, edit, delete, crawl, or export another user's accounts.

### Task 3: Frontend Auth UI

**Files:**
- Modify: `api/static/monitor.html`
- Test: `tests/test_monitor_crawl_limit_ui.py`

- [ ] Add a minimal login/register card for email and password.
- [ ] Hide monitor content until authenticated.
- [ ] Add current user display and logout button.
- [ ] Ensure existing API helper sends cookies.

### Task 4: Verify And Restart

**Files:**
- No new production files.

- [ ] Run targeted auth and monitor tests.
- [ ] Restart uvicorn on `127.0.0.1:8080`.
- [ ] Verify `/api/auth/me`, `/api/monitor/accounts`, `/api/monitor/matrix`, and `/monitor`.
