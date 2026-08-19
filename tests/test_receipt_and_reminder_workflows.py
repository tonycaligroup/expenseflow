import tempfile
import unittest
from pathlib import Path

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.kolo_gateway import FakeKoloGateway
from scripts.expenseflow.kolo_workflows import (
    APPROVAL_REQUEST,
    EXPENSE,
    NOTIFICATION_EVENT,
    RECEIPT,
    attach_receipt_reference,
    capture_expense,
    decide_report_approval,
    send_due_approval_reminders,
    submit_report_for_approval,
    upsert_approval_policy,
    upsert_expense_settings,
    upsert_user_profile,
    upload_and_attach_receipt,
)


SUBMITTER = {"user_id": 1, "display_name": "Employee", "department": "Ops", "status": "active"}
APPROVER = {
    "user_id": 2,
    "display_name": "Approver",
    "status": "active",
    "can_approve": True,
    "approval_scope": {"departments": ["Ops"], "max_amount": "1000.00"},
}


class ReceiptAndReminderWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeKoloGateway()
        upsert_user_profile(self.gateway, SUBMITTER)
        upsert_user_profile(self.gateway, APPROVER)
        upsert_approval_policy(self.gateway, "default", {"default_approver_user_id": 2})
        upsert_expense_settings(
            self.gateway,
            "default",
            {
                "expense_admin_user_ids": [99],
                "message_prefix": "EXPENSEFLOW TEST",
                "approval_reminders": {
                    "enabled": True,
                    "initial_delay_hours": 1,
                    "interval_hours": 1,
                    "max_attempts": 2,
                },
            },
        )

    def test_capture_accepts_normalized_receipt_attachment(self):
        expense = capture_expense(
            self.gateway,
            {
                **self._expense_data(),
                "receipt_attachment": {
                    "objectStoreObjectId": "obj_inline",
                    "reference": "kolo://obj/obj_inline",
                    "filename": "receipt.png",
                    "contentType": "image/png",
                },
            },
            1,
            expense_id="exp_inline",
        )

        self.assertEqual(expense["receipt_ref"], "obj_inline")
        self.assertEqual(expense["receipt_attachments"][0]["reference"], "kolo://obj/obj_inline")
        self.assertEqual(self.gateway.list_records(RECEIPT, status="stored")[0]["payload"]["expense_id"], "exp_inline")

    def test_receipt_reference_is_authorized_idempotent_and_locked_after_submit(self):
        self._capture()
        attachment = {
            "objectStoreObjectId": "obj_1",
            "reference": "kolo://obj/obj_1",
            "filename": "receipt.png",
            "contentType": "image/png",
            "sha256": "a" * 64,
        }

        attached = attach_receipt_reference(self.gateway, "exp_1", attachment, 1)
        repeated = attach_receipt_reference(self.gateway, "exp_1", attachment, 1)
        self.assertEqual(attached["status"], "attached")
        self.assertEqual(repeated["status"], "already_attached")
        self.assertEqual(len(self.gateway.get_record(EXPENSE, "exp_1")["payload"]["receipt_attachments"]), 1)

        with self.assertRaises(ExpenseFlowError) as ctx:
            attach_receipt_reference(self.gateway, "exp_1", {**attachment, "objectStoreObjectId": "obj_2"}, 7)
        self.assertEqual(ctx.exception.code, "unauthorized_receipt_actor")

        submit_report_for_approval(self.gateway, 1, ["exp_1"], report_id="er_1", approval_request_id="ar_1")
        with self.assertRaises(ExpenseFlowError) as ctx:
            attach_receipt_reference(
                self.gateway,
                "exp_1",
                {**attachment, "objectStoreObjectId": "obj_2", "sha256": "b" * 64},
                1,
            )
        self.assertEqual(ctx.exception.code, "receipt_locked")

    def test_upload_deduplicates_before_second_platform_upload(self):
        self._capture()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "media" / "inbound" / "receipt.png"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"synthetic receipt bytes")
            first = upload_and_attach_receipt(self.gateway, "exp_1", path, 1)
            second = upload_and_attach_receipt(self.gateway, "exp_1", path, 1)

        self.assertEqual(first["status"], "attached")
        self.assertEqual(second["status"], "already_attached")
        self.assertEqual(len(self.gateway.uploads), 1)
        self.assertEqual(len(self.gateway.list_records(RECEIPT, status="stored")), 1)
        saved = self.gateway.get_record(EXPENSE, "exp_1")["payload"]["receipt_attachments"][0]
        self.assertNotIn("file_path", saved)

    def test_failed_upload_is_reserved_and_not_retried(self):
        class FailingUploadGateway(FakeKoloGateway):
            def upload_file(self, file_path):
                raise ExpenseFlowError("kolo_command_failed", "Upload outcome is unknown.", retryable=True)

        gateway = FailingUploadGateway()
        upsert_user_profile(gateway, SUBMITTER)
        upsert_expense_settings(gateway, "default", {"expense_admin_user_ids": [99]})
        capture_expense(gateway, self._expense_data(), 1, expense_id="exp_failed")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "media" / "inbound" / "receipt.png"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"synthetic receipt bytes")
            with self.assertRaises(ExpenseFlowError):
                upload_and_attach_receipt(gateway, "exp_failed", path, 1)
            with self.assertRaises(ExpenseFlowError) as ctx:
                upload_and_attach_receipt(gateway, "exp_failed", path, 1)

        self.assertEqual(ctx.exception.code, "receipt_upload_incomplete")
        self.assertEqual(gateway.list_records(RECEIPT)[0]["status"], "upload_unknown")

    def test_upload_rejects_files_outside_kolo_inbound_staging(self):
        self._capture()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "receipt.png"
            path.write_bytes(b"synthetic receipt bytes")
            with self.assertRaises(ExpenseFlowError) as ctx:
                upload_and_attach_receipt(self.gateway, "exp_1", path, 1)

        self.assertEqual(ctx.exception.code, "receipt_path_not_allowed")
        self.assertEqual(self.gateway.uploads, [])

    def test_due_reminders_are_bounded_idempotent_and_escalated(self):
        self._capture()
        submit_report_for_approval(self.gateway, 1, ["exp_1"], report_id="er_1", approval_request_id="ar_1")
        initial_message_count = len(self.gateway.messages)

        first = send_due_approval_reminders(self.gateway, as_of="2099-01-01T00:00:00Z")
        self.assertEqual(first["sent"], ["ar_1"])
        self.assertEqual(len(self.gateway.messages), initial_message_count + 1)
        self.assertTrue(self.gateway.messages[-1]["message"].startswith("EXPENSEFLOW TEST"))

        repeated = send_due_approval_reminders(self.gateway, as_of="2099-01-01T00:00:00Z")
        self.assertEqual(repeated["sent"], [])
        self.assertEqual(len(self.gateway.messages), initial_message_count + 1)

        second = send_due_approval_reminders(self.gateway, as_of="2099-01-02T00:00:00Z")
        self.assertEqual(second["sent"], ["ar_1"])
        self.assertEqual(second["escalated"], [{"approval_request_id": "ar_1", "user_id": 99}])
        request = self.gateway.get_record(APPROVAL_REQUEST, "ar_1")["payload"]
        self.assertEqual(request["reminder_status"], "exhausted")
        self.assertEqual(len(self.gateway.list_records(NOTIFICATION_EVENT)), 3)

    def test_decision_resolves_reminders_and_completes_visibility_task(self):
        self._capture()
        submitted = submit_report_for_approval(
            self.gateway, 1, ["exp_1"], report_id="er_1", approval_request_id="ar_1"
        )
        task_id = submitted["approval_request"]["task_id"]
        result = decide_report_approval(self.gateway, "ar_1", 2, "approved", decision_id="ad_1")

        self.assertEqual(result["approval_request"]["reminder_status"], "resolved")
        self.assertEqual(result["approval_request"]["task_completion_status"], "completed")
        self.assertEqual(next(task for task in self.gateway.tasks if task["task_id"] == task_id)["status"], "completed")
        sweep = send_due_approval_reminders(self.gateway, as_of="2099-01-01T00:00:00Z")
        self.assertEqual(sweep["scanned"], 0)

    def test_reminder_blocks_and_escalates_when_approver_becomes_inactive(self):
        self._capture()
        submit_report_for_approval(self.gateway, 1, ["exp_1"], report_id="er_1", approval_request_id="ar_1")
        inactive = {**APPROVER, "status": "deactivated"}
        upsert_user_profile(self.gateway, inactive)

        sweep = send_due_approval_reminders(self.gateway, as_of="2099-01-01T00:00:00Z")

        self.assertIn({"approval_request_id": "ar_1", "reason": "approver_unavailable"}, sweep["skipped"])
        self.assertEqual(sweep["escalated"], [{"approval_request_id": "ar_1", "user_id": 99}])
        request = self.gateway.get_record(APPROVAL_REQUEST, "ar_1")["payload"]
        self.assertEqual(request["reminder_status"], "blocked_approver")

    def _capture(self):
        return capture_expense(self.gateway, self._expense_data(), 1, expense_id="exp_1")

    @staticmethod
    def _expense_data():
        return {
            "vendor": "Synthetic Vendor",
            "date": "2026-08-19",
            "amount": "12.34",
            "currency": "USD",
            "category": "Office Supplies",
        }


if __name__ == "__main__":
    unittest.main()
