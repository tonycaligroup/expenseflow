# QuickBooks Online Platform Verification

Kolo performed a read-only review of its QuickBooks Online command surface on
2026-08-19. The account was not connected: `kolo quickbooks status` returned
`connected: false`, production environment, and no realms. No QuickBooks reads
or writes were attempted.

## Verified Command Contract

- `kolo quickbooks status` returns connection environment and
  `realms: [{realm_id, company_name, needs_reconnect}]` when connected.
- `kolo quickbooks call PATH --api accounting --realm REALM --query KEY=VALUE
  -q` performs non-mutating reads. Accounting paths are relative to
  `/v3/company/{realmId}/`; do not include a leading slash or query string.
- `kolo quickbooks write PATH --body JSON --realm REALM --request-id ID
  --reason TEXT --session-key KEY` freezes an approval brief. It does not mutate
  QBO until a human approves the brief.
- `kolo quickbooks write-status --brief-id ID` reports `pending`, `approved`,
  `executed`, `rejected`, `failed`, or `expired`, plus `execution_result`.
- `executed` with a null `execution_result` means execution is still in
  progress. ExpenseFlow requires a non-null result with the expected entity ID.
- Reads are not approval-gated. All writes are approval-gated; the skill cannot
  approve or bypass the brief.

## Deterministic Design

1. Pin a realm and require explicit `purchase`, `bill`, or `journalentry`
   configuration. Purchase is not assumed to be universally correct.
2. Refresh bounded account/vendor/customer/tax/class/department/currency reads
   and persist only allowlisted reference fields.
3. Build canonical JSON in code and hash the path plus body.
4. Create one permanent `skill.export_run` claim before opening a brief and one
   `skill.export_item` per expense line.
5. Persist the returned brief number. If the response is lost, fail closed and
   do not create another brief automatically.
6. Poll the stored brief. Only a confirmed QBO entity ID completes the run and
   moves the report and expenses to `synced`.
7. Permit a new attempt after `rejected` or `expired` only through an explicit
   operator retry. Never auto-retry `failed` or unknown outcomes.

## Unverified Until Connection

- Live query response shapes and permissions for each configured realm.
- Whether `Purchase`, `Bill`, or `JournalEntry` matches the organization's
  accounting and reimbursement policy.
- Whether accounting `--request-id` is enforced as an Intuit idempotency key.
- The exact nesting of QBO entity ID and SyncToken in `execution_result`.
- Sparse-update and stale-SyncToken conflict behavior through Kolo.
- Multipart receipt attachment support. Current write help accepts JSON only,
  so ExpenseFlow sends safe object references in `PrivateNote` instead.
- Production throughput, approval latency, and failure-recovery operations.

The next live test requires an administrator-connected QBO sandbox or dedicated
test company, bounded reference reads, one synthetic approved report, human
approval of one clearly labeled brief, readback of the created transaction, and
cleanup or reversal according to the accountant-approved test procedure.
