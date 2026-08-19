import unittest

from scripts.expenseflow.approval_engine import create_approval_request, record_approval_decision
from scripts.expenseflow.errors import ExpenseFlowError


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

DRAFT_REPORT = {
    "report_id": "er_test",
    "submitter_user_id": 1,
    "submitter_name": "Tony",
    "totals_by_currency": {"USD": "45.00"},
    "status": "draft",
    "submitted_at": None,
    "approved_at": None,
    "exported_at": None,
    "synced_at": None,
}

SUBMITTED_EXPENSE = {
    "expense_id": "exp_1",
    "status": "submitted",
}


class ApprovalEngineTests(unittest.TestCase):
    def test_creates_approval_request_and_submits_report(self):
        result = create_approval_request(
            DRAFT_REPORT,
            SUBMITTER,
            {"approval_policy": {"default_approver_user_id": 2}},
            [APPROVER],
            request_id="ar_test",
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["report"]["status"], "pending_approval")
        self.assertEqual(result["approval_request"]["approval_request_id"], "ar_test")
        self.assertEqual(result["approval_request"]["approver_user_id"], 2)

    def test_holds_when_no_valid_approver(self):
        result = create_approval_request(
            DRAFT_REPORT,
            SUBMITTER,
            {"approval_policy": {"default_approver_user_id": 99}},
            [],
        )

        self.assertEqual(result["status"], "held_pending_manager")
        self.assertEqual(result["report"]["status"], "held_pending_manager")
        self.assertIsNone(result["approval_request"])

    def test_records_approval_decision(self):
        request_result = create_approval_request(
            DRAFT_REPORT,
            SUBMITTER,
            {"approval_policy": {"default_approver_user_id": 2}},
            [APPROVER],
            request_id="ar_test",
        )
        decision_result = record_approval_decision(
            request_result["report"],
            [SUBMITTED_EXPENSE],
            request_result["approval_request"],
            2,
            "approved",
            decision_id="ad_test",
        )

        self.assertEqual(decision_result["report"]["status"], "approved")
        self.assertEqual(decision_result["expenses"][0]["status"], "approved")
        self.assertEqual(decision_result["approval_decision"]["approval_decision_id"], "ad_test")

    def test_reject_requires_note(self):
        request_result = create_approval_request(
            DRAFT_REPORT,
            SUBMITTER,
            {"approval_policy": {"default_approver_user_id": 2}},
            [APPROVER],
        )
        with self.assertRaises(ExpenseFlowError) as ctx:
            record_approval_decision(
                request_result["report"],
                [SUBMITTED_EXPENSE],
                request_result["approval_request"],
                2,
                "rejected",
            )
        self.assertEqual(ctx.exception.code, "missing_rejection_note")

    def test_rejects_wrong_approver(self):
        request_result = create_approval_request(
            DRAFT_REPORT,
            SUBMITTER,
            {"approval_policy": {"default_approver_user_id": 2}},
            [APPROVER],
        )
        with self.assertRaises(ExpenseFlowError) as ctx:
            record_approval_decision(
                request_result["report"],
                [SUBMITTED_EXPENSE],
                request_result["approval_request"],
                3,
                "approved",
            )
        self.assertEqual(ctx.exception.code, "wrong_approver")


if __name__ == "__main__":
    unittest.main()
