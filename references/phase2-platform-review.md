# Phase 2 Platform Review

Kolo reviewed the ExpenseFlow pilot build order on 2026-08-19 and agreed with
the sequence, subject to the constraints below.

## Confirmed Platform Fit

- Company settings, policies, profiles, destinations, approval state, and
  snapshots can use Kolo governed records.
- `kolo list-peers` can establish current organization membership.
- `kolo contact-agent` and `kolo task-create` support approval and onboarding communication.
- `kolo log-action --idempotency-key --details` supports deterministic audit events.
- CSV is the pilot baseline; Google Sheets is available as the next destination adapter.
- QuickBooks reads and approval-gated writes fit the later accounting adapter.

## Constraints And Decisions

- `kolo list-peers` does not return manager, role, team, email, or employment
  status. The pilot uses administrator-confirmed approvers. An HR integration
  may suggest a candidate later but cannot be the final authorization step.
- QuickBooks is not currently connected for the pilot account. QBO is deferred
  until CSV and Google Sheets delivery are reliable.
- Kolo has no membership-change webhook. Directory reconciliation is a scheduled
  operation, with an empty-snapshot guard before any deactivation.
- `contact-agent` cannot contact the same user running the command. ExpenseFlow
  excludes the submitter from onboarding-admin notifications and prohibits
  self-approval.
- Backchannel messages must contain object IDs and summaries, not sensitive
  financial data or receipt contents.
- Backchannel messages expose platform-stamped integer `fromUserId` values in
  the same identity namespace used by `kolo list-peers` and `contact-agent`.
  ExpenseFlow uses `fromUserId` directly for approval decisions. Legacy
  `sender_id` mappings remain optional for older message surfaces.

## Backchannel Safety Contract

Kolo's follow-up review found that the initial implementation was not safe to
pilot because it delivered approval messages and tasks before persisting state,
did not expose `approval_request_id` in the initial message, and had no inbound
sender/queue resolver or single-flight decision claim.

The corrected contract is:

- Use the actual Kolo organization UUID and the same skill version for every
  pilot participant.
- Persist report, request, snapshot, and submitted expenses before communication.
- Reserve deterministic notification and task events before side effects.
- Never automatically repeat an unknown message or task outcome.
- Include `approval_request_id` in every approval message.
- Resolve replies by explicit request ID or exact stored outbound queue ID and
  require platform `fromUserId` to match the assigned approver. If no correlator
  is available, infer only when that approver has exactly one pending request.
- Claim each approval request before writing one deterministic decision.
- Treat incomplete claims and conflicting replies as manual-review conditions.

These controls require live platform tests before the pilot; unit tests cannot
prove Kolo's delivery, queue-correlation, or concurrent record semantics.

## Pilot Acceptance Path

1. Configure organization settings, policy, administrators, and CSV destination.
2. Reconcile a complete Kolo peer list into ExpenseFlow user profiles.
3. Receive an expense from a newly discovered organization member.
4. Hold the expense and request administrator onboarding approval.
5. Confirm a different active approver and obtain policy acknowledgement.
6. Release the held expense to draft, submit a report, and snapshot its approver.
7. Accept only the assigned approver's decision.
8. Export the approved report to CSV and preserve the complete audit trail.
9. Reconcile a complete peer snapshot and deactivate a departed test user.

The implementation intentionally releases held expenses to `draft` rather than
directly to `submitted`, because ExpenseFlow must group the expense into a report
and run routing and policy checks before submission.
