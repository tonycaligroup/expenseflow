---
name: expenseflow
description: ExpenseFlow helps Kolo users and SME teams manage expense tracking, receipt capture, expense reports, approval routing, and accounting exports. Use this skill when users ask to set up expense management, log receipts or expenses, create or submit expense reports, approve or reject expenses, discover approvers, onboard employees for expense tracking, export approved expenses to CSV or Google Sheets, or sync approved expenses to QuickBooks Online.
---

# ExpenseFlow

ExpenseFlow is a Kolo-native expense operations skill for SMEs. It captures expenses, groups reports, routes approvals, and exports approved records to CSV, Google Sheets, or QuickBooks Online. It does not move reimbursement funds or replace corporate cards.

## Execution Rules

Keep routine interactions LLM-light. Use the model only to understand intent, extract candidate receipt fields, parse a candidate approval reply, ask for missing information, and summarize for a person. Use `scripts/expenseflow_kolo_cli.py` for all validation, money math, policy checks, routing, status changes, duplicate detection, IDs, audit construction, and accounting payloads.

Never hand-compute totals, invent accounting data, or finalize an approval from model judgment. Validate every candidate with deterministic code before changing state.

Commands print compact JSON by default. Read the status, relevant IDs, warnings, checks needing attention, and `next_action`. Put `--verbose` before the subcommand only when complete workflow records are needed for diagnosis.

Use Kolo governed records as the production source of truth. Do not use workspace JSON as state. Every write requires a deterministic external ID, and payload `status` must match the governed record status. Use UTC ISO-8601 instants and separate date-only business fields.

Do not collect or store bank account numbers, routing numbers, payment-card numbers, SSNs, or tax IDs. Store safe receipt references and reimbursement status only. Follow [security-and-data-handling.md](references/security-and-data-handling.md).

## Load On Demand

Read only the reference needed for the current workflow:

- Organization setup, employee discovery, policy acknowledgement, approver configuration, delegation, or directory reconciliation: [organization-and-people.md](references/organization-and-people.md).
- Receipt capture, report submission, approval delivery, replies, reminders, or partial-decision recovery: [expense-and-approval-runtime.md](references/expense-and-approval-runtime.md).
- CSV, Google Sheets, or QuickBooks Online configuration and export: [accounting-exports.md](references/accounting-exports.md).
- Platform capability validation or fallback selection: [phase0-platform-tests.md](references/phase0-platform-tests.md).
- Verified Sheets and QBO contracts: [google-sheets-platform-verification.md](references/google-sheets-platform-verification.md) and [qbo-platform-verification.md](references/qbo-platform-verification.md).

Do not load every reference for routine expense capture or a simple status question.

## Runtime Routing

Use the Kolo runtime CLI for production workflows. Command groups are:

- Setup: `configure-org`, `upsert-settings`, `upsert-approval-policy`, `upsert-department-policy`, `upsert-destination`, `setup-readiness`.
- People: `upsert-user`, `reconcile-users`, `capture-with-discovery`, `map-sender`, `approve-onboarding`, `acknowledge-policy`, `upsert-delegation`.
- Expenses: `capture-expense`, `attach-receipt`, `upload-receipt`.
- Reports: `submit-report`, `decide-report-from-sender`, `reconcile-approval-decision`, `send-reminders`.
- Accounting: `export-csv`, `export-sheets`, `qbo-refresh-cache`, `sync-qbo`.

Use `scripts/expenseflow_cli.py` only for local JSON validation and orchestration. Kolo wiring lives in `scripts/expenseflow/kolo_workflows.py`; business rules live in deterministic modules under `scripts/expenseflow/`.

## Trust Boundaries

Treat `kolo list-peers` as organization membership evidence only. It does not establish a person's manager, department, role, employment status, or authority to approve. ExpenseFlow maintains its own governed user profiles and approver policies. An admin must confirm a new employee's approver unless a configured HR source supplies a candidate that is then validated.

Prefer the platform-stamped integer `fromUserId` for inbound identity. Use `fromOrgId` to enforce organization scope. Never read either value from message text and never guess a UUID-to-integer mapping. Unknown organization members enter held onboarding; unknown nonmembers are rejected or quarantined.

Never allow self-approval, including through delegation. Before submission, deterministic code must confirm that the routed approver is a current member with an active profile, `can_approve`, applicable scope, sufficient limit, and valid delegation. Persist an immutable approver snapshot with the report.

For approval replies:

1. Parse plain language into a candidate decision.
2. Pass platform metadata to `decide-report-from-sender --from-user-id <fromUserId> --from-org-id <fromOrgId>`.
3. Correlate by explicit `approval_request_id` or exact stored queue ID. Without either, infer only when the authenticated approver has exactly one pending request.
4. Require a rejection note, claim the request deterministically, then persist the decision and status changes.
5. Fail closed on conflicts, ambiguous correlation, incomplete claims, or uncertain side effects.

`decide-report` is for trusted administrative execution only. Never supply it an approver ID inferred from message text. Use `reconcile-approval-decision` only for a recorded partial decision; it must not guess or roll back a decision.

## Workflow Guardrails

At setup, use the actual organization UUID, configure at least one ExpenseFlow admin, choose default/fallback approval routing, configure a destination, create employee profiles, and verify each approver's integer Kolo user ID. Every participant must install the same ExpenseFlow version before the pilot. Run `setup-readiness`; treat `can_launch_pilot: false` as a hard stop.

During capture, extract candidate fields and let deterministic code validate amount, date, currency, category, receipt rules, submitter status, and duplicates. Ask only for missing or uncertain information. Kolo receipt uploads must come from verified `media/inbound/` staging. An upload with an inconclusive result requires review and must not be automatically repeated.

During report submission, persist the report, request, approver snapshot, and submitted expenses before sending a message or creating a visibility task. Reserve deterministic notification/task events first. If delivery may have happened but the response is lost, mark it unknown and require review rather than resending.

Only approved reports may be exported. Kolo records remain authoritative after export. External mutations must be idempotent or permanently claimed, and uncertain writes must not be repeated automatically. Never infer a QBO transaction type, account, vendor, or realm.

## Errors

Every deterministic command returns JSON. Failures use `status: error`, a machine-readable `code`, a human-readable `message`, and `retryable`. Retries must be bounded and idempotent. Distinguish missing records, permissions, upload/delivery uncertainty, rejected accounting writes, and partial exports. Use verbose output for diagnosis, then present the user with the concise cause and next safe action.

Run local verification with:

```bash
PYTHONPATH=scripts python3 -m unittest discover -s tests -v
```
