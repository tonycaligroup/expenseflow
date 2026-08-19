import json
import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.kolo_command_gateway import KoloCommandGateway


class ScriptedRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class QboGatewayTests(unittest.TestCase):
    def test_status_uses_verified_command(self):
        runner = ScriptedRunner([{"connected": False, "environment": "production", "realms": []}])
        gateway = KoloCommandGateway(runner=runner)

        result = gateway.quickbooks_status()

        self.assertFalse(result["connected"])
        self.assertEqual(runner.commands[0], ["kolo", "quickbooks", "status"])

    def test_read_uses_quiet_mode_realm_and_sorted_query(self):
        runner = ScriptedRunner([{"QueryResponse": {"Account": []}}])
        gateway = KoloCommandGateway(runner=runner)

        gateway.quickbooks_call(
            "query",
            realm_id="realm_1",
            query={"z": "last", "query": "select * from Account maxresults 5"},
        )

        self.assertEqual(
            runner.commands[0],
            [
                "kolo",
                "quickbooks",
                "call",
                "query",
                "--api",
                "accounting",
                "-q",
                "--realm",
                "realm_1",
                "--query",
                "query=select * from Account maxresults 5",
                "--query",
                "z=last",
            ],
        )

    def test_write_uses_canonical_json_and_returns_brief(self):
        runner = ScriptedRunner([{"briefNumber": "brief_1"}])
        gateway = KoloCommandGateway(runner=runner)

        result = gateway.quickbooks_write(
            "purchase",
            {"b": 2, "a": 1},
            realm_id="realm_1",
            request_id="request_1",
            reason="Approved report er_1",
            session_key="session_1",
        )

        command = runner.commands[0]
        self.assertEqual(result["brief_number"], "brief_1")
        self.assertEqual(command[:4], ["kolo", "quickbooks", "write", "purchase"])
        self.assertEqual(command[command.index("--body") + 1], json.dumps({"a": 1, "b": 2}, separators=(",", ":")))
        self.assertIn("--request-id", command)
        self.assertIn("--session-key", command)

    def test_write_requires_brief_number(self):
        gateway = KoloCommandGateway(runner=ScriptedRunner([{"status": "ok"}]))

        with self.assertRaises(ExpenseFlowError) as ctx:
            gateway.quickbooks_write("purchase", {"Line": []})

        self.assertEqual(ctx.exception.code, "missing_qbo_brief_number")

    def test_write_status_normalizes_camel_case_execution_result(self):
        runner = ScriptedRunner([{"status": "EXECUTED", "executionResult": {"Purchase": {"Id": "1"}}}])
        gateway = KoloCommandGateway(runner=runner)

        result = gateway.quickbooks_write_status("brief_1")

        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["execution_result"]["Purchase"]["Id"], "1")
        self.assertEqual(
            runner.commands[0],
            ["kolo", "quickbooks", "write-status", "--brief-id", "brief_1"],
        )


if __name__ == "__main__":
    unittest.main()
