import unittest

from scripts.expenseflow.expense_core import detect_duplicates


class DuplicateDetectionTests(unittest.TestCase):
    def test_detects_same_receipt(self):
        duplicates = detect_duplicates(
            {"receipt_ref": "obj_1", "vendor": "A", "date": "2026-08-18", "amount": "10.00", "currency": "USD"},
            [{"expense_id": "exp_1", "receipt_ref": "obj_1", "status": "draft"}],
        )
        self.assertEqual(duplicates, [{"expense_id": "exp_1", "reason": "same_receipt"}])

    def test_detects_same_vendor_date_amount(self):
        duplicates = detect_duplicates(
            {"vendor": "Office Depot", "date": "2026-08-18", "amount": "10.00", "currency": "USD"},
            [{"expense_id": "exp_1", "vendor": "office depot", "date": "2026-08-18", "amount": "10.00", "currency": "USD"}],
        )
        self.assertEqual(duplicates, [{"expense_id": "exp_1", "reason": "same_vendor_date_amount"}])

    def test_ignores_rejected_expenses(self):
        duplicates = detect_duplicates(
            {"receipt_ref": "obj_1", "vendor": "A", "date": "2026-08-18", "amount": "10.00", "currency": "USD"},
            [{"expense_id": "exp_1", "receipt_ref": "obj_1", "status": "rejected"}],
        )
        self.assertEqual(duplicates, [])


if __name__ == "__main__":
    unittest.main()
