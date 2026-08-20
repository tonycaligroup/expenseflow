import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from scripts import expenseflow_kolo_cli


class ExpenseFlowKoloCliTests(unittest.TestCase):
    def test_compact_response_keeps_actionable_fields_and_omits_record_detail(self):
        payload = {
            "status": "ok",
            "expense": {
                "expense_id": "exp_1",
                "status": "draft",
                "merchant_name": "Cafe",
                "amount": "12.50",
                "currency": "USD",
                "duplicate_candidates": ["exp_old"],
                "receipt_attachments": [{"object_store_object_id": "secret_detail"}],
                "internal_trace": "large diagnostic value",
            },
        }

        compact = expenseflow_kolo_cli.compact_cli_response(payload, "capture-expense")

        self.assertEqual(compact["expense"]["expense_id"], "exp_1")
        self.assertEqual(compact["expense"]["merchant_name"], "Cafe")
        self.assertNotIn("receipt_attachments", compact["expense"])
        self.assertNotIn("internal_trace", compact["expense"])

    def test_compact_readiness_keeps_only_checks_needing_attention(self):
        payload = {
            "status": "ok",
            "result": {
                "status": "not_ready",
                "can_launch_pilot": False,
                "summary": {"blocker_count": 1, "warning_count": 1, "pass_count": 8},
                "checks": [
                    {"id": "org", "status": "pass", "message": "Organization is configured."},
                    {
                        "id": "destination",
                        "status": "blocker",
                        "message": "Destination is missing.",
                        "next_action": "Configure a destination.",
                    },
                ],
                "next_action": "Configure a destination.",
            },
        }

        compact = expenseflow_kolo_cli.compact_cli_response(payload, "setup-readiness")

        self.assertEqual(compact["result"]["summary"]["pass_count"], 8)
        self.assertEqual(len(compact["result"]["checks"]), 1)
        self.assertEqual(compact["result"]["checks"][0]["status"], "blocker")

    def test_main_verbose_preserves_complete_workflow_result(self):
        workflow_result = {
            "expense_id": "exp_1",
            "status": "draft",
            "internal_trace": "diagnostic detail",
        }
        output = StringIO()
        with (
            patch.object(expenseflow_kolo_cli, "KoloCommandGateway", return_value=object()),
            patch.object(expenseflow_kolo_cli, "capture_expense", return_value=workflow_result),
            redirect_stdout(output),
        ):
            exit_code = expenseflow_kolo_cli.main(
                [
                    "--org-id",
                    "org_1",
                    "--verbose",
                    "capture-expense",
                    "--submitter-user-id",
                    "1",
                    "--expense",
                    "{}",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["expense"]["internal_trace"], "diagnostic detail")

    def test_csv_content_is_preserved_in_compact_export_output(self):
        payload = {
            "status": "ok",
            "result": {
                "status": "ok",
                "report": {"report_id": "er_1", "status": "exported", "private": "omit"},
                "csv": "expense_id,amount\nexp_1,12.50\n",
            },
        }

        compact = expenseflow_kolo_cli.compact_cli_response(payload, "export-csv")

        self.assertEqual(compact["result"]["csv"], payload["result"]["csv"])
        self.assertNotIn("private", compact["result"]["report"])

    def test_compact_export_summarizes_successful_items(self):
        payload = {
            "status": "ok",
            "result": {
                "status": "ok",
                "items": [
                    {"export_item_id": "item_1", "status": "confirmed", "row_payload": ["large"]},
                    {"export_item_id": "item_2", "status": "unknown", "error_code": "write_unknown"},
                ],
            },
        }

        compact = expenseflow_kolo_cli.compact_cli_response(payload, "export-sheets")["result"]

        self.assertEqual(compact["item_count"], 2)
        self.assertEqual(compact["item_status_counts"], {"confirmed": 1, "unknown": 1})
        self.assertEqual(compact["items"], [{"export_item_id": "item_2", "status": "unknown", "error_code": "write_unknown"}])

    def test_compact_response_bounds_large_id_lists(self):
        payload = {
            "status": "ok",
            "result": {
                "report": {
                    "report_id": "er_1",
                    "expense_ids": [f"exp_{index}" for index in range(25)],
                    "totals": {"USD": "250.00"},
                }
            },
        }

        report = expenseflow_kolo_cli.compact_cli_response(payload, "submit-report")["result"]["report"]

        self.assertEqual(len(report["expense_ids"]), 10)
        self.assertEqual(report["expense_ids_count"], 25)
        self.assertEqual(report["totals"], {"USD": "250.00"})

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

    def test_discover_org_uses_one_deterministic_gateway_operation(self):
        gateway = SimpleNamespace(
            discover_organization=lambda: {
                "org_id": "org_1",
                "member_count": 2,
                "source": "kolo list-peers",
            }
        )

        result = expenseflow_kolo_cli.cmd_discover_org(SimpleNamespace(), gateway)

        self.assertEqual(
            result,
            {
                "status": "ok",
                "result": {
                    "org_id": "org_1",
                    "member_count": 2,
                    "source": "kolo list-peers",
                },
            },
        )

    def test_main_discover_org_prints_only_compact_scope(self):
        gateway = SimpleNamespace(
            discover_organization=lambda: {
                "org_id": "org_1",
                "member_count": 2,
                "source": "kolo list-peers",
            }
        )
        output = StringIO()

        with (
            patch.object(expenseflow_kolo_cli, "KoloCommandGateway", return_value=gateway),
            redirect_stdout(output),
        ):
            exit_code = expenseflow_kolo_cli.main(["discover-org"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "status": "ok",
                "result": {
                    "org_id": "org_1",
                    "member_count": 2,
                    "source": "kolo list-peers",
                },
            },
        )

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

    def test_inbound_decision_passes_platform_user_and_correlation(self):
        args = SimpleNamespace(
            from_user_id=272086,
            sender_id=None,
            decision="approved",
            org_id="org_1",
            from_org_id="org_1",
            approval_request_id="ar_1",
            queue_id="queue_1",
            note=None,
            decision_id=None,
        )

        with patch.object(
            expenseflow_kolo_cli,
            "decide_report_approval_from_user",
            return_value={"ok": True},
        ) as decide:
            result = expenseflow_kolo_cli.cmd_decide_report_from_sender(args, gateway=object())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(decide.call_args.args[1:4], (272086, "approved", "org_1"))
        self.assertEqual(decide.call_args.kwargs["from_org_id"], "org_1")
        self.assertEqual(decide.call_args.kwargs["approval_request_id"], "ar_1")

    def test_inbound_decision_keeps_legacy_sender_support(self):
        args = SimpleNamespace(
            from_user_id=None,
            sender_id="sender-uuid",
            decision="approved",
            org_id="org_1",
            from_org_id=None,
            approval_request_id="ar_1",
            queue_id="queue_1",
            note=None,
            decision_id=None,
        )

        with patch.object(
            expenseflow_kolo_cli,
            "decide_report_approval_from_sender",
            return_value={"ok": True},
        ) as decide:
            result = expenseflow_kolo_cli.cmd_decide_report_from_sender(args, gateway=object())

        self.assertEqual(result["status"], "ok")
        self.assertEqual(decide.call_args.args[1:4], ("sender-uuid", "approved", "org_1"))

    def test_reconcile_decision_requires_explicit_stale_confirmation(self):
        args = SimpleNamespace(
            approval_request_id="ar_1",
            org_id="org_1",
            confirm_stale_claim=True,
            as_of="2026-08-20T00:00:00Z",
        )

        with patch.object(
            expenseflow_kolo_cli,
            "reconcile_approval_decision",
            return_value={"status": "reconciled"},
        ) as reconcile:
            result = expenseflow_kolo_cli.cmd_reconcile_approval_decision(args, gateway=object())

        self.assertEqual(result["status"], "ok")
        self.assertTrue(reconcile.call_args.kwargs["confirm_stale_claim"])
        self.assertEqual(reconcile.call_args.kwargs["as_of"], "2026-08-20T00:00:00Z")

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
