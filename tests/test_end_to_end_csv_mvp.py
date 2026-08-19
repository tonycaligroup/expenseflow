import csv
from io import StringIO
import unittest

from scripts.expenseflow.approval_engine import create_approval_request, record_approval_decision
from scripts.expenseflow.csv_export import generate_report_csv
from scripts.expenseflow.expense_core import create_expense
from scripts.expenseflow.report_engine import create_report


class EndToEndCsvMvpTests(unittest.TestCase):
    def test_draft_expense_to_approved_csv(self):
        submitter = {
            "user_id": 1,
            "display_name": "Tony",
            "department": "Engineering",
            "status": "active",
        }
        approver = {
            "user_id": 2,
            "display_name": "Kendra",
            "status": "active",
            "can_approve": True,
            "approval_scope": {"departments": ["Engineering"], "max_amount": "1000.00"},
        }
        expense = create_expense(
            {
                "vendor": "=Office Depot",
                "date": "2026-08-18",
                "amount": "45.005",
                "currency": "usd",
                "category": "Office Supplies",
                "payment_method": "Personal Card",
                "receipt_ref": "receipt_1",
            },
            submitter,
            expense_id="exp_test",
        )
        report = create_report([expense], submitter, title="August Expenses", report_id="er_test")

        approval = create_approval_request(
            report,
            submitter,
            {"approval_policy": {"default_approver_user_id": 2}},
            [approver],
            request_id="ar_test",
        )
        submitted_expense = {**expense, "status": "submitted"}
        decision = record_approval_decision(
            approval["report"],
            [submitted_expense],
            approval["approval_request"],
            2,
            "approved",
            decision_id="ad_test",
        )
        exported = generate_report_csv(decision["report"], decision["expenses"])
        rows = list(csv.DictReader(StringIO(exported)))

        self.assertEqual(decision["report"]["status"], "approved")
        self.assertEqual(rows[0]["vendor"], "'=Office Depot")
        self.assertEqual(rows[0]["amount"], "45.01")


if __name__ == "__main__":
    unittest.main()
