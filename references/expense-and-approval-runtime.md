# Expense And Approval Runtime

Read this reference for receipt capture, reports, approvals, reminders, and partial-decision recovery.

## Expense Capture

1. Extract candidate fields from receipt media or chat text.
2. Validate date, amount, currency, category, receipt requirement, duplicate candidates, and submitter status with deterministic code.
3. Ask concise follow-up questions only for missing or uncertain fields.
4. Store the receipt through the verified attachment workflow.
5. Create `skill.expense` with a deterministic ID.
6. Return captured fields, warnings, duplicates, and the next action.

Kolo stages inbound attachments under `media/inbound/`. `upload-receipt` resolves symlinks, checks that location, validates type and configured size, hashes the file, and reserves `skill.receipt` before `kolo file-upload`. The upload API has no native idempotency. If the result is inconclusive, mark the record `upload_unknown` and require review. Do not automatically upload it again.

After success, retain only object-store ID, `kolo://obj/...` reference, safe metadata, and hash. Never persist a local path or receipt binary. Lock receipt changes after submission.

Expense statuses are `draft`, `held_pending_onboarding`, `held_pending_manager`, `submitted`, `approved`, `rejected`, `exported`, and `synced`.

## Report Submission

On submission, deterministic code must:

1. Load eligible expenses and calculate totals by currency.
2. Run policy checks and route an eligible approver.
3. Create `skill.expense_report`, `skill.approval_request`, and an immutable `skill.approver_snapshot`.
4. Persist the report, request, snapshot, and submitted expenses.
5. Reserve a deterministic `skill.notification_event`, then send `kolo contact-agent`.
6. Reserve a deterministic `skill.task_event`, then create a visibility task.
7. Store returned queue/task IDs and log submission.

The message must include `approval_request_id` and explicit approve/reject reply forms. If message or task delivery may have happened but the response is lost, mark the event unknown and require review. Never recreate that side effect automatically.

Use `contact-agent` plus governed records for internal expense approvals. Do not use `kolo request-approval` for manager approval. Use `kolo quickbooks write` only after internal approval and only for the configured accounting mutation.

Report statuses are `draft`, `held_pending_manager`, `pending_approval`, `partially_approved`, `approved`, `rejected`, `exported`, and `synced`.

## Approval Replies

Parse a plain-language reply into a candidate only. Read integer `fromUserId` and `fromOrgId` from Kolo backchannel metadata, never message text. Match the authenticated user to the stored `approver_user_id`.

Prefer explicit `approval_request_id` or exact stored outbound queue ID. If neither exists, infer only when that approver has exactly one pending request. Otherwise ask for the request ID and make no state change.

Run `decide-report-from-sender` with platform identity and correlation. Validate organization, report/expense IDs, assigned approver, decision type, and required rejection note. Claim with deterministic `skill.approval_decision_claim` before updates, create `skill.approval_decision`, update report and expenses, acknowledge the inbound message when supported, complete the visibility task, and log idempotently.

An identical completed decision may replay. A conflicting decision or incomplete claim requires review. Legacy `--sender-id` support exists only when platform `fromUserId` is unavailable and a verified mapping already exists.

If a claim is `review_required` after a partial write, use `reconcile-approval-decision`. Reconciliation requires exactly one persisted decision and completes only missing state that is pending or already agrees. It never rolls back or guesses. A still-`claimed` record requires explicit stale-claim confirmation and must be at least 15 minutes old.

## Reminders

Run `send-reminders` only when reminders are configured. Before each send, reserve a deterministic notification event. Send only pending due requests to an approver who remains active and eligible. Stop at the configured maximum, notify escalation users or admins, and leave the request pending for a human decision. Reminder activity never implies approval. After a decision, set reminders to `resolved` and complete the visibility task.
