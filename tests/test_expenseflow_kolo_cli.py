import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import expenseflow_kolo_cli


class ExpenseFlowKoloCliTests(unittest.TestCase):
    def test_decide_report_casts_approver_user_id_to_int(self):
        args = SimpleNamespace(
            approval_request_id="ar_1",
            approver_user_id="272086",
            decision="approved",
            note=None,
            decision_id=None,
        )

        with patch.object(expenseflow_kolo_cli, "decide_report_approval", return_value={"ok": True}) as decide:
            result = expenseflow_kolo_cli.cmd_decide_report(args, gateway=object())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(decide.call_args.args[2], 272086)


if __name__ == "__main__":
    unittest.main()
