import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.expense_core import create_expense, validate_expense


ACTIVE_SUBMITTER = {
    "user_id": 1,
    "display_name": "Tony",
    "status": "active",
}


class ExpenseValidationTests(unittest.TestCase):
    def test_valid_expense_is_normalized(self):
        expense = validate_expense(
            {
                "vendor": " Office Depot ",
                "date": "2026-08-18",
                "amount": "45.005",
                "currency": "usd",
                "tax": "0",
                "category": "Office Supplies",
            }
        )

        self.assertEqual(expense["vendor"], "Office Depot")
        self.assertEqual(expense["amount"], "45.01")
        self.assertEqual(expense["currency"], "USD")
        self.assertEqual(expense["tax"], "0.00")

    def test_rejects_invalid_date(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            validate_expense(
                {
                    "vendor": "Office Depot",
                    "date": "not-a-date",
                    "amount": "45.00",
                    "category": "Office Supplies",
                }
            )
        self.assertEqual(ctx.exception.code, "invalid_date")

    def test_rejects_negative_amount(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            validate_expense(
                {
                    "vendor": "Office Depot",
                    "date": "2026-08-18",
                    "amount": "-1.00",
                    "category": "Office Supplies",
                }
            )
        self.assertEqual(ctx.exception.code, "invalid_money")

    def test_rejects_unknown_category(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            validate_expense(
                {
                    "vendor": "Office Depot",
                    "date": "2026-08-18",
                    "amount": "45.00",
                    "category": "Magic Beans",
                }
            )
        self.assertEqual(ctx.exception.code, "invalid_category")

    def test_requires_receipt_above_threshold(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            validate_expense(
                {
                    "vendor": "Office Depot",
                    "date": "2026-08-18",
                    "amount": "45.00",
                    "category": "Office Supplies",
                },
                {"receipt_required_above": "25.00"},
            )
        self.assertEqual(ctx.exception.code, "receipt_required")

    def test_create_expense_holds_pending_onboarding_submitter(self):
        expense = create_expense(
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "45.00",
                "category": "Office Supplies",
            },
            {"user_id": 1, "display_name": "Tony", "status": "pending_admin_approval"},
            expense_id="exp_test",
        )
        self.assertEqual(expense["status"], "held_pending_onboarding")

    def test_create_expense_rejects_inactive_submitter(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            create_expense(
                {
                    "vendor": "Office Depot",
                    "date": "2026-08-18",
                    "amount": "45.00",
                    "category": "Office Supplies",
                },
                {"user_id": 1, "display_name": "Tony", "status": "suspended"},
            )
        self.assertEqual(ctx.exception.code, "inactive_submitter")


if __name__ == "__main__":
    unittest.main()
