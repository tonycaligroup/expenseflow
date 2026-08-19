import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.reminder_engine import (
    advance_reminder_schedule,
    initialize_reminder_schedule,
    is_reminder_due,
    normalize_reminder_settings,
)


class ReminderEngineTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "approval_reminders": {
                "enabled": True,
                "initial_delay_hours": 12,
                "interval_hours": 24,
                "max_attempts": 2,
            }
        }
        self.request = {
            "approval_request_id": "ar_1",
            "status": "pending",
            "created_at": "2026-08-19T08:00:00Z",
        }

    def test_initializes_and_advances_bounded_schedule(self):
        scheduled = initialize_reminder_schedule(self.request, self.settings)
        self.assertEqual(scheduled["next_reminder_at"], "2026-08-19T20:00:00Z")
        self.assertFalse(is_reminder_due(scheduled, "2026-08-19T19:59:59Z"))
        self.assertTrue(is_reminder_due(scheduled, "2026-08-19T20:00:00Z"))

        first = advance_reminder_schedule(scheduled, self.settings, "2026-08-19T20:00:00Z")
        self.assertEqual(first["next_reminder_at"], "2026-08-20T20:00:00Z")
        second = advance_reminder_schedule(first, self.settings, "2026-08-20T20:00:00Z")
        self.assertEqual(second["reminder_status"], "exhausted")
        self.assertIsNone(second["next_reminder_at"])

    def test_disabled_by_default(self):
        config = normalize_reminder_settings({})
        scheduled = initialize_reminder_schedule(self.request, {})
        self.assertFalse(config["enabled"])
        self.assertEqual(scheduled["reminder_status"], "disabled")

    def test_rejects_invalid_interval(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            normalize_reminder_settings({"approval_reminders": {"enabled": True, "interval_hours": 0}})
        self.assertEqual(ctx.exception.code, "invalid_reminder_settings")


if __name__ == "__main__":
    unittest.main()
