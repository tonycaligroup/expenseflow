import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.qbo_export import (
    build_qbo_transaction,
    extract_qbo_entity,
    normalize_qbo_reference_cache,
)


REPORT = {
    "report_id": "er_1",
    "submitter_user_id": 7,
    "status": "approved",
    "approved_at": "2026-08-19T12:00:00Z",
}


def expense(expense_id="exp_1", amount="12.34", category="Office Supplies", currency="USD"):
    return {
        "expense_id": expense_id,
        "status": "approved",
        "date": "2026-08-18",
        "vendor": "Office Depot",
        "category": category,
        "amount": amount,
        "currency": currency,
        "tax": "0.00",
        "receipt_ref": "kolo-object://receipt_1",
    }


class QboExportTests(unittest.TestCase):
    def test_purchase_payload_is_deterministic_and_account_mapped(self):
        config = {
            "transaction_type": "purchase",
            "category_account_ids": {"Office Supplies": "41"},
            "balancing_account_id": "99",
            "payment_type": "Cash",
        }

        first = build_qbo_transaction(REPORT, [expense()], config)
        second = build_qbo_transaction(REPORT, [expense()], config)

        self.assertEqual(first, second)
        self.assertEqual(first["entity_type"], "Purchase")
        self.assertEqual(first["path"], "purchase")
        self.assertEqual(first["body"]["AccountRef"], {"value": "99"})
        self.assertEqual(
            first["body"]["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"],
            {"value": "41"},
        )
        self.assertEqual(first["body"]["Line"][0]["Amount"], 12.34)
        self.assertIn("kolo-object://receipt_1", first["body"]["PrivateNote"])

    def test_journal_entry_balances_debits_and_credit(self):
        transaction = build_qbo_transaction(
            REPORT,
            [expense("exp_1", "10.00"), expense("exp_2", "2.34")],
            {
                "transaction_type": "journalentry",
                "category_account_ids": {"Office Supplies": "41"},
                "balancing_account_id": "88",
            },
        )

        lines = transaction["body"]["Line"]
        self.assertEqual([line["Amount"] for line in lines], [10.0, 2.34, 12.34])
        self.assertEqual(lines[-1]["JournalEntryLineDetail"]["PostingType"], "Credit")
        self.assertEqual(lines[-1]["JournalEntryLineDetail"]["AccountRef"], {"value": "88"})

    def test_bill_uses_submitter_vendor_mapping(self):
        transaction = build_qbo_transaction(
            REPORT,
            [expense()],
            {
                "transaction_type": "bill",
                "category_account_ids": {"Office Supplies": "41"},
                "employee_vendor_ids": {"7": "vendor_7"},
                "accounts_payable_account_id": "ap_1",
            },
        )

        self.assertEqual(transaction["body"]["VendorRef"], {"value": "vendor_7"})
        self.assertEqual(transaction["body"]["APAccountRef"], {"value": "ap_1"})

    def test_missing_category_mapping_fails_closed(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            build_qbo_transaction(
                REPORT,
                [expense(category="Travel")],
                {
                    "transaction_type": "purchase",
                    "category_account_ids": {"Office Supplies": "41"},
                    "balancing_account_id": "99",
                },
            )
        self.assertEqual(ctx.exception.code, "missing_qbo_category_mapping")

    def test_multi_currency_report_is_rejected(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            build_qbo_transaction(
                REPORT,
                [expense("exp_usd"), expense("exp_cad", currency="CAD")],
                {
                    "transaction_type": "purchase",
                    "category_account_ids": {"Office Supplies": "41"},
                    "balancing_account_id": "99",
                },
            )
        self.assertEqual(ctx.exception.code, "qbo_multi_currency_report")

    def test_local_receipt_path_is_not_sent_to_qbo(self):
        row = expense()
        row["receipt_ref"] = "/tmp/private-receipt.png"
        transaction = build_qbo_transaction(
            REPORT,
            [row],
            {
                "transaction_type": "purchase",
                "category_account_ids": {"Office Supplies": "41"},
                "balancing_account_id": "99",
            },
        )
        self.assertNotIn("/tmp", transaction["body"]["PrivateNote"])

    def test_reference_cache_keeps_only_allowlisted_fields(self):
        cache = normalize_qbo_reference_cache(
            {
                "Account": {
                    "QueryResponse": {
                        "Account": [
                            {"Id": "1", "Name": "Travel", "AccountType": "Expense", "Secret": "no"}
                        ]
                    }
                },
                "Vendor": {
                    "QueryResponse": {
                        "Vendor": [{"Id": "2", "DisplayName": "Employee", "PrimaryEmailAddr": {"Address": "x"}}]
                    }
                },
            }
        )

        self.assertEqual(cache["accounts"], [{"Id": "1", "Name": "Travel", "AccountType": "Expense"}])
        self.assertEqual(cache["vendors"], [{"Id": "2", "DisplayName": "Employee"}])
        self.assertNotIn("Secret", cache["accounts"][0])

    def test_extracts_created_entity_from_nested_execution_result(self):
        entity = extract_qbo_entity(
            {"response": {"Purchase": {"Id": "123", "SyncToken": "0"}}},
            "Purchase",
        )

        self.assertEqual(entity["entity_id"], "123")
        self.assertEqual(entity["sync_token"], "0")

    def test_execution_result_requires_entity_id(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            extract_qbo_entity({"Purchase": {"SyncToken": "0"}}, "Purchase")
        self.assertEqual(ctx.exception.code, "qbo_execution_result_invalid")


if __name__ == "__main__":
    unittest.main()
