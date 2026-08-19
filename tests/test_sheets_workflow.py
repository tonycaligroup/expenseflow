import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.kolo_gateway import FakeKoloGateway
from scripts.expenseflow.kolo_workflows import (
    ACCOUNTING_DESTINATION,
    EXPENSE,
    EXPENSE_REPORT,
    EXPORT_ITEM,
    EXPORT_RUN,
    export_approved_report_sheets,
    upsert_accounting_destination,
)
from scripts.expenseflow.sheets_export import SHEET_COLUMNS


class FailOnSecondAppendGateway(FakeKoloGateway):
    def __init__(self):
        super().__init__()
        self.append_count = 0

    def sheets_append_values(self, spreadsheet_id, a1_range, values):
        self.append_count += 1
        if self.append_count == 2:
            raise ExpenseFlowError(
                "sheets_outcome_unknown",
                "The append response was lost.",
                retryable=False,
            )
        return super().sheets_append_values(spreadsheet_id, a1_range, values)


class AppendThenLoseResponseGateway(FakeKoloGateway):
    def __init__(self):
        super().__init__()
        self.lose_response = True

    def sheets_append_values(self, spreadsheet_id, a1_range, values):
        response = super().sheets_append_values(spreadsheet_id, a1_range, values)
        if self.lose_response:
            self.lose_response = False
            raise ExpenseFlowError("sheets_outcome_unknown", "The append response was lost.")
        return response


class SheetsWorkflowTests(unittest.TestCase):
    def test_exports_and_repeated_call_does_not_append_again(self):
        gateway = self._gateway()

        result = export_approved_report_sheets(gateway, "er_1")
        repeated = export_approved_report_sheets(gateway, "er_1")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(repeated["status"], "already_exported")
        self.assertEqual(gateway.get_record(EXPENSE_REPORT, "er_1")["status"], "exported")
        self.assertEqual(gateway.get_record(EXPENSE, "exp_1")["status"], "exported")
        self.assertEqual(len([op for op in gateway.sheet_operations if op["operation"] == "append"]), 1)
        self.assertEqual(gateway.list_records(EXPORT_ITEM)[0]["status"], "confirmed")

    def test_existing_row_is_reconciled_without_append(self):
        gateway = self._gateway()
        export_approved_report_sheets(gateway, "er_1")
        run = gateway.list_records(EXPORT_RUN)[0]["payload"]
        item = gateway.list_records(EXPORT_ITEM)[0]["payload"]
        sheet = gateway.spreadsheets["sheet_1"]["sheets"]["ExpenseFlow"]
        sheet["rows"].insert(1, ["manual row"])
        gateway.records[(EXPORT_RUN, run["export_run_id"])]["payload"]["status"] = "in_progress"
        gateway.records[(EXPORT_RUN, run["export_run_id"])]["status"] = "in_progress"
        gateway.records[(EXPENSE_REPORT, "er_1")]["payload"]["status"] = "approved"
        gateway.records[(EXPENSE_REPORT, "er_1")]["status"] = "approved"
        gateway.records[(EXPENSE, "exp_1")]["payload"]["status"] = "approved"
        gateway.records[(EXPENSE, "exp_1")]["status"] = "approved"

        result = export_approved_report_sheets(gateway, "er_1")

        self.assertEqual(result["status"], "ok")
        refreshed = gateway.get_record(EXPORT_ITEM, item["export_item_id"])["payload"]
        self.assertEqual(refreshed["destination_row"], 3)
        self.assertEqual(len([op for op in gateway.sheet_operations if op["operation"] == "append"]), 1)

    def test_partial_unknown_failure_leaves_report_approved_and_blocks_csv_fallback(self):
        gateway = self._gateway(FailOnSecondAppendGateway(), expense_count=2, fallback=True)

        with self.assertRaises(ExpenseFlowError) as ctx:
            export_approved_report_sheets(gateway, "er_1")

        self.assertEqual(ctx.exception.code, "sheets_outcome_unknown")
        self.assertEqual(gateway.get_record(EXPENSE_REPORT, "er_1")["status"], "approved")
        statuses = [record["status"] for record in gateway.list_records(EXPORT_ITEM)]
        self.assertEqual(statuses, ["confirmed", "unknown"])

    def test_lost_append_response_is_reconciled_without_second_append(self):
        gateway = self._gateway(AppendThenLoseResponseGateway())

        with self.assertRaises(ExpenseFlowError):
            export_approved_report_sheets(gateway, "er_1")
        result = export_approved_report_sheets(gateway, "er_1")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len([op for op in gateway.sheet_operations if op["operation"] == "append"]), 1)

    def test_incomplete_prior_claim_fails_closed_without_append(self):
        gateway = self._gateway()
        run_id = "sheets:default:sheet_1:er_1"
        gateway.upsert_record(
            EXPORT_RUN,
            run_id,
            {
                "export_run_id": run_id,
                "org_id": "default",
                "report_id": "er_1",
                "spreadsheet_id": "sheet_1",
                "sheet_id": 0,
                "sheet_name_at_export": "ExpenseFlow",
                "status": "in_progress",
            },
            "in_progress",
        )

        with self.assertRaises(ExpenseFlowError) as ctx:
            export_approved_report_sheets(gateway, "er_1")

        self.assertEqual(ctx.exception.code, "sheets_export_incomplete")
        self.assertEqual(len([op for op in gateway.sheet_operations if op["operation"] == "append"]), 0)

    def test_connection_failure_can_return_csv_fallback_before_any_sheet_row(self):
        gateway = self._gateway(fallback=True)
        gateway.queue_sheet_failure(
            "metadata",
            ExpenseFlowError("sheets_permission_denied", "No spreadsheet access."),
        )

        result = export_approved_report_sheets(gateway, "er_1")

        self.assertEqual(result["status"], "fallback_ready")
        self.assertIn("report_id,expense_id", result["csv"])
        self.assertEqual(gateway.get_record(EXPENSE_REPORT, "er_1")["status"], "approved")

    def test_mismatched_headers_do_not_modify_or_export_report(self):
        gateway = self._gateway()
        gateway.spreadsheets["sheet_1"]["sheets"]["ExpenseFlow"]["rows"] = [["wrong", "headers"]]

        with self.assertRaises(ExpenseFlowError) as ctx:
            export_approved_report_sheets(gateway, "er_1")

        self.assertEqual(ctx.exception.code, "sheets_header_mismatch")
        self.assertEqual(gateway.get_record(EXPENSE_REPORT, "er_1")["status"], "approved")
        self.assertEqual(gateway.list_records(EXPORT_RUN), [])

    def _gateway(self, gateway=None, expense_count=1, fallback=False):
        gateway = gateway or FakeKoloGateway()
        gateway.add_spreadsheet("sheet_1")
        upsert_accounting_destination(
            gateway,
            "default",
            {
                "destination_type": "sheets",
                "config": {
                    "spreadsheet_id": "sheet_1",
                    "sheet_name": "ExpenseFlow",
                    "fallback_to_csv": fallback,
                },
            },
        )
        expense_ids = []
        for number in range(1, expense_count + 1):
            expense_id = f"exp_{number}"
            expense_ids.append(expense_id)
            gateway.upsert_record(
                EXPENSE,
                expense_id,
                {
                    "expense_id": expense_id,
                    "report_id": "er_1",
                    "submitter_user_id": 1,
                    "submitter_name": "Tony",
                    "date": "2026-08-19",
                    "vendor": f"Vendor {number}",
                    "category": "Office Supplies",
                    "amount": "12.34",
                    "currency": "USD",
                    "tax": "0.00",
                    "payment_method": "Personal Card",
                    "receipt_ref": f"receipt_{number}",
                    "note": "",
                    "status": "approved",
                },
                "approved",
            )
        gateway.upsert_record(
            EXPENSE_REPORT,
            "er_1",
            {
                "report_id": "er_1",
                "org_id": "default",
                "submitter_user_id": 1,
                "submitter_name": "Tony",
                "expense_ids": expense_ids,
                "status": "approved",
                "approved_at": "2026-08-19T12:00:00Z",
            },
            "approved",
        )
        return gateway


if __name__ == "__main__":
    unittest.main()
