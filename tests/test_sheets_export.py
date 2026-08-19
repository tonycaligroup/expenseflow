import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.sheets_export import (
    SHEET_COLUMNS,
    build_sheet_row,
    column_label,
    make_row_id,
    parse_updated_range,
    quote_sheet_title,
)


class SheetsExportTests(unittest.TestCase):
    def test_row_ids_are_deterministic_and_destination_scoped(self):
        first = make_row_id("org_1", "sheet_1", "exp_1")

        self.assertEqual(first, make_row_id("org_1", "sheet_1", "exp_1"))
        self.assertNotEqual(first, make_row_id("org_1", "sheet_2", "exp_1"))

    def test_a1_helpers_quote_titles_and_parse_returned_row(self):
        self.assertEqual(quote_sheet_title("CFO's Export"), "'CFO''s Export'")
        self.assertEqual(column_label(15), "O")
        self.assertEqual(parse_updated_range("'CFO''s Export'!A12:O12", "CFO's Export"), 12)

    def test_sheet_row_uses_raw_values_and_does_not_leak_staged_receipt_path(self):
        report = {
            "report_id": "er_1",
            "submitter_user_id": 7,
            "submitter_name": "Tony",
            "status": "approved",
            "approved_at": "2026-08-19T12:00:00Z",
        }
        expense = {
            "expense_id": "exp_1",
            "status": "approved",
            "date": "2026-08-19",
            "vendor": "=Formula stays literal with RAW",
            "category": "Office Supplies",
            "amount": "12.3",
            "currency": "usd",
            "tax": "0",
            "receipt_ref": "/home/kolo/.openclaw/media/inbound/receipt.png",
        }

        row = build_sheet_row(report, expense, "org_1", "sheet_1")

        self.assertEqual(len(row), len(SHEET_COLUMNS))
        self.assertEqual(row[SHEET_COLUMNS.index("vendor")], "=Formula stays literal with RAW")
        self.assertEqual(row[SHEET_COLUMNS.index("amount")], "12.30")
        self.assertEqual(row[SHEET_COLUMNS.index("receipt_ref")], "")

    def test_parse_updated_range_rejects_wrong_tab(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            parse_updated_range("Other!A2:O2", "ExpenseFlow")
        self.assertEqual(ctx.exception.code, "unexpected_sheet_write")


if __name__ == "__main__":
    unittest.main()
