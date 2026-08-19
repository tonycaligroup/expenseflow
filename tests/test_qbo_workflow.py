import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.kolo_gateway import FakeKoloGateway
from scripts.expenseflow.kolo_workflows import (
    ACCOUNTING_REFERENCE_CACHE,
    EXPENSE,
    EXPENSE_REPORT,
    EXPORT_ITEM,
    EXPORT_RUN,
    refresh_qbo_reference_cache,
    sync_approved_report_qbo,
    upsert_accounting_destination,
)


CONNECTED = {
    "connected": True,
    "environment": "sandbox",
    "realms": [{"realm_id": "realm_1", "company_name": "Test Company", "needs_reconnect": False}],
}


class LoseWriteResponseGateway(FakeKoloGateway):
    def quickbooks_write(self, *args, **kwargs):
        super().quickbooks_write(*args, **kwargs)
        raise ExpenseFlowError("kolo_command_failed", "The approval brief response was lost.", retryable=True)


class QboWorkflowTests(unittest.TestCase):
    def test_approval_brief_then_execution_marks_report_synced(self):
        gateway = self._gateway()

        pending = sync_approved_report_qbo(gateway, "er_1", session_key="session_1")
        gateway.set_qbo_write_status(
            pending["brief_number"],
            "executed",
            {"Purchase": {"Id": "qbo_123", "SyncToken": "0"}},
        )
        completed = sync_approved_report_qbo(gateway, "er_1")

        self.assertEqual(pending["status"], "approval_pending")
        self.assertEqual(completed["status"], "ok")
        self.assertEqual(gateway.get_record(EXPENSE_REPORT, "er_1")["status"], "synced")
        self.assertEqual(gateway.get_record(EXPENSE, "exp_1")["status"], "synced")
        self.assertEqual(gateway.list_records(EXPORT_ITEM)[0]["status"], "confirmed")
        self.assertEqual(completed["export_run"]["qbo_entity_id"], "qbo_123")

    def test_repeated_completed_sync_does_not_create_another_brief(self):
        gateway = self._gateway()
        pending = sync_approved_report_qbo(gateway, "er_1")
        gateway.set_qbo_write_status(
            pending["brief_number"],
            "executed",
            {"Purchase": {"Id": "qbo_123", "SyncToken": "0"}},
        )
        sync_approved_report_qbo(gateway, "er_1")

        repeated = sync_approved_report_qbo(gateway, "er_1")

        self.assertEqual(repeated["status"], "already_synced")
        self.assertEqual(len([op for op in gateway.qbo_operations if op["operation"] == "write"]), 1)

    def test_executed_without_result_stays_pending(self):
        gateway = self._gateway()
        pending = sync_approved_report_qbo(gateway, "er_1")
        gateway.set_qbo_write_status(pending["brief_number"], "executed", None)

        result = sync_approved_report_qbo(gateway, "er_1")

        self.assertEqual(result["status"], "execution_pending")
        self.assertEqual(gateway.get_record(EXPENSE_REPORT, "er_1")["status"], "approved")

    def test_existing_brief_can_be_reconciled_after_connection_is_lost(self):
        gateway = self._gateway()
        pending = sync_approved_report_qbo(gateway, "er_1")
        gateway.qbo_connection = {"connected": False, "environment": "sandbox", "realms": []}
        gateway.set_qbo_write_status(
            pending["brief_number"],
            "executed",
            {"Purchase": {"Id": "qbo_123", "SyncToken": "0"}},
        )

        result = sync_approved_report_qbo(gateway, "er_1")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(gateway.get_record(EXPENSE_REPORT, "er_1")["status"], "synced")

    def test_rejected_brief_can_only_retry_explicitly(self):
        gateway = self._gateway()
        pending = sync_approved_report_qbo(gateway, "er_1")
        gateway.set_qbo_write_status(pending["brief_number"], "rejected")
        rejected = sync_approved_report_qbo(gateway, "er_1")

        same = sync_approved_report_qbo(gateway, "er_1")
        retried = sync_approved_report_qbo(gateway, "er_1", retry_terminal=True)

        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(same["status"], "rejected")
        self.assertEqual(retried["status"], "approval_pending")
        self.assertEqual(retried["export_run"]["attempt"], 2)
        self.assertEqual(len([op for op in gateway.qbo_operations if op["operation"] == "write"]), 2)

    def test_lost_brief_response_fails_closed_without_second_write(self):
        gateway = self._gateway(LoseWriteResponseGateway(qbo_status=CONNECTED))

        with self.assertRaises(ExpenseFlowError):
            sync_approved_report_qbo(gateway, "er_1")
        with self.assertRaises(ExpenseFlowError) as ctx:
            sync_approved_report_qbo(gateway, "er_1")

        self.assertEqual(ctx.exception.code, "qbo_sync_incomplete")
        self.assertEqual(len([op for op in gateway.qbo_operations if op["operation"] == "write"]), 1)
        self.assertEqual(gateway.list_records(EXPORT_RUN)[0]["status"], "unknown")

    def test_not_connected_fails_before_claim_or_write(self):
        gateway = self._gateway(FakeKoloGateway())

        with self.assertRaises(ExpenseFlowError) as ctx:
            sync_approved_report_qbo(gateway, "er_1")

        self.assertEqual(ctx.exception.code, "qbo_not_connected")
        self.assertEqual(gateway.list_records(EXPORT_RUN), [])

    def test_invalid_execution_result_does_not_mark_synced(self):
        gateway = self._gateway()
        pending = sync_approved_report_qbo(gateway, "er_1")
        gateway.set_qbo_write_status(pending["brief_number"], "executed", {"Purchase": {"SyncToken": "0"}})

        with self.assertRaises(ExpenseFlowError) as ctx:
            sync_approved_report_qbo(gateway, "er_1")

        self.assertEqual(ctx.exception.code, "qbo_execution_result_invalid")
        self.assertEqual(gateway.get_record(EXPENSE_REPORT, "er_1")["status"], "approved")

    def test_refresh_cache_uses_bounded_queries_and_allowlists_fields(self):
        gateway = self._gateway()
        gateway.qbo_reads = {
            "Account": {"QueryResponse": {"Account": [{"Id": "1", "Name": "Travel", "Secret": "no"}]}},
            "Vendor": {"QueryResponse": {"Vendor": [{"Id": "2", "DisplayName": "Employee"}]}},
        }

        result = refresh_qbo_reference_cache(gateway)

        self.assertEqual(result["references"]["accounts"], [{"Id": "1", "Name": "Travel"}])
        self.assertEqual(gateway.get_record(ACCOUNTING_REFERENCE_CACHE, "default")["status"], "active")
        calls = [op for op in gateway.qbo_operations if op["operation"] == "call"]
        self.assertEqual(len(calls), 7)
        self.assertTrue(all("maxresults 100" in op["query"]["query"] for op in calls))

    def _gateway(self, gateway=None):
        gateway = gateway or FakeKoloGateway(qbo_status=CONNECTED)
        upsert_accounting_destination(
            gateway,
            "default",
            {
                "destination_type": "qbo",
                "config": {
                    "realm_id": "realm_1",
                    "transaction_type": "purchase",
                    "category_account_ids": {"Office Supplies": "41"},
                    "balancing_account_id": "99",
                    "payment_type": "Cash",
                },
            },
        )
        gateway.upsert_record(
            EXPENSE,
            "exp_1",
            {
                "expense_id": "exp_1",
                "report_id": "er_1",
                "submitter_user_id": 7,
                "date": "2026-08-19",
                "vendor": "Office Depot",
                "category": "Office Supplies",
                "amount": "12.34",
                "currency": "USD",
                "tax": "0.00",
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
                "submitter_user_id": 7,
                "expense_ids": ["exp_1"],
                "status": "approved",
                "approved_at": "2026-08-19T12:00:00Z",
            },
            "approved",
        )
        return gateway


if __name__ == "__main__":
    unittest.main()
