import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.receipt_engine import normalize_receipt_attachment


class ReceiptEngineTests(unittest.TestCase):
    def test_normalizes_kolo_object_reference_and_safe_metadata(self):
        receipt = normalize_receipt_attachment(
            {
                "objectStoreObjectId": "obj_123",
                "reference": "kolo://obj/obj_123",
                "filename": "/private/tmp/receipt.png",
                "contentType": "image/png",
                "sizeBytes": "1200",
                "sha256": "a" * 64,
            }
        )

        self.assertEqual(receipt["object_store_object_id"], "obj_123")
        self.assertEqual(receipt["filename"], "receipt.png")
        self.assertEqual(receipt["size_bytes"], 1200)
        self.assertNotIn("local_path", receipt)

    def test_derives_object_id_from_kolo_reference(self):
        receipt = normalize_receipt_attachment(
            {"reference": "kolo://obj/obj_456", "filename": "receipt.pdf"}
        )
        self.assertEqual(receipt["object_store_object_id"], "obj_456")
        self.assertEqual(receipt["content_type"], "application/pdf")

    def test_rejects_unsupported_receipt_type(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            normalize_receipt_attachment(
                {"objectStoreObjectId": "obj_123", "filename": "receipt.exe", "contentType": "application/x-msdownload"}
            )
        self.assertEqual(ctx.exception.code, "unsupported_receipt_type")

    def test_rejects_mismatched_object_reference(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            normalize_receipt_attachment(
                {
                    "objectStoreObjectId": "obj_123",
                    "reference": "kolo://obj/obj_456",
                    "filename": "receipt.png",
                }
            )
        self.assertEqual(ctx.exception.code, "receipt_reference_mismatch")

    def test_enforces_configured_size_limit(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            normalize_receipt_attachment(
                {
                    "objectStoreObjectId": "obj_123",
                    "filename": "receipt.png",
                    "sizeBytes": 1201,
                },
                {"max_receipt_bytes": 1200},
            )
        self.assertEqual(ctx.exception.code, "receipt_too_large")


if __name__ == "__main__":
    unittest.main()
