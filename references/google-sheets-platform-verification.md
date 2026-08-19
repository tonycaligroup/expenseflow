# Google Sheets Platform Verification

Kolo reviewed the ExpenseFlow Google Sheets plan on 2026-08-19, then ran a
controlled live test against a temporary spreadsheet. The review inspected the
authenticated integration, spreadsheet metadata, range reads, API
documentation, CLI capabilities, and failure shapes. The live test exercised
the write, readback, duplicate-prevention, formula-safety, and cleanup paths.

## Verified Connection

- `kolo integration-routing` reports Google Sheets connected through the Maton
  gateway for the Kolo account.
- The supported route is
  `https://gateway.maton.ai/google-sheets/v4/spreadsheets/{path}`.
- Spreadsheet metadata reads return stable integer `sheetId` values and mutable
  tab titles.
- Single-range reads and `values:batchGet` return standard Google Sheets v4
  response shapes.
- The Maton route, not `gws`, is authoritative for the verified account.

## Verified Write Surface

Kolo confirmed the standard v4 route shapes through the Maton integration:

- Append: `POST /spreadsheets/{id}/values/{range}:append`
- Deterministic range update: `PUT /spreadsheets/{id}/values/{range}`
- Batch value update: `POST /spreadsheets/{id}/values:batchUpdate`
- Structural update: `POST /spreadsheets/{id}:batchUpdate`

The controlled test executed the deterministic header update and value append.
The append response supplied `updates.updatedRange`; ExpenseFlow parsed it,
stored the resulting row and range as hints, and confirmed the complete row
with a readback before changing internal expense state.

Native append idempotency and conditional writes are not available. The
`developerMetadata` endpoint also appeared unavailable through the verified
Maton connection. ExpenseFlow therefore uses an ordinary
`expenseflow_row_id` column and client-side lookup.

## Failure Handling

| Failure | ExpenseFlow behavior |
| --- | --- |
| 400 invalid request | Do not retry; fix the range or payload. |
| 401 unauthenticated | Stop and reconnect the integration. |
| 403 permission denied | Stop; share the spreadsheet with the connected account. |
| 404 not found | Stop; repair the destination configuration. |
| 429 or 5xx on reads | Retry with bounded exponential backoff. |
| Inconclusive append/update response | Mark the item `unknown` or `appended`; do not repeat the write automatically. |
| HTML instead of JSON | Treat the endpoint as unsupported through Maton. |

CSV fallback is safe only before any expense row has a potentially successful
external write. Partial Sheets and CSV exports must never be combined silently.

## Concurrency Limitation

Kolo's governed record CLI has no create-only, if-absent, expected-version,
ETag, compare-and-set, conditional status transition, or lease operation. The
platform also has no documented one-invocation-at-a-time guarantee for a skill
or cron job.

The governed record key `(record_type, external_id)` is unique and an upsert
returns `created: true` for the first creation and `created: false` for an
update. Kolo considers one-winner behavior for concurrent creates likely, but
the transaction semantics are not documented. ExpenseFlow limits its claim to
what the observable API supports:

1. Use one deterministic `skill.export_run` key per report, organization, and
   spreadsheet.
2. Competing workers submit the same claim payload.
3. Only the worker receiving `created: true` may append.
4. A worker finding an existing claim may only reconcile rows already present.
5. If any claimed row is absent, stop for review instead of taking over or
   appending again.

This prevents an automatic retry from turning an uncertain write into a known
duplicate. It does not provide automatic recovery when a process dies after
claiming an export but before writing a row. A verified conditional-write or
lease primitive is the platform improvement needed for safe automatic takeover.

## Controlled Live Test

The controlled live test passed on 2026-08-19 using one temporary spreadsheet,
one synthetic organization, one synthetic report, and one synthetic expense.
No real users, vendors, receipts, approvers, accounting data, messages, tasks,
or uploads were used.

1. The first export wrote the exact 15-column header and one expense row.
2. The deterministic `expenseflow_row_id`, stored write range, `export_item`
   state `confirmed`, and `export_run` state `complete` were verified.
3. The report and expense moved from `approved` to `exported` only after the
   row readback succeeded.
4. A vendor value beginning with `=SUM(A1:A10)` remained literal text, proving
   that `RAW` writes prevented formula execution.
5. Repeating the same export returned `already_exported`; the sheet still
   contained exactly one matching expense row.
6. Governed payloads contained no integration credentials or local filesystem
   paths.
7. The temporary spreadsheet was permanently deleted and its absence confirmed
   with a subsequent `404` response.
8. The five synthetic governed records were soft-deleted. Kolo's audit history
   remains available as the trace of those actions.

The implementation suite also passes 124 deterministic tests. This test proves
the controlled single-report path; it does not establish production throughput
or eliminate the concurrency limitation described above.
