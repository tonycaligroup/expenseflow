import unittest
from unittest.mock import patch

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.kolo_command_gateway import KoloCommandGateway, _run_command


class ScriptedRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        if not self.responses:
            raise AssertionError(f"No scripted response for {command}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class KoloCommandGatewayTests(unittest.TestCase):
    def test_sheets_calls_delegate_to_narrow_adapter(self):
        sheets = _SheetsGateway()
        gateway = KoloCommandGateway(runner=ScriptedRunner([]), sheets_gateway=sheets)

        result = gateway.sheets_append_values("sheet_1", "ExpenseFlow!A1:O1", [["row"]])

        self.assertEqual(result, {"updates": {"updatedRange": "ExpenseFlow!A2:O2"}})
        self.assertEqual(sheets.calls, [("append", "sheet_1", "ExpenseFlow!A1:O1", [["row"]])])

    def test_upsert_record_calls_kolo_and_sets_status_when_needed(self):
        runner = ScriptedRunner(
            [
                {
                    "created": True,
                    "record": {
                        "recordType": "skill.expense",
                        "externalId": "exp_1",
                        "payload": {"expense_id": "exp_1", "status": "draft"},
                        "status": "active",
                        "schemaVersion": 1,
                    },
                },
                {"recordType": "skill.expense", "externalId": "exp_1", "payload": {"status": "draft"}, "status": "draft"},
            ]
        )
        gateway = KoloCommandGateway(runner=runner)

        result = gateway.upsert_record("skill.expense", "exp_1", {"expense_id": "exp_1", "status": "draft"}, "draft")

        self.assertTrue(result["created"])
        self.assertEqual(result["record"]["status"], "draft")
        self.assertEqual(runner.commands[0][:5], ["kolo", "record-upsert", "--record-type", "skill.expense", "--external-id"])
        self.assertEqual(runner.commands[1], ["kolo", "record-status", "--record-type", "skill.expense", "--external-id", "exp_1", "--status", "draft"])

    def test_get_record_normalizes_camel_case_response(self):
        gateway = KoloCommandGateway(
            runner=ScriptedRunner(
                [
                    {
                        "recordType": "skill.user_profile",
                        "externalId": "1",
                        "payload": {"user_id": 1, "status": "active"},
                        "status": "active",
                        "schemaVersion": 1,
                    }
                ]
            )
        )

        record = gateway.get_record("skill.user_profile", "1")

        self.assertEqual(record["record_type"], "skill.user_profile")
        self.assertEqual(record["external_id"], "1")
        self.assertEqual(record["payload"]["user_id"], 1)

    def test_list_records_accepts_list_response(self):
        gateway = KoloCommandGateway(
            runner=ScriptedRunner(
                [
                    [
                        {
                            "recordType": "skill.expense",
                            "externalId": "exp_1",
                            "payload": {"expense_id": "exp_1", "status": "draft"},
                            "status": "draft",
                        }
                    ]
                ]
            )
        )

        records = gateway.list_records("skill.expense", status="draft")

        self.assertEqual(records[0]["external_id"], "exp_1")

    def test_list_records_fetches_payloads_when_list_omits_them(self):
        runner = ScriptedRunner(
            [
                {
                    "status": "ok",
                    "records": [
                        {
                            "record_type": "skill.user_profile",
                            "external_id": "2",
                            "status": "active",
                            "schema_version": 1,
                        }
                    ],
                },
                {
                    "status": "ok",
                    "record": {
                        "record_type": "skill.user_profile",
                        "external_id": "2",
                        "payload": {"user_id": 2, "status": "active"},
                        "status": "active",
                        "schema_version": 1,
                    },
                },
            ]
        )
        gateway = KoloCommandGateway(runner=runner)

        records = gateway.list_records("skill.user_profile", status="active")

        self.assertEqual(records[0]["payload"]["user_id"], 2)
        self.assertEqual(
            runner.commands[1],
            ["kolo", "record-get", "--record-type", "skill.user_profile", "--external-id", "2"],
        )

    def test_contact_agent_requires_queue_id(self):
        gateway = KoloCommandGateway(runner=ScriptedRunner([{"status": "ok"}]))

        with self.assertRaises(ExpenseFlowError) as ctx:
            gateway.contact_agent(2, "Please approve")
        self.assertEqual(ctx.exception.code, "missing_queue_id")

    def test_list_peers_normalizes_platform_fields(self):
        runner = ScriptedRunner(
            [{"peers": [{"userId": "7", "displayName": "New Employee", "orgId": "org_1"}]}]
        )
        gateway = KoloCommandGateway(runner=runner)

        peers = gateway.list_peers()

        self.assertEqual(peers, [{"user_id": 7, "display_name": "New Employee", "org_id": "org_1"}])
        self.assertEqual(runner.commands[0], ["kolo", "list-peers"])

    def test_create_task_accepts_task_id_variants(self):
        gateway = KoloCommandGateway(runner=ScriptedRunner([{"task": {"task_id": "task_1", "status": "not_started"}}]))

        task = gateway.create_task("Review", 2, {"report_id": "er_1"})

        self.assertEqual(task["task_id"], "task_1")
        self.assertEqual(task["status"], "not_started")

    def test_create_task_does_not_send_unsupported_metadata_flag(self):
        runner = ScriptedRunner([{"taskId": "task_1", "status": "open"}])
        gateway = KoloCommandGateway(runner=runner)

        gateway.create_task("Review", 2, {"report_id": "er_1"})

        self.assertEqual(runner.commands[0], ["kolo", "task-create", "--title", "Review", "--user", "2"])

    def test_complete_task_uses_verified_task_id_shape(self):
        runner = ScriptedRunner([{"task": {"taskId": "task_1", "status": "completed"}}])
        gateway = KoloCommandGateway(runner=runner)

        result = gateway.complete_task("task_1")

        self.assertEqual(result["task_id"], "task_1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(runner.commands[0], ["kolo", "task-complete", "--task-id", "task_1"])

    def test_upload_file_normalizes_object_store_response(self):
        runner = ScriptedRunner(
            [{"objectStoreObjectId": "01a-object", "reference": "kolo://obj/01a-object"}]
        )
        gateway = KoloCommandGateway(runner=runner)

        result = gateway.upload_file("media/inbound/receipt.png")

        self.assertEqual(result["object_store_object_id"], "01a-object")
        self.assertEqual(result["reference"], "kolo://obj/01a-object")
        self.assertEqual(runner.commands[0], ["kolo", "file-upload", "media/inbound/receipt.png"])

    def test_upload_file_preserves_live_object_reference(self):
        runner = ScriptedRunner(
            [{"objectStoreObjectId": "01a-object", "reference": "kolo-object://01a-object"}]
        )
        gateway = KoloCommandGateway(runner=runner)

        result = gateway.upload_file("media/inbound/receipt.jpg")

        self.assertEqual(result["object_store_object_id"], "01a-object")
        self.assertEqual(result["reference"], "kolo-object://01a-object")

    def test_upload_file_requires_object_store_id(self):
        gateway = KoloCommandGateway(runner=ScriptedRunner([{"status": "ok"}]))

        with self.assertRaises(ExpenseFlowError) as ctx:
            gateway.upload_file("media/inbound/receipt.png")
        self.assertEqual(ctx.exception.code, "missing_receipt_object_id")

    def test_log_action_requires_audit_event_id(self):
        gateway = KoloCommandGateway(runner=ScriptedRunner([{"status": "ok"}]))

        with self.assertRaises(ExpenseFlowError) as ctx:
            gateway.log_action("skill.expense", "Captured", "key")
        self.assertEqual(ctx.exception.code, "missing_audit_event_id")

    def test_log_action_sends_details_flag_for_metadata(self):
        runner = ScriptedRunner([{"auditEventId": "audit_1"}])
        gateway = KoloCommandGateway(runner=runner)

        gateway.log_action("skill.expense", "Captured", "key", {"expense_id": "exp_1"})

        self.assertIn("--details", runner.commands[0])
        self.assertNotIn("--metadata", runner.commands[0])

    def test_run_command_rejects_invalid_json(self):
        completed = _Completed(returncode=0, stdout="not json", stderr="")
        with patch("subprocess.run", return_value=completed):
            with self.assertRaises(ExpenseFlowError) as ctx:
                _run_command(["kolo", "record-get"])
        self.assertEqual(ctx.exception.code, "invalid_kolo_json")

    def test_run_command_converts_nonzero_exit(self):
        completed = _Completed(returncode=1, stdout="", stderr="backend unavailable")
        with patch("subprocess.run", return_value=completed):
            with self.assertRaises(ExpenseFlowError) as ctx:
                _run_command(["kolo", "record-get"])
        self.assertEqual(ctx.exception.code, "kolo_command_failed")
        self.assertTrue(ctx.exception.retryable)

    def test_run_command_maps_nested_record_get_404_to_record_not_found(self):
        completed = _Completed(
            returncode=1,
            stdout='{"detail":{"detail":{"error":"not_found"}},"status":404}',
            stderr="",
        )
        command = [
            "kolo",
            "record-get",
            "--record-type",
            "skill.user_profile",
            "--external-id",
            "272426",
        ]

        with patch("subprocess.run", return_value=completed):
            with self.assertRaises(ExpenseFlowError) as ctx:
                _run_command(command)

        self.assertEqual(ctx.exception.code, "record_not_found")
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(ctx.exception.details["record_type"], "skill.user_profile")
        self.assertEqual(ctx.exception.details["external_id"], "272426")

    def test_run_command_does_not_hide_non_record_get_404(self):
        completed = _Completed(returncode=1, stdout='{"error":"not_found","status":404}', stderr="")

        with patch("subprocess.run", return_value=completed):
            with self.assertRaises(ExpenseFlowError) as ctx:
                _run_command(["kolo", "record-upsert"])

        self.assertEqual(ctx.exception.code, "kolo_command_failed")


class _SheetsGateway:
    def __init__(self):
        self.calls = []

    def append_values(self, spreadsheet_id, a1_range, values):
        self.calls.append(("append", spreadsheet_id, a1_range, values))
        return {"updates": {"updatedRange": "ExpenseFlow!A2:O2"}}


class _Completed:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


if __name__ == "__main__":
    unittest.main()
