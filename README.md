# Forma — Resource management

A persistent, single-workspace development application based on the attached Functional Requirements Specification v1.0 (13 November 2026). Built with Flask, SQLite, vanilla JavaScript and openpyxl. No external API credentials are needed for the local workflow.

## Run

Requires Python 3.12 and its venv support. Use the existing checkout; cloud tasks are already isolated and do not require another Git worktree.

```sh
cd /workspace/resource-management
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python app.py
```

The development server listens on port 3000. `PORT` overrides the port. No frontend build or Node package installation is required.

Local development accounts: `admin`, `ops`, `pm`, `dev`, `finance`, `vendor`. Each seeded account initially uses **local-demo-only**. `APP_ADMIN_PASSWORD` overrides this shared initial password **only before the database is first created**. The application does not include a user-administration screen. This is demo authentication; do not expose these accounts to the public Internet.

SQLite data, the generated session signing key and screenshots live in ignored `.local/`. The server seeds sample people, projects, leave and allocations for the current Sydney reporting week only on first initialization. It does not erase existing records on restart. The session key persists across restarts; optionally provide `SESSION_SECRET` securely outside source control. No secrets are required for this local demo.

## Workflow

1. **Resources / Projects:** Create unique identifiers, work patterns, employment dates, project ownership, billability and budgets. Archive records to retain their history. Effective-dated capacity updates preserve prior periods.
2. **Resource planner:** Switch week/month, navigate dates, filter people/team/project, and create tasks with explicit start/end dates. Assign one task to multiple people. Enter hours per working day or per week, tentative or confirmed. Each assignee receives the indicated hours. Calendar cells show total confirmed allocation against capacity and flag overbooking. Editing uses optimistic version checks and records the old/new assignments.
3. **Import center:** Download the CSV template or map custom column headings. Preview CSV/XLSX files, then commit. Unknown resource/project rows are quarantined; identical source records do not inflate totals. Original files, rows, entry identifiers, sources and import batches remain traceable. Source IDs must be stable and unique within the selected source. Without IDs, a normalized composite of resource, project, date, hours, billable and activity is used; supply IDs when distinct identical entries should both count.
4. **Timesheets:** Create a reporting period. Submit → Review → Approve, or Return with a reason. Missing time on allocated working days is flagged on submission. Resolve exceptions before approval. Self-approval is rejected. Approval locks entries from further imports and corrections. Operations/administrators can reopen with a reason. Corrections are separate signed deltas and require independent approval and resubmission if already in review.
5. **Reconciliation:** Enter PM or vendor period totals, compare against original time plus approved adjustments, assign exceptions and record resolutions. Statements remain independent; they never add to source actuals. For example, 160 source hours and 176 vendor hours produce a +16h exception.
6. **Reports:** Set explicit dates and resource/project/team filters. Review planned vs approved actuals, hours/percentage variance and utilisation. CSV/XLSX exports include approved source detail, adjustments, timestamp and filters; exported totals match the selected report. Finance can read/export approved hours, without editing planning data.
7. **Audit history:** Administrators and operations inspect before/after changes. User-administration screens and account-management endpoints are not included.

## Calculation conventions

- Reporting boundaries use date-only values in **Australia/Sydney**. Resource time zones are metadata; source export dates must already be normalized to reporting dates.
- Daily capacity follows the Monday–Sunday work pattern and employment dates. Weekly capacity changes scale the pattern from their effective date.
- Approved leave, holidays and other non-working events reduce capacity. Overlapping date-only events use the maximum unavailable hours on each date, capped by scheduled hours; they do not double-subtract the same day. Separate timed partial-day events and location-wide holiday feeds need a richer calendar model.
- Weekly allocations distribute evenly over an assignee's working weekdays. Partial weeks are prorated. Leave reduces capacity and does not silently delete a confirmed allocation.
- Tentative bookings are displayed but excluded from confirmed planning totals.
- Actual utilisation = approved actual hours / available capacity. Billable hours use approved billable entries. Zero-capacity/planned denominators show N/A.
- Periods cannot overlap for the same resource/project. Missing-day exceptions cover confirmed allocated working days. Other sources do not replace one another: source IDs and the selected source label define deduplication boundaries. Importing the same underlying time under two different source labels requires explicit reconciliation.

## Validation

```sh
.venv/bin/python -m pytest -q
# With the local server running on port 3000 and Chromium installed:
.venv/bin/python tests/browser_check.py
```

Backend tests use fresh temporary databases. Browser checks use the running local demo, create and delete their own allocation, and leave audit evidence. They verify login, weekly/monthly navigation, shared assignments, search, all screens, responsive layout, and JavaScript errors. Screenshots are written to `.local/`. The frozen lockfile includes development/test dependencies.

## Scope and release readiness

This implements the central local development workflows, rather than claiming completion of every FRS release acceptance criterion. It is **not a production deployment** or a signed-off P1 release.

Remaining release work: production SSO/MFA and account lifecycle policy, multi-organisation isolation, vendor-to-many-resource scopes, configurable reporting time zones, location holiday calendars and more detailed overlapping partial leave, production migrations/WSGI deployment, agreed performance and retention targets, backup/restore and monitoring operations, formal WCAG 2.2 AA and all stakeholder UAT acceptance. Imports support configurable column mappings rather than vendor-specific API export parsers; provide representative anonymised files before integration sign-off. Editing/resolving quarantined source rows uses re-import of corrected source records and an explicit exception resolution; no automatic identity guessing occurs. Financial rates/margins, scheduled Jira/YouTrack API sync, forecasting, and allocation approval requests are Phase 2 and are not implemented.

Existing local sample users and API role checks are supplied for development. Application logic enforces one workspace with per-project PM access and own-resource developer/vendor access. There is no claim of cross-organisation deployment readiness.

No production service is started, external messages sent, or configuration published by this repository.

## Local backup / recovery

Stop writes or use SQLite's online backup API to copy `.local/resource.db`; preserve `.local/session-key` securely when session continuity is needed. A database can be restored while the development server is stopped. Production RPO/RTO, encryption, off-site retention and restore exercises require an agreed hosting plan. Do not commit database files, passwords or signing keys.

## Deploy to Render

[Deploy to Render](https://render.com/deploy?repo=https://github.com/aliitworx-source/resource-management)

1. Open the link, sign into Render, and connect the GitHub repository if prompted.
2. Review the paid Starter service and 1 GB persistent disk, then click **Deploy Blueprint**. All environment variables and passwords are generated automatically; no commands or password setup are needed.
3. Open the HTTPS address Render provides. Sign in as `admin`; find the generated password under the service's **Environment → APP_ADMIN_PASSWORD** in Render. Keep it private.

There is no administration section in the app. Login and role checks remain in place. Other seeded accounts have unknown random passwords on new production installations. Existing databases keep their existing passwords; the generated value initializes new databases only.

Future GitHub commits deploy automatically after adopting the updated blueprint. Existing services may need a Blueprint sync or **Manual Deploy → Deploy latest commit** to pick up these changes.

The blueprint runs Gunicorn in Singapore with one worker/four threads. SQLite and the signing key persist under `/var/data`; `/healthz` checks database access. HTTPS session cookies are Secure, and the application rejects missing/weak initialization credentials. Render generates both `SESSION_SECRET` and `APP_ADMIN_PASSWORD`; no secrets are committed to GitHub.

This is an evaluation deployment with sample data. The remaining release requirements above still apply before live business data is used. Persistent storage requires paid Render resources; an ephemeral free instance would lose SQLite data. Render deployment is separate from publishing the Codex development environment.
