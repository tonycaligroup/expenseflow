import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.report_engine import transition_report
from scripts.expenseflow.status import validate_transition


class StatusTransitionTests(unittest.TestCase):
    def test_valid_expense_transition(self):
        self.assertTrue(validate_transition("expense", "draft", "submitted"))

    def test_invalid_expense_transition(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            validate_transition("expense", "approved", "draft")
        self.assertEqual(ctx.exception.code, "invalid_transition")

    def test_report_transition_sets_timestamp(self):
        report = {"status": "draft", "submitted_at": None}
        updated = transition_report(report, "pending_approval")
        self.assertEqual(updated["status"], "pending_approval")
        self.assertIsNotNone(updated["submitted_at"])


if __name__ == "__main__":
    unittest.main()
