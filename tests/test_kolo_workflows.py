import csv
from io import StringIO
import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.kolo_gateway import FakeKoloGateway
from scripts.expenseflow.kolo_workflows import (
    APPROVAL_POLICY,
    APPROVAL_REQUEST,
    EXPENSE,
    EXPENSE_REPORT,
    capture_expense,
    decide_report_approval,
    export_approved_report_csv,
    submit_report_for_approval,
    upsert_approval_policy,
    upsert_expense_settings,
    upsert_user_profile,
)


SUBMITTER = {
    "user_id": 1,
    "display_name": "Tony",
    "department": "Engineering",
    "status": "active",
}

APPROVER = {
    "user_id": 2,
    "display_name": "Kendra",
    "status": "active",
    "can_approve": True,
    "approval_scope": {"departments": ["Engineering"], "max_amount": "1000.00"},
}


class KoloWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeKoloGateway()
        upsert_user_profile(self.gateway, SUBMITTER)
        upsert_user_profile(self.gateway, APPROVER)
        upsert_expense_settings(self.gateway, "default", {"receipt_required_above": "25.00"})
        upsert_approval_policy(self.gateway, "default", {"default_approver_user_id": 2})

    def test_capture_expense_writes_governed_record_and_audit(self):
        expense = capture_expense(
            self.gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "45.00",
                "currency": "usd",
                "category": "Office Supplies",
                "receipt_ref": "receipt_1",
            },
            submitter_user_id=1,
            expense_id="exp_test",
        )
        record = self.gateway.get_record(EXPENSE, "exp_test")

        self.assertEqual(expense["status"], "draft")
        self.assertEqual(record["status"], "draft")
        self.assertEqual(record["payload"]["currency"], "USD")
        self.assertIn("expenseflow:capture:exp_test", self.gateway.audit_events)

    def test_capture_detects_duplicate_candidate(self):
        first = {
            "vendor": "Office Depot",
            "date": "2026-08-18",
            "amount": "45.00",
            "currency": "USD",
            "category": "Office Supplies",
            "receipt_ref": "receipt_1",
        }
        capture_expense(self.gateway, first, 1, expense_id="exp_1")
        duplicate = capture_expense(self.gateway, {**first, "receipt_ref": "receipt_1"}, 1, expense_id="exp_2")

        self.assertEqual(duplicate["duplicate_candidates"], [{"expense_id": "exp_1", "reason": "same_receipt"}])

    def test_submit_report_creates_approval_request_message_task_and_submitted_expenses(self):
        capture_expense(
            self.gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "45.00",
                "currency": "USD",
                "category": "Office Supplies",
                "receipt_ref": "receipt_1",
            },
            1,
            expense_id="exp_1",
        )
        result = submit_report_for_approval(
            self.gateway,
            1,
            ["exp_1"],
            report_id="er_test",
            approval_request_id="ar_test",
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.gateway.get_record(EXPENSE_REPORT, "er_test")["status"], "pending_approval")
        self.assertEqual(self.gateway.get_record(EXPENSE, "exp_1")["status"], "submitted")
        self.assertEqual(self.gateway.get_record(APPROVAL_REQUEST, "ar_test")["payload"]["backchannel_queue_id"], self.gateway.messages[0]["queueId"])
        self.assertEqual(len(self.gateway.tasks), 1)

    def test_submit_report_holds_when_no_approver(self):
        self.gateway.records.pop((APPROVAL_POLICY, "default"))
        capture_expense(
            self.gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "45.00",
                "currency": "USD",
                "category": "Office Supplies",
                "receipt_ref": "receipt_1",
            },
            1,
            expense_id="exp_1",
        )
        result = submit_report_for_approval(self.gateway, 1, ["exp_1"], report_id="er_held")

        self.assertEqual(result["status"], "held_pending_manager")
        self.assertEqual(self.gateway.get_record(EXPENSE_REPORT, "er_held")["status"], "held_pending_manager")
        self.assertEqual(self.gateway.get_record(EXPENSE, "exp_1")["status"], "held_pending_manager")
        self.assertEqual(self.gateway.messages, [])

    def test_approval_decision_updates_records(self):
        self._submit_one_expense()
        result = decide_report_approval(self.gateway, "ar_test", 2, "approved", decision_id="ad_test")

        self.assertEqual(result["report"]["status"], "approved")
        self.assertEqual(self.gateway.get_record(EXPENSE_REPORT, "er_test")["status"], "approved")
        self.assertEqual(self.gateway.get_record(EXPENSE, "exp_1")["status"], "approved")

    def test_wrong_approver_is_rejected(self):
        self._submit_one_expense()
        with self.assertRaises(ExpenseFlowError) as ctx:
            decide_report_approval(self.gateway, "ar_test", 3, "approved")
        self.assertEqual(ctx.exception.code, "wrong_approver")

    def test_export_approved_report_csv_updates_statuses(self):
        self._submit_one_expense()
        decide_report_approval(self.gateway, "ar_test", 2, "approved", decision_id="ad_test")
        export = export_approved_report_csv(self.gateway, "er_test")
        rows = list(csv.DictReader(StringIO(export["csv"])))

        self.assertEqual(export["status"], "ok")
        self.assertEqual(rows[0]["expense_id"], "exp_1")
        self.assertEqual(self.gateway.get_record(EXPENSE_REPORT, "er_test")["status"], "exported")
        self.assertEqual(self.gateway.get_record(EXPENSE, "exp_1")["status"], "exported")

    def test_export_unapproved_report_is_blocked(self):
        self._submit_one_expense()
        with self.assertRaises(ExpenseFlowError) as ctx:
            export_approved_report_csv(self.gateway, "er_test")
        self.assertEqual(ctx.exception.code, "report_not_approved")

    def test_audit_log_is_idempotent(self):
        first = self.gateway.log_action("skill.test", "Same event", "same-key")
        second = self.gateway.log_action("skill.test", "Same event", "same-key")

        self.assertEqual(first["auditEventId"], second["auditEventId"])

    def _submit_one_expense(self):
        capture_expense(
            self.gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "45.00",
                "currency": "USD",
                "category": "Office Supplies",
                "receipt_ref": "receipt_1",
            },
            1,
            expense_id="exp_1",
        )
        return submit_report_for_approval(
            self.gateway,
            1,
            ["exp_1"],
            report_id="er_test",
            approval_request_id="ar_test",
        )


if __name__ == "__main__":
    unittest.main()
