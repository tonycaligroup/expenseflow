# Google Sheets Platform Verification

Kolo reviewed the ExpenseFlow Google Sheets plan on 2026-08-19. The review was
read-only: it inspected the authenticated integration, spreadsheet metadata,
range reads, API documentation, CLI capabilities, and failure shapes. It did
not create, edit, or delete a spreadsheet.

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

## Expected Write Surface

Kolo confirmed the standard v4 route shapes through the Maton integration, but
the read-only review did not execute them. They remain partially verified until
the controlled live test succeeds:

- Append: `POST /spreadsheets/{id}/values/{range}:append`
- Deterministic range update: `PUT /spreadsheets/{id}/values/{range}`
- Batch value update: `POST /spreadsheets/{id}/values:batchUpdate`
- Structural update: `POST /spreadsheets/{id}:batchUpdate`

An append response should contain `spreadsheetId`, `tableRange`, and
`updates.updatedRange`. ExpenseFlow parses `updatedRange`, stores the resulting
row and range as hints, and confirms the complete row with a readback before it
changes internal expense state.

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

## Live Test Still Required

The smallest controlled live test should use a temporary spreadsheet and
synthetic expense only:

1. Create or select a temporary spreadsheet and record its spreadsheet ID, tab
   title, and immutable sheet ID.
2. Configure a temporary ExpenseFlow organization destination.
3. Export one approved synthetic expense.
4. Verify headers, one row, `expenseflow_row_id`, stored `updatedRange`, and
   readback confirmation.
5. Invoke the same export again and verify no second row is appended.
6. Remove the temporary governed records and spreadsheet after evidence is
   collected.

Do not use a production accounting sheet for this test.
