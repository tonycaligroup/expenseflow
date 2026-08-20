# Accounting Exports

Read this reference when configuring or running CSV, Google Sheets, or QuickBooks Online exports. Kolo governed records remain the source of truth. Only approved reports may be exported.

## CSV

Generate CSV with deterministic code. Escape user-controlled cells beginning with `=`, `+`, `-`, or `@`. `export-csv` marks the report and expenses `exported`, logs idempotently, and returns the CSV content even in compact output. Deliver the file only through the verified Kolo media flow.

## Google Sheets

Configure `spreadsheet_id`, `sheet_name`, optional immutable `sheet_id`, and optional `fallback_to_csv`. The tab must be empty or have exact ExpenseFlow headers. Write with `valueInputOption=RAW` and a deterministic `expenseflow_row_id`. Scan that ID column before append; stored row number and A1 range are hints because users can reorder rows.

Reserve each `skill.export_item`, append once, and mark it `confirmed` only after readback matches. Use `unknown` when an append may have succeeded without a usable response. Never repeat an unknown append automatically. Mark the report and expenses exported only after every item is confirmed.

Kolo governed records do not provide conditional create or an atomic lease. Use deterministic `skill.export_run` as a permanent single-flight claim. Only the invocation whose upsert reports `created: true` may append. A later run may reconcile rows already present but must fail closed when a claimed row is missing. Do not expire or take over a claim automatically.

Reads may retry bounded 429/5xx failures. Appends and updates must not retry after an inconclusive response. CSV fallback is allowed only before any item becomes `appended`, `confirmed`, or `unknown`; never combine a partial Sheets export with CSV. See [google-sheets-platform-verification.md](google-sheets-platform-verification.md).

## QuickBooks Online

Pin a numeric `realm_id` and explicitly choose `purchase`, `bill`, or `journalentry`. Configure `category_account_ids`. Purchase and JournalEntry require `balancing_account_id`; Bill requires `employee_vendor_ids` or `default_employee_vendor_id`. Never infer accounting treatment or create accounts/vendors automatically.

Run `qbo-refresh-cache` to refresh the bounded allowlisted reference cache. Pagination stops with an error rather than silently exceeding the 1,000-row limit per entity. QBO request bodies are limited to 100 KB.

`sync-qbo` creates a permanent governed claim and opens Kolo's human approval brief. Later calls poll write status. Only `executed` with a non-null result containing the QBO entity ID moves report and expenses to `synced`. Do not repeat a claim with a missing brief number or unknown/failed result. Rejected or expired briefs may start another attempt only with explicit `--retry-terminal`.

Configure `max_execution_checks` from 1 to 100, default 12. If an executed brief repeatedly lacks a result, mark the run `review_required`, its items `unknown`, and log operator review without submitting another write.

A report must contain one currency per QBO transaction. Store returned entity ID and SyncToken. Kolo write accepts JSON bodies only, so include safe receipt object references in `PrivateNote`; do not attempt multipart receipt upload. See [qbo-platform-verification.md](qbo-platform-verification.md).
