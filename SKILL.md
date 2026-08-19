---
name: expenseflow
description: ExpenseFlow helps Kolo users and SME teams manage expense tracking, receipt capture, expense reports, approval routing, and accounting exports. Use this skill when users ask to set up expense management, log receipts or expenses, create or submit expense reports, approve or reject expenses, discover approvers, onboard employees for expense tracking, export approved expenses to CSV or Google Sheets, or sync approved expenses to QuickBooks Online.
---

# ExpenseFlow

ExpenseFlow is a Kolo-native expense operations skill for SMEs. It should help teams capture expenses, group them into reports, route approvals, and export or sync approved expenses to accounting destinations.

The v3 scope is **approval-ready expense reports with flexible export**, not full reimbursement or corporate-card replacement.

## Operating Model

Keep the skill LLM-light.

Use the LLM for:

- Extracting draft fields from receipt images or PDFs.
- Understanding user intent in chat.
- Parsing plain-language approval replies into candidate decisions.
- Asking for missing information.
- Summarizing expense reports for humans.

Use deterministic scripts for:

- IDs, money math, totals, date validation, currency grouping, category validation.
- Policy checks, approver routing, and approver eligibility validation.
- Status transitions, duplicate detection, approval state, and audit event construction.
- CSV rows, Google Sheets rows, and QuickBooks payload generation.
- Idempotency, retries, pagination, and structured errors.

Never hand-compute totals, invent accounting payloads, or mark approvals final from model judgment alone. The model may produce a candidate; deterministic code must validate before state changes.

Deterministic code lives under `scripts/expenseflow/`. Use `scripts/expenseflow_cli.py` for JSON command-line validation and orchestration. Run local tests with:

```bash
python3 -m unittest discover -s tests -v
```

Current deterministic modules include expense and receipt validation, money math, duplicate detection, status transitions, approver routing, bounded approval reminders, approval decision handling, report creation, and CSV export generation.

Kolo platform wiring lives in `scripts/expenseflow/kolo_workflows.py`. It must call deterministic core modules for business rules, then persist results through a narrow gateway interface. Tests use `FakeKoloGateway` from `scripts/expenseflow/kolo_gateway.py`; production usage uses `KoloCommandGateway` from `scripts/expenseflow/kolo_command_gateway.py`.

Use `scripts/expenseflow_kolo_cli.py` for Kolo runtime flows:

- `upsert-user`
- `upsert-settings`
- `upsert-approval-policy`
- `upsert-department-policy`
- `upsert-destination`
- `upsert-delegation`
- `configure-org`
- `reconcile-users`
- `capture-expense`
- `capture-with-discovery`
- `map-sender`
- `approve-onboarding`
- `acknowledge-policy`
- `submit-report`
- `decide-report`
- `attach-receipt`
- `upload-receipt`
- `send-reminders`
- `export-csv`

## Phase 0 Gate

Before building or relying on platform-dependent workflows, run the Phase 0 verification tests in [phase0-platform-tests.md](references/phase0-platform-tests.md).

Proceed with implementation only after documenting:

- Whether `kolo contact-agent` returns a usable queue/message ID.
- Whether `kolo task-create` returns a usable task ID.
- How receipt attachments are exposed to the skill.
- How CSV files should be delivered to the current Kolo chat.
- Whether record and audit idempotency behave as expected.

If a platform check fails, use the fallback described in the Phase 0 reference rather than guessing.

## Source Of Truth

Use Kolo governed records for production state. Do not use local workspace JSON files as the source of truth.

All governed record writes must use deterministic external IDs. Record payload `status` must match the governed record status field.

Core record types:

- `skill.expense_settings`
- `skill.accounting_destination`
- `skill.user_profile`
- `skill.identity_discovery`
- `skill.approval_policy`
- `skill.department_policy`
- `skill.expense`
- `skill.receipt`
- `skill.expense_report`
- `skill.approval_request`
- `skill.approval_decision`
- `skill.approver_snapshot`
- `skill.notification_event`

Use UTC ISO-8601 timestamps for instants. Store date-only business fields, such as expense date and due date, separately.

## Setup Workflow

When an admin sets up ExpenseFlow:

1. Discover org users with `kolo list-peers`.
2. Create org settings in `skill.expense_settings`.
3. Configure default and fallback approvers in `skill.approval_policy`.
4. Configure department routing in `skill.department_policy` if needed.
5. Configure export destination in `skill.accounting_destination`: CSV, Google Sheets, Google Drive/Gmail, or QuickBooks Online.
6. If QBO is selected, run `kolo quickbooks status` and fetch accounts/vendors with `kolo quickbooks call`.
7. Configure categories and receipt thresholds.
8. Create `skill.user_profile` records for employees.
9. Send policy acknowledgement messages with `kolo contact-agent`.
10. Log setup completion with `kolo log-action`.

Approval reminders are disabled by default. To enable them, configure
`approval_reminders.enabled`, `initial_delay_hours`, `interval_hours`,
`max_attempts`, and optional `escalation_user_ids` in organization settings.
Configure exactly one isolated reminder cron job per organization.

Organization setup must configure at least one `expense_admin_user_id` or
`expense_admin_user_ids` value. Kolo user discovery does not expose manager,
role, team, or employment status. Treat `kolo list-peers` as organization
membership evidence only. Require an ExpenseFlow admin to confirm the
employee's approver unless an optional HR integration supplies a candidate.

Use the optional `message_prefix` setting for pilots, sandboxes, or other
environments where every outbound message and task must carry a fixed warning.
The deterministic workflow prepends it to onboarding, identity, and approval
communications.

Do not collect or store bank account numbers, routing numbers, card numbers, SSNs, or tax IDs. Track reimbursement method/status only; accounting or payroll handles actual payment.

## Employee Lifecycle

Support both scheduled discovery and just-in-time discovery.

Scheduled discovery:

- Run `kolo list-peers` on a daily cron.
- Compare current peers with `skill.user_profile`.
- Create new users as `discovered`.
- Mark missing users as `deactivated`.
- Notify admin and log the reconciliation.

Just-in-time discovery:

- Resolve a known inbound `sender_id` through `skill.user_profile`.
- If inbound context has only an unmapped UUID sender ID, store the validated
  expense as `held_pending_onboarding`, create `skill.identity_discovery` as
  `pending_admin_mapping`, and ask an admin to map it to an integer Kolo user ID.
- Never guess the UUID-to-integer identity mapping.
- If an unknown user submits an expense, verify them against `kolo list-peers`.
- If they are an org member, create `skill.user_profile` as `pending_admin_approval`.
- Store the expense as `held_pending_onboarding`.
- Notify admin with expense context.
- Require admin approval and policy acknowledgement before releasing the expense.
- If the sender is not an org member, reject or quarantine and notify admin.

Release an onboarding-held expense back to `draft`, not directly to
`submitted`. This preserves report grouping, policy checks, and approver
snapshot creation before submission.

User statuses:

- `discovered`
- `pending_admin_approval`
- `pending_policy_ack`
- `active`
- `suspended`
- `deactivated`
- `rejected`
- `pending_manager_assignment`

## Approver Registry

Kolo org membership does not expose manager hierarchy. ExpenseFlow must maintain its own approver registry through governed records.

Support user queries such as:

- "Who is my approver?"
- "Who approves Engineering expenses?"
- "List approvers."
- "Set Kendra as Engineering approver."
- "Make Sam backup approver."
- "Delegate my approvals to Max next week."

Approver routing order:

1. Explicit approver on `skill.user_profile`.
2. Gusto manager mapping, if configured and available.
3. Department policy.
4. Default approver.
5. Fallback approver.
6. Hold as `held_pending_manager` and notify admin.

Before sending an approval request, re-validate the selected approver:

- Current org member.
- Active user profile.
- `can_approve` is true.
- Department/scope applies.
- Amount is within approval limit.
- Active delegation is respected.

Create an immutable `skill.approver_snapshot` at submission time so historical reports remain auditable after policy changes.

An active `skill.approval_delegation` may replace the routed approver only when
the date range is valid and the delegate is an active, eligible approver. More
than one active delegation for the same approver is an error. Never route an
expense back to its submitter through direct assignment or delegation.

## Expense Capture

When a user logs a receipt or expense:

1. Extract draft fields from receipt or chat text.
2. Validate date, amount, currency, category, receipt requirement, duplicate candidates, and submitter status.
3. Ask concise follow-up questions only for missing or uncertain fields.
4. Upload/store receipt using the verified attachment flow.
5. Create `skill.expense`.
6. Return the captured fields, warnings, and next action.

Kolo stages inbound attachments under `media/inbound/`. Pass only a verified
staged path to `upload-receipt`; local uploads outside that directory are
rejected. The workflow resolves symlinks, validates the file type and optional
organization size limit, hashes the file, and reserves a deterministic
`skill.receipt` record before calling `kolo file-upload FILE_PATH`.

`kolo file-upload` has no native idempotency. A receipt record left as
`upload_unknown` must be reviewed rather than uploaded again automatically.
After a successful upload, store only the object-store ID, `kolo://obj/...`
reference, safe file metadata, and hash. Never persist the staged local path or
receipt binary. Lock receipt changes once the expense is submitted.

For implementation, call `capture_expense(...)` from `kolo_workflows.py` after receipt extraction has produced draft fields. The workflow loads submitter profile/settings, validates with deterministic code, detects duplicate candidates, writes `skill.expense`, and logs an idempotent audit event.

Expense statuses:

- `draft`
- `held_pending_onboarding`
- `held_pending_manager`
- `submitted`
- `approved`
- `rejected`
- `exported`
- `synced`

## Reports And Approvals

When a user submits expenses:

1. Find eligible expenses.
2. Create `skill.expense_report`.
3. Calculate totals by currency.
4. Run policy checks.
5. Route and re-validate approver.
6. Create `skill.approver_snapshot`.
7. Create `skill.approval_request`.
8. Send approval request with `kolo contact-agent`.
9. Create optional visibility task with `kolo task-create`.
10. Update report to `pending_approval`.
11. Log submission.

When reminders are enabled, submission also stores `next_reminder_at`,
`reminder_count`, and `reminder_status` on the approval request. Run due sweeps
with deterministic code:

```bash
PYTHONPATH=scripts python3 scripts/expenseflow_kolo_cli.py \
  --org-id <org_id> send-reminders
```

Schedule this command through one `openclaw cron add --session isolated` job per
organization. The job prompt must include the organization ID. Kolo cron runs a
single instance of a job at a time; do not configure overlapping jobs for the
same organization.

Before each delivery, create a deterministic `skill.notification_event`.
Send only pending, due requests to an approver who is still active and eligible.
Stop after the configured maximum, notify configured escalation users or
ExpenseFlow admins, and leave the approval request pending for a human decision.
Never infer approval from reminder activity. After a decision, set reminders to
`resolved` and complete the visibility task with
`kolo task-complete --task-id <task_id>`.

For implementation, call `submit_report_for_approval(...)` from `kolo_workflows.py`. It creates `skill.expense_report`, sends the approver message through the gateway, creates a visibility task, writes `skill.approval_request`, marks included expenses `submitted`, and logs an idempotent audit event.

Use `contact-agent` plus governed records for internal expense approvals. Do not use `kolo request-approval` for normal manager approvals.

Use `kolo quickbooks write` only for external accounting mutations after the internal report is approved.

Approval replies:

- Parse plain-language replies into candidate decisions.
- Validate report/expense IDs, sender, delegation, decision type, and rejection notes.
- Lock approval processing with short expiration fields.
- Create `skill.approval_decision`.
- Update report and expense statuses.
- Acknowledge inbound message when supported.
- Complete visibility task when supported.
- Log the decision idempotently.

Report statuses:

- `draft`
- `held_pending_manager`
- `pending_approval`
- `partially_approved`
- `approved`
- `rejected`
- `exported`
- `synced`

## Export Destinations

Treat export destinations as adapters. Kolo governed records remain the source of truth.

Supported v3 destinations:

- CSV.
- Google Sheets.
- Google Drive.
- Gmail.
- QuickBooks Online.

CSV exports must be generated by deterministic code and delivered through the verified Kolo message/media flow. Escape spreadsheet formula prefixes in user-controlled fields.

CSV export is the first end-to-end adapter. A report and all included expenses must be `approved` before export. User-controlled CSV cells must be escaped when they begin with `=`, `+`, `-`, or `@`.

For implementation, call `export_approved_report_csv(...)` from `kolo_workflows.py`. It generates CSV, marks the report and expenses `exported`, and logs an idempotent audit event. Delivery of the CSV file/message remains a platform adapter responsibility until the Kolo media delivery shape is fully verified.

Google Sheets exports append deterministic rows to a configured sheet. Track export status at both report and expense row level.

QBO sync requires:

1. `kolo quickbooks status`.
2. QBO account/vendor cache.
3. Category-to-account mapping.
4. Deterministic Bill or JournalEntry payload generation.
5. Approval-gated `kolo quickbooks write`.
6. Confirmed write completion before marking records `synced`.

If QBO receipt attachment upload is unsupported, include receipt references in transaction notes.

## Error Handling

Every deterministic command must print JSON. On failure, return:

```json
{
  "status": "error",
  "code": "machine_readable_code",
  "message": "Human-readable message",
  "retryable": false
}
```

Distinguish missing records, backend failures, permission errors, file upload failures, delivery failures, rejected QBO writes, and partial export failures.

Retries must be bounded and idempotent.

Follow [security-and-data-handling.md](references/security-and-data-handling.md)
for receipt references, approval messages, logs, exports, and integration data.

The first live three-user Kolo pilot results are documented in
[live-pilot-results.md](references/live-pilot-results.md). Keep participant,
organization, queue, task, and governed-record identifiers in the private Kolo
audit thread rather than publishing them in this repository.
