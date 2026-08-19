from io import BytesIO
import json
import unittest
from urllib.error import HTTPError, URLError

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.maton_sheets_gateway import MatonSheetsGateway


class FakeResponse:
    def __init__(self, payload, content_type="application/json"):
        self.payload = payload
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ScriptedOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class MatonSheetsGatewayTests(unittest.TestCase):
    def test_append_uses_raw_values_and_does_not_retry_unknown_outcome(self):
        opener = ScriptedOpener([URLError("timed out"), FakeResponse({"unexpected": True})])
        gateway = MatonSheetsGateway(api_key="secret", opener=opener, sleeper=lambda _: None)

        with self.assertRaises(ExpenseFlowError) as ctx:
            gateway.append_values("sheet id", "'Expense Flow'!A1:O1", [["=literal"]])

        self.assertEqual(ctx.exception.code, "sheets_outcome_unknown")
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0][0]
        self.assertEqual(request.method, "POST")
        self.assertIn("valueInputOption=RAW", request.full_url)
        self.assertIn("sheet%20id", request.full_url)

    def test_read_retries_rate_limit_with_bounded_backoff(self):
        error_body = BytesIO(b'{"error":{"status":"RESOURCE_EXHAUSTED","message":"slow down"}}')
        rate_limit = HTTPError(
            "https://example.test",
            429,
            "rate limited",
            {"Content-Type": "application/json"},
            error_body,
        )
        sleeps = []
        opener = ScriptedOpener([rate_limit, FakeResponse({"values": [["ok"]]})])
        gateway = MatonSheetsGateway(
            api_key="secret",
            opener=opener,
            sleeper=sleeps.append,
            max_attempts=3,
        )

        result = gateway.read_values("sheet_1", "ExpenseFlow!A1:A1")

        self.assertEqual(result["values"], [["ok"]])
        self.assertEqual(sleeps, [1])
        self.assertEqual(len(opener.requests), 2)

    def test_update_is_not_retried_after_unknown_response(self):
        opener = ScriptedOpener([URLError("connection reset"), FakeResponse({"unexpected": True})])
        gateway = MatonSheetsGateway(api_key="secret", opener=opener, sleeper=lambda _: None)

        with self.assertRaises(ExpenseFlowError) as ctx:
            gateway.update_values("sheet_1", "ExpenseFlow!A2:O2", [["value"]])

        self.assertEqual(ctx.exception.code, "sheets_outcome_unknown")
        self.assertEqual(len(opener.requests), 1)

    def test_missing_connection_fails_without_exposing_credentials(self):
        gateway = MatonSheetsGateway(api_key=None, opener=ScriptedOpener([]))
        gateway.api_key = None

        with self.assertRaises(ExpenseFlowError) as ctx:
            gateway.get_metadata("sheet_1")

        self.assertEqual(ctx.exception.code, "sheets_connection_missing")


if __name__ == "__main__":
    unittest.main()
