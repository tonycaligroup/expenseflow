# ExpenseFlow Security And Data Handling

ExpenseFlow processes business expense data, but it is not a vault for payment
credentials, tax identities, or banking information.

## Never Collect Or Store

- Full card numbers, CVV values, bank account numbers, or routing numbers.
- Social Security numbers, tax IDs, passwords, API keys, or authentication codes.
- Full receipt images or extracted receipt text in Kolo backchannel messages,
  task titles, audit details, or model prompts when an object reference is enough.

## Governed Records

- Store expense, report, approval, user, destination, and audit state in Kolo
  governed records.
- Store unresolved UUID-to-integer identity mappings in
  `skill.identity_discovery`; require an authorized admin to map them to a
  current `list-peers` member.
- Store receipt object IDs or verified platform references. Do not copy receipt
  binaries into record payloads.
- Accept local receipt uploads only from Kolo's resolved `media/inbound/`
  staging path. Do not persist or log the local path.
- Reserve `skill.receipt` by expense ID and SHA-256 before upload. Treat an
  interrupted upload as `upload_unknown`; do not retry it automatically because
  `kolo file-upload` is not idempotent.
- Use deterministic external IDs and idempotency keys.
- Keep the approver snapshot immutable after report submission.
- Deactivated, suspended, rejected, or unverified users cannot submit expenses.

## Messages And Tasks

- Approval messages may include report ID, submitter display name, totals by
  currency, and a short policy summary.
- Onboarding messages may include user ID and held expense ID.
- Do not include receipt contents, card data, private notes, or accounting
  credentials in `contact-agent` messages or task titles.
- Reminder and escalation messages may include report/request IDs, submitter
  name, totals by currency, attempt count, and assigned approver ID. Do not add
  vendor line items or receipt content.
- Treat a natural-language approval reply as a candidate only. Deterministic
  code must verify request ID, responder identity, decision type, current state,
  and idempotency before changing records.

## Exports And Integrations

- Export only approved reports.
- Escape spreadsheet formula prefixes in all user-controlled CSV and Sheets cells.
- Store integration object IDs and posting results, not access credentials.
- Preview accounting payloads before posting. QuickBooks writes require the
  platform's approval-gated write path.
- Record external transaction IDs and block duplicate posting.

## Directory Reconciliation

- `kolo list-peers` is membership evidence, not manager or employment-status evidence.
- Never deactivate users from an empty peer snapshot.
- Run destructive reconciliation only from a complete organization snapshot.
- Require an administrator to confirm approver assignments when no verified HR
  manager relationship is available.
- Never infer that an inbound UUID and an integer Kolo user ID identify the same
  person. Preserve the held submission and require an explicit mapping.
