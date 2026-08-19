# Live Pilot Results

ExpenseFlow completed its first live three-user Kolo pilot on 2026-08-19.
Participant names, Kolo user IDs, organization ID, queue IDs, task ID, and
governed-record IDs remain in the private Kolo audit thread and are intentionally
excluded from this repository.

## Result

PASS on commit `e07cd6a730862ed50385de57cb484fc7764ca80b`.

- 81 deterministic tests passed in the Kolo runtime environment.
- Three distinct current organization members served as administrator,
  employee, and approver.
- No real receipt, banking, card, tax, email, Google Sheets, or QuickBooks data
  was used.
- No destructive directory reconciliation was run.
- Every outbound pilot message and task used the configured test warning prefix.

## Verified Sequence

1. Existing organization settings and policy records were checked before setup.
2. CSV-only settings, approval policy, and destination were configured against
   the real Kolo organization ID.
3. Current peers were reconciled without deactivating missing users.
4. A reconciled employee's first synthetic expense moved the employee from
   `discovered` to `pending_admin_approval` and held the expense as
   `held_pending_onboarding`.
5. The configured administrator received a correlated onboarding message.
6. The administrator assigned a distinct active approver and the employee
   received a policy acknowledgement message.
7. Policy acknowledgement activated the employee and released the held expense
   to `draft`.
8. Report submission created an immutable approver snapshot, approval request,
   correlated approver message, and visibility task.
9. The assigned approver's deterministic decision moved the report and expense
   to `approved`.
10. CSV export moved both records to `exported` and delivered a downloadable
    CSV attachment to the authorized Kolo thread.
11. Repeating a safe audit write with the same idempotency key returned the same
    audit event ID without repeating outbound messages.

## Defect Found And Fixed

The first attempt stopped before sending messages because a missing governed
record was classified as a generic Kolo command failure. The onboarding workflow
needed `record_not_found` to start just-in-time discovery.

The production gateway now recognizes Kolo's nested `record-get` 404 response
and maps only that response to `record_not_found`. Other command failures remain
hard errors. Regression tests cover the observed nested response and confirm
that non-`record-get` 404 responses are not hidden.

The pilot also led to two preventive improvements:

- Organization-configurable message prefixes are applied deterministically to
  onboarding, identity, approval, and task communications.
- A reconciled `discovered` employee's first expense now triggers the same admin
  review as an employee discovered just in time.

## Remaining Scope

This pilot verifies the Kolo-native CSV workflow. A separate controlled test now
verifies Google Sheets delivery, readback confirmation, duplicate prevention,
formula safety, and cleanup; see
`references/google-sheets-platform-verification.md`.

Remaining live scope includes receipt attachment ingestion, scheduled approval
reminders, QuickBooks mapping and writes, reimbursement payment, card feeds,
high-volume Sheets performance, and production operations at scale.
