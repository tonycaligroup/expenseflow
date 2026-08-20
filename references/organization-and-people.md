# Organization And People

Read this reference for setup, employee lifecycle, approver configuration, delegation, and policy acknowledgement.

## Setup Sequence

1. Obtain the actual Kolo organization UUID with `PYTHONPATH=scripts python3 scripts/expenseflow_kolo_cli.py discover-org`; never pilot with `default`. This performs exactly one `kolo list-peers` call, validates that every returned member has the same organization ID, and returns only the ID, member count, and source. Do not try `kolo org`, `kolo ping`, environment inspection, or other fallback probes. If the user requested only the organization ID, stop here.
2. Discover members with `kolo list-peers`.
3. Create `skill.expense_settings`, including at least one `expense_admin_user_id` or `expense_admin_user_ids` value.
4. Configure default and fallback approvers in `skill.approval_policy`.
5. Add optional department routing in `skill.department_policy`.
6. Configure one `skill.accounting_destination`.
7. Configure categories, currencies, receipt thresholds, and policy version.
8. Create `skill.user_profile` records and verify every approver's integer Kolo `user_id`.
9. Send policy acknowledgement messages with `kolo contact-agent`.
10. Have all pilot participants install the same ExpenseFlow version.
11. Run `setup-readiness` and resolve every blocker.
12. Log setup completion with `kolo log-action`.

`setup-readiness` is deterministic and read-only. It returns `not_ready`, `ready_with_warnings`, or `ready`, `can_launch_pilot`, a summary, checks, and the first next action. Compact output includes only checks needing attention; run with `--verbose` to inspect passing checks. For Sheets and QBO, it verifies the configured connection without exporting. `--skip-integration-check` is diagnostic and leaves a warning.

Use optional `message_prefix` for pilots or sandboxes where every outbound message and task needs a fixed warning. The deterministic workflow adds it to onboarding, identity, and approval communications.

## Employee Discovery

Support scheduled and just-in-time discovery.

For scheduled discovery, run `kolo list-peers`, compare peers with `skill.user_profile`, create new users as `discovered`, mark missing users `deactivated`, notify an admin, and log reconciliation. Use one daily job per organization only when the organization wants automatic reconciliation.

For a new inbound submitter, prefer platform `fromUserId`. Verify that user and `fromOrgId` against current peers. If the user is a member, create a profile as `pending_admin_approval`, capture the valid expense as `held_pending_onboarding`, and notify an admin. Require admin approval, an assigned approver, and policy acknowledgement. Release held expenses to `draft`, never directly to `submitted`.

If only an unmapped legacy UUID sender is available, create `skill.identity_discovery` as `pending_admin_mapping` and hold the expense. An admin may map it to an integer Kolo user ID after member verification. Never guess the mapping. Reject or quarantine nonmembers and notify an admin.

User statuses are `discovered`, `pending_admin_approval`, `pending_policy_ack`, `active`, `suspended`, `deactivated`, `rejected`, and `pending_manager_assignment`.

## Approver Registry

Kolo membership does not expose manager hierarchy. Store routing in governed user, approval-policy, department-policy, and delegation records.

Route in this order:

1. Explicit approver on the submitter profile.
2. Valid configured HR manager mapping, when available.
3. Department policy.
4. Default approver.
5. Fallback approver.
6. Hold as `held_pending_manager` and notify an admin.

Before each submission, verify current membership, active profile, `can_approve`, department/scope, approval limit, and any delegation. A delegation is valid only within its date range and to an active eligible approver. Multiple active delegations for one approver are an error. Direct routing and delegation must never send an expense back to its submitter.

Create an immutable `skill.approver_snapshot` at submission so policy changes do not rewrite history. Admins should be able to answer who approves a user or department, list approvers, assign defaults/backups, and create time-bounded delegations through the skill.

## Reminders

Reminders are disabled by default. To enable them, set `approval_reminders.enabled`, `initial_delay_hours`, `interval_hours`, `max_attempts`, and optional `escalation_user_ids`. Configure exactly one isolated reminder job per organization. Do not create overlapping jobs.
