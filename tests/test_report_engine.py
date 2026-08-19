import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.report_engine import create_report


class ReportEngineTests(unittest.TestCase):
    def test_create_report_calculates_totals_by_currency(self):
        report = create_report(
            [
                {"expense_id": "exp_1", "status": "draft", "amount": "50.00", "currency": "USD"},
                {"expense_id": "exp_2", "status": "draft", "amount": "100.00", "currency": "EUR"},
            ],
            {"user_id": 1, "display_name": "Tony"},
            title="August Expenses",
            report_id="er_test",
        )

        self.assertEqual(report["report_id"], "er_test")
        self.assertEqual(report["totals_by_currency"], {"EUR": "100.00", "USD": "50.00"})

    def test_create_report_rejects_empty_report(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            create_report([], {"user_id": 1})
        self.assertEqual(ctx.exception.code, "empty_report")

    def test_create_report_rejects_non_draft_expense(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            create_report(
                [{"expense_id": "exp_1", "status": "submitted", "amount": "50.00", "currency": "USD"}],
                {"user_id": 1},
            )
        self.assertEqual(ctx.exception.code, "invalid_report_expense_status")


if __name__ == "__main__":
    unittest.main()
