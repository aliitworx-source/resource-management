# Local API v1

Canonical prefix: `/api/v1`. Short `/api` routes are retained as local aliases. JSON requests and responses except multipart imports and file downloads. Errors return `{ "error": "message" }` with 400 for validation, 401 for authentication, 403 for permissions, and 409 for conflicts. SQLite foreign keys and database transactions protect imports and linked entities. This is a development API; production paging, identity integration and agreed load testing remain release work.

`POST /session` accepts username/password, sets an HttpOnly, SameSite=Lax session cookie and returns the public user and CSRF token. `GET /session` reads current authentication; `DELETE /session` signs out. Supply `X-CSRF-Token` on every authenticated mutation, alongside the cookie. Sessions enforce API permissions independently of the UI.

| Route | Method | Purpose / main fields |
|---|---|---|
| `/data` | GET | Role-scoped resources, projects, assignments, calendars, time, periods, exceptions, statements and audit data |
| `/resources` | POST | id, name, title, team, employment, manager, location, timezone, hours, seven-number pattern, start, end, status; `editing` for update |
| `/capacity` | POST | resource_id, effective, hours; effective-dated weekly capacity |
| `/projects` | POST | id, name, client, pm (user ID), start, end, status, billable, budget; `editing` for update |
| `/tasks` | POST | title, project_id, start, end, hours, unit (`day`/`week`), state (`Confirmed`/`Tentative`), notes, people (resource ID list); id/version required to edit |
| `/tasks` | DELETE | id; removes the allocation and assignments, retains audit history |
| `/leave` | POST | resource_id, start, end, hours per day, category; records approved non-working time |
| `/imports/preview` | POST | Multipart `file`, `source`, optional `mapping` JSON canonical-field → source-header map; validates without persisting |
| `/imports/commit` | POST | Same input; preserves original file, normalized rows and quarantine exceptions, skips duplicates |
| `/template` | GET | Download canonical CSV headers and example row |
| `/batches/<id>` | GET | Download retained original file; administrator/operations only |
| `/periods` | POST | action `Create` with resource_id/project_id/start/end, or id/action `Submit`, `Review`, `Approve`, `Return`, `Reopen`; reason for Return/Reopen |
| `/adjustments` | POST | entry_id, delta, reason to propose; id/approve=true for independent approval |
| `/statements` | POST | resource_id, project_id, start, end, hours, kind (`Vendor`/`PM`); stores independent statement and variance exception |
| `/exceptions` | POST | id, owner, state (`Open`/`In review`/`Resolved`), resolution; resolution required to close |
| `/export` | GET | start/end required; optional project, resource, resources (comma-separated IDs), format (`csv`/`xlsx`); approved locked-period time only, including approved adjustments |

## Role matrix

| Capability | Administrator | Operations | Project Manager | Developer | Finance | Vendor Liaison |
|---|---|---|---|---|---|---|
| Read | Workspace | Workspace | Owned projects | Own resource | Workspace | Own bound resource |
| Resources/projects/calendars | Manage | Manage | Read | Read | Read | Read |
| Allocations | Manage | Manage | Owned projects | Read | Read | Read |
| Import files | Yes | Yes | No | No | No | Bound resource |
| Submit periods/corrections | Yes | Yes | Owned projects | Own resource | No | No |
| Review/approve | Yes | Yes | Owned projects | No | No | No |
| Reopen locked period | With reason | With reason | No | No | No | No |
| Reconcile statements | Yes | Yes | Owned projects | No | No | Bound resource |
| Export approved hours | Yes | Yes | Owned projects | No | Yes | No |
| Audit / raw batches | Yes | Yes | No | No | No | No |

Self-approval is always denied for periods and corrections. Submitted/reviewed periods are returned when a correction is raised or approved. Pending corrections and unresolved period exceptions block period approval. Approved periods reject later imports and corrections until privileged reopening. Independent vendor/PM statements can still record discrepancies without overwriting approved time.

## Stored entities

The schema is initialized in `app.py`: users, resources, effective-dated capacity changes, projects, tasks, many-to-many assignments, leave/calendar events, import batches with original files, normalized entries, reporting periods, reconciliation exceptions, independent statements, non-destructive adjustments, and audit events. Dates are ISO date strings; audit/import times are UTC timestamps. Passwords are hashes, never returned or audited. No rate fields exist in this release.

Source `(source, source_id)` is unique. No-source-ID rows use a deterministic composite hash. Assignments are unique per task/resource; each logical task is counted once per assigned person per working day. Period overlap checks and explicit states protect approvals. Initial daily-only task storage is migrated to allow weekly totals without losing assignments. Back up the database before deploying any future schema changes.
