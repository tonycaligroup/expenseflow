# Phase 0 Platform Tests

Run these tests before implementing platform-dependent ExpenseFlow workflows. Record the observed outputs and choose the fallback path where needed.

## Test 1: `contact-agent` Correlation

Goal: determine whether outbound approval messages return a stable queue or message ID.

```bash
kolo contact-agent -t <test_user_id> -m "ExpenseFlow test with request_id: ar_test_001" 2>&1
```

Pass condition: output includes a stable queue ID, message ID, or correlation ID.

Fallback: embed `request_id` and `report_id` in every approval message and correlate inbound replies by explicit IDs, sender, and timestamp window.

## Test 2: `task-create` Return ID

Goal: determine whether visibility tasks can be completed later by ID.

```bash
kolo task-create --title "ExpenseFlow test task" --user <test_user_id> 2>&1
```

Pass condition: output includes a task ID or UUID usable with task completion commands.

Fallback: treat tasks as visibility-only and keep the approval state machine entirely in governed records.

## Test 3: Receipt Attachment Handling

Goal: determine how uploaded receipts are exposed to a skill.

Procedure:

1. Send a test receipt image to Kolo.
2. Inspect the inbound message context available to the skill or agent.
3. Identify whether attachments appear as local paths, object references, URLs, or base64 payloads.
4. Confirm whether `kolo file-upload` can accept the observed attachment form.

Pass condition: there is a reliable path from inbound receipt to stored object reference.

Fallback: store the platform-provided attachment reference and defer re-upload if a local path is unavailable.

## Test 4: Record Upsert Idempotency

Goal: confirm that the same record type and external ID update the same record.

```bash
kolo record-upsert --record-type skill.test --external-id expenseflow_test_001 --payload '{"test":true}' --schema-version 1
kolo record-upsert --record-type skill.test --external-id expenseflow_test_001 --payload '{"test":true,"again":true}' --schema-version 1
kolo record-get --record-type skill.test --external-id expenseflow_test_001
```

Pass condition: only one logical record exists and payload updates.

## Test 5: Custom Statuses

Goal: confirm ExpenseFlow lifecycle statuses are accepted.

```bash
kolo record-status --record-type skill.test --external-id expenseflow_test_001 --status held_pending_onboarding
kolo record-get --record-type skill.test --external-id expenseflow_test_001
```

Pass condition: status is set to `held_pending_onboarding`.

## Test 6: Audit Idempotency

Goal: confirm duplicate audit events are suppressed by idempotency key.

```bash
kolo log-action --category skill.test --title "ExpenseFlow audit test" --idempotency-key "expenseflow_test_key_001"
kolo log-action --category skill.test --title "ExpenseFlow audit test" --idempotency-key "expenseflow_test_key_001"
```

Pass condition: only one audit event is created or the second call reports an idempotent duplicate.

Fallback: make audit idempotency best-effort and include deterministic event IDs in details.

## Test 7: CSV Delivery

Goal: confirm how a generated CSV should be sent to the current Kolo chat.

Procedure:

1. Generate a small CSV file.
2. Send it with the platform message/media mechanism.
3. Confirm filename, visibility, and download behavior.

Expected shape:

```json
{
  "action": "send",
  "channel": "kolo",
  "target": "kolo:<chat-uuid>",
  "message": "ExpenseFlow CSV delivery test.",
  "media": "/path/to/expenseflow-test.csv",
  "filename": "expenseflow-test.csv"
}
```

Pass condition: the CSV appears in chat as a downloadable attachment.

Fallback: upload the CSV to Google Drive or send it through Gmail, then notify the user with the resulting destination.

## Cleanup

After tests, remove or tombstone test records if the platform supports it:

```bash
kolo record-delete --record-type skill.test --external-id expenseflow_test_001
```

Do not delete audit records unless the platform specifically supports safe test cleanup.
