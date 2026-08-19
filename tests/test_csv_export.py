import csv
from io import StringIO
import unittest

from scripts.expenseflow.csv_export import escape_csv_cell, generate_report_csv
from scripts.expenseflow.errors import ExpenseFlowError


APPROVED_REPORT = {
    "report_id": "er_test",
    "submitter_user_id": 1,
    "submitter_name": "Tony",
    "status": "approved",
}

APPROVED_EXPENSE = {
    "expense_id": "exp_1",
    "submitter_user_id": 1,
    "submitter_name": "Tony",
    "date": "2026-08-18",
    "vendor": "Office Depot",
    "category": "Office Supplies",
    "amount": "45.005",
    "currency": "usd",
    "tax": "0",
    "payment_method": "Personal Card",
    "receipt_ref": "receipt_1",
    "note": "Printer paper",
    "status": "approved",
}


class CsvExportTests(unittest.TestCase):
    def test_escapes_spreadsheet_formula_prefixes(self):
        self.assertEqual(escape_csv_cell("=IMPORTXML()"), "'=IMPORTXML()")
        self.assertEqual(escape_csv_cell("+SUM(A1:A2)"), "'+SUM(A1:A2)")
        self.assertEqual(escape_csv_cell("Office Depot"), "Office Depot")

    def test_generates_csv_for_approved_report(self):
        exported = generate_report_csv(APPROVED_REPORT, [APPROVED_EXPENSE])
        rows = list(csv.DictReader(StringIO(exported)))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["report_id"], "er_test")
        self.assertEqual(rows[0]["amount"], "45.01")
        self.assertEqual(rows[0]["currency"], "USD")

    def test_rejects_unapproved_report(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            generate_report_csv({**APPROVED_REPORT, "status": "pending_approval"}, [APPROVED_EXPENSE])
        self.assertEqual(ctx.exception.code, "report_not_approved")

    def test_rejects_unapproved_expense(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            generate_report_csv(APPROVED_REPORT, [{**APPROVED_EXPENSE, "status": "submitted"}])
        self.assertEqual(ctx.exception.code, "expense_not_approved")


if __name__ == "__main__":
    unittest.main()
