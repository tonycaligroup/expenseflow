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

    def test_contact_agent_requires_queue_id(self):
        gateway = KoloCommandGateway(runner=ScriptedRunner([{"status": "ok"}]))

        with self.assertRaises(ExpenseFlowError) as ctx:
            gateway.contact_agent(2, "Please approve")
        self.assertEqual(ctx.exception.code, "missing_queue_id")

    def test_create_task_accepts_task_id_variants(self):
        gateway = KoloCommandGateway(runner=ScriptedRunner([{"taskId": "task_1", "status": "open"}]))

        task = gateway.create_task("Review", 2, {"report_id": "er_1"})

        self.assertEqual(task["task_id"], "task_1")

    def test_log_action_requires_audit_event_id(self):
        gateway = KoloCommandGateway(runner=ScriptedRunner([{"status": "ok"}]))

        with self.assertRaises(ExpenseFlowError) as ctx:
            gateway.log_action("skill.expense", "Captured", "key")
        self.assertEqual(ctx.exception.code, "missing_audit_event_id")

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


class _Completed:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


if __name__ == "__main__":
    unittest.main()
