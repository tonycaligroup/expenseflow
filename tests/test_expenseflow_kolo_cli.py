import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import expenseflow_kolo_cli


class ExpenseFlowKoloCliTests(unittest.TestCase):
    def test_export_sheets_passes_report_id(self):
        args = SimpleNamespace(report_id="er_1")

        with patch.object(expenseflow_kolo_cli, "export_approved_report_sheets", return_value={"ok": True}) as export:
            result = expenseflow_kolo_cli.cmd_export_sheets(args, gateway=object())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(export.call_args.args[1], "er_1")

    def test_sync_qbo_passes_routing_and_retry_options(self):
        args = SimpleNamespace(
            report_id="er_1",
            session_key="session_1",
            chat_id="chat_1",
            retry_terminal=True,
        )

        with patch.object(expenseflow_kolo_cli, "sync_approved_report_qbo", return_value={"ok": True}) as sync:
            result = expenseflow_kolo_cli.cmd_sync_qbo(args, gateway=object())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(sync.call_args.args[1], "er_1")
        self.assertEqual(sync.call_args.kwargs["session_key"], "session_1")
        self.assertEqual(sync.call_args.kwargs["chat_id"], "chat_1")
        self.assertTrue(sync.call_args.kwargs["retry_terminal"])

    def test_refresh_qbo_passes_org_id(self):
        args = SimpleNamespace(org_id="org_1")

        with patch.object(expenseflow_kolo_cli, "refresh_qbo_reference_cache", return_value={"ok": True}) as refresh:
            result = expenseflow_kolo_cli.cmd_refresh_qbo(args, gateway=object())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(refresh.call_args.args[1], "org_1")

    def test_setup_readiness_passes_org_and_integration_choice(self):
        args = SimpleNamespace(org_id="org_1", skip_integration_check=True)
        gateway = object()

        with patch.object(
            expenseflow_kolo_cli,
            "organization_setup_readiness",
            return_value={"status": "ready_with_warnings"},
        ) as readiness:
            result = expenseflow_kolo_cli.cmd_setup_readiness(args, gateway=gateway)

        self.assertEqual(result["status"], "ok")
        readiness.assert_called_once_with(gateway, "org_1", verify_destination=False)

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

    def test_attach_receipt_casts_actor_and_loads_attachment(self):
        args = SimpleNamespace(
            expense_id="exp_1",
            acting_user_id=272426,
            attachment='{"objectStoreObjectId":"obj_1","filename":"receipt.png"}',
            org_id="org_1",
        )

        with patch.object(expenseflow_kolo_cli, "attach_receipt_reference", return_value={"ok": True}) as attach:
            result = expenseflow_kolo_cli.cmd_attach_receipt(args, gateway=object())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(attach.call_args.args[2]["objectStoreObjectId"], "obj_1")
        self.assertEqual(attach.call_args.args[3], 272426)

    def test_send_reminders_passes_explicit_clock(self):
        args = SimpleNamespace(org_id="org_1", as_of="2026-08-19T12:00:00Z")

        with patch.object(expenseflow_kolo_cli, "send_due_approval_reminders", return_value={"ok": True}) as send:
            result = expenseflow_kolo_cli.cmd_send_reminders(args, gateway=object())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(send.call_args.args[1:], ("org_1", "2026-08-19T12:00:00Z"))


if __name__ == "__main__":
    unittest.main()
