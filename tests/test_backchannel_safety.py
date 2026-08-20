import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.kolo_gateway import FakeKoloGateway
from scripts.expenseflow.kolo_workflows import (
    APPROVAL_DECISION,
    APPROVAL_DECISION_CLAIM,
    APPROVAL_REQUEST,
    EXPENSE,
    EXPENSE_REPORT,
    NOTIFICATION_EVENT,
    TASK_EVENT,
    capture_expense,
    decide_report_approval,
    decide_report_approval_from_sender,
    reconcile_approval_decision,
    submit_report_for_approval,
    upsert_approval_policy,
    upsert_expense_settings,
    upsert_user_profile,
)


ORG_ID = "9db4581f-cc65-4fe7-8796-2d8b63bf80b5"
OTHER_ORG_ID = "52ca6d59-8b5b-4548-9a3d-1f3aa5578f56"


class PersistBeforeDeliveryGateway(FakeKoloGateway):
    def contact_agent(self, target_user_id, message):
        request = self.get_record(APPROVAL_REQUEST, "ar_safe")
        report = self.get_record(EXPENSE_REPORT, "er_safe")
        expense = self.get_record(EXPENSE, "exp_safe")
        if request["status"] != "pending" or report["status"] != "pending_approval" or expense["status"] != "submitted":
            raise AssertionError("Submission state was not persisted before delivery.")
        return super().contact_agent(target_user_id, message)


class LostContactAcknowledgementGateway(FakeKoloGateway):
    def __init__(self):
        super().__init__()
        self.fail_contact_once = True

    def contact_agent(self, target_user_id, message):
        result = super().contact_agent(target_user_id, message)
        if self.fail_contact_once:
            self.fail_contact_once = False
            raise ExpenseFlowError("contact_result_lost", "Delivery result was lost.")
        return result


class LostTaskAcknowledgementGateway(FakeKoloGateway):
    def __init__(self):
        super().__init__()
        self.fail_task_once = True

    def create_task(self, title, user_id, metadata=None):
        result = super().create_task(title, user_id, metadata)
        if self.fail_task_once:
            self.fail_task_once = False
            raise ExpenseFlowError("task_result_lost", "Task result was lost.")
        return result


class PartialDecisionWriteGateway(FakeKoloGateway):
    def __init__(self):
        super().__init__()
        self.fail_decision_request_write = False

    def upsert_record(self, record_type, external_id, payload, status="active", schema_version=1):
        if self.fail_decision_request_write and record_type == APPROVAL_REQUEST and status == "approved":
            self.fail_decision_request_write = False
            raise ExpenseFlowError("simulated_partial_write", "Simulated approval request write failure.")
        return super().upsert_record(record_type, external_id, payload, status, schema_version)


class BackchannelSafetyTests(unittest.TestCase):
    def _configured_gateway(self, gateway=None):
        gateway = gateway or FakeKoloGateway()
        upsert_user_profile(
            gateway,
            {
                "user_id": 1,
                "org_id": ORG_ID,
                "display_name": "Employee",
                "department": "Operations",
                "status": "active",
                "sender_id": "sender-employee",
            },
        )
        upsert_user_profile(
            gateway,
            {
                "user_id": 2,
                "org_id": ORG_ID,
                "display_name": "Approver",
                "status": "active",
                "can_approve": True,
                "sender_id": "sender-approver-uuid",
                "approval_scope": {"departments": ["Operations"], "max_amount": "1000.00"},
            },
        )
        upsert_expense_settings(gateway, ORG_ID, {"receipt_required_above": "25.00"})
        upsert_approval_policy(gateway, ORG_ID, {"default_approver_user_id": 2})
        return gateway

    def _capture(self, gateway, expense_id="exp_safe"):
        return capture_expense(
            gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-19",
                "amount": "12.34",
                "currency": "USD",
                "category": "Office Supplies",
            },
            1,
            org_id=ORG_ID,
            expense_id=expense_id,
        )

    def _submit(self, gateway):
        return submit_report_for_approval(
            gateway,
            1,
            ["exp_safe"],
            org_id=ORG_ID,
            report_id="er_safe",
            approval_request_id="ar_safe",
        )

    def test_submission_persists_before_delivery_and_prints_request_id(self):
        gateway = self._configured_gateway(PersistBeforeDeliveryGateway())
        self._capture(gateway)

        result = self._submit(gateway)

        self.assertEqual(result["status"], "ok")
        self.assertIn("Approval request: ar_safe", gateway.messages[0]["message"])
        self.assertIn("approve ar_safe", gateway.messages[0]["message"])
        self.assertEqual(len(gateway.list_records(NOTIFICATION_EVENT)), 1)
        self.assertEqual(len(gateway.list_records(TASK_EVENT)), 1)

    def test_submission_retry_does_not_duplicate_message_or_task(self):
        gateway = self._configured_gateway()
        self._capture(gateway)
        first = self._submit(gateway)

        second = self._submit(gateway)

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(len(gateway.messages), 1)
        self.assertEqual(len(gateway.tasks), 1)
        self.assertEqual(second["communication"]["notification"]["status"], "already_sent")
        self.assertEqual(second["communication"]["task"]["status"], "already_created")

    def test_lost_contact_result_fails_closed_without_resending(self):
        gateway = self._configured_gateway(LostContactAcknowledgementGateway())
        self._capture(gateway)

        first = self._submit(gateway)
        second = self._submit(gateway)

        self.assertEqual(first["status"], "communication_review_required")
        self.assertEqual(second["status"], "communication_review_required")
        self.assertEqual(len(gateway.messages), 1)
        event = gateway.list_records(NOTIFICATION_EVENT)[0]["payload"]
        self.assertEqual(event["status"], "delivery_unknown")

    def test_lost_task_result_fails_closed_without_recreating_task(self):
        gateway = self._configured_gateway(LostTaskAcknowledgementGateway())
        self._capture(gateway)

        first = self._submit(gateway)
        second = self._submit(gateway)

        self.assertEqual(first["status"], "communication_review_required")
        self.assertEqual(second["status"], "communication_review_required")
        self.assertEqual(len(gateway.tasks), 1)
        event = gateway.list_records(TASK_EVENT)[0]["payload"]
        self.assertEqual(event["status"], "creation_unknown")

    def test_inbound_decision_resolves_uuid_sender_and_exact_queue(self):
        gateway = self._configured_gateway()
        self._capture(gateway)
        submitted = self._submit(gateway)

        result = decide_report_approval_from_sender(
            gateway,
            "sender-approver-uuid",
            "approved",
            ORG_ID,
            queue_id=submitted["approval_request"]["backchannel_queue_id"],
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["approval_decision"]["approver_user_id"], 2)
        self.assertEqual(gateway.get_record(EXPENSE_REPORT, "er_safe")["status"], "approved")

    def test_inbound_decision_rejects_unmapped_sender_and_cross_org_request(self):
        gateway = self._configured_gateway()
        self._capture(gateway)
        self._submit(gateway)

        with self.assertRaises(ExpenseFlowError) as unmapped:
            decide_report_approval_from_sender(
                gateway,
                "unknown-sender",
                "approved",
                ORG_ID,
                approval_request_id="ar_safe",
            )
        self.assertEqual(unmapped.exception.code, "unmapped_approval_sender")

        with self.assertRaises(ExpenseFlowError) as wrong_org:
            decide_report_approval_from_sender(
                gateway,
                "sender-approver-uuid",
                "approved",
                OTHER_ORG_ID,
                approval_request_id="ar_safe",
            )
        self.assertEqual(wrong_org.exception.code, "unmapped_approval_sender")

    def test_completed_decision_replays_but_conflicting_decision_is_rejected(self):
        gateway = self._configured_gateway()
        self._capture(gateway)
        self._submit(gateway)
        first = decide_report_approval(gateway, "ar_safe", 2, "approved", org_id=ORG_ID)

        replay = decide_report_approval(gateway, "ar_safe", 2, "approved", org_id=ORG_ID)

        self.assertEqual(first["status"], "ok")
        self.assertEqual(replay["status"], "already_decided")
        self.assertEqual(
            replay["approval_decision"]["approval_decision_id"],
            first["approval_decision"]["approval_decision_id"],
        )
        with self.assertRaises(ExpenseFlowError) as conflict:
            decide_report_approval(gateway, "ar_safe", 2, "rejected", note="No", org_id=ORG_ID)
        self.assertEqual(conflict.exception.code, "approval_decision_conflict")

    def test_existing_claim_blocks_a_competing_decision(self):
        gateway = self._configured_gateway()
        self._capture(gateway)
        self._submit(gateway)
        gateway.upsert_record(
            APPROVAL_DECISION_CLAIM,
            f"{ORG_ID}:ar_safe",
            {
                "approval_decision_claim_id": f"{ORG_ID}:ar_safe",
                "org_id": ORG_ID,
                "approval_request_id": "ar_safe",
                "report_id": "er_safe",
                "status": "claimed",
            },
            "claimed",
        )

        with self.assertRaises(ExpenseFlowError) as claimed:
            decide_report_approval(gateway, "ar_safe", 2, "approved", org_id=ORG_ID)

        self.assertEqual(claimed.exception.code, "approval_decision_review_required")
        self.assertEqual(gateway.get_record(EXPENSE_REPORT, "er_safe")["status"], "pending_approval")

    def test_partial_decision_write_is_reconciled_from_persisted_decision(self):
        gateway = self._configured_gateway(PartialDecisionWriteGateway())
        self._capture(gateway)
        self._submit(gateway)
        gateway.fail_decision_request_write = True

        with self.assertRaises(ExpenseFlowError) as partial:
            decide_report_approval(gateway, "ar_safe", 2, "approved", org_id=ORG_ID)

        self.assertEqual(partial.exception.code, "simulated_partial_write")
        self.assertEqual(gateway.get_record(EXPENSE_REPORT, "er_safe")["status"], "approved")
        self.assertEqual(gateway.get_record(APPROVAL_REQUEST, "ar_safe")["status"], "pending")
        self.assertEqual(len(gateway.list_records(APPROVAL_DECISION)), 1)
        claim = gateway.get_record(APPROVAL_DECISION_CLAIM, f"{ORG_ID}:ar_safe")
        self.assertEqual(claim["status"], "review_required")

        result = reconcile_approval_decision(gateway, "ar_safe", ORG_ID)

        self.assertEqual(result["status"], "reconciled")
        self.assertEqual(gateway.get_record(APPROVAL_REQUEST, "ar_safe")["status"], "approved")
        self.assertEqual(gateway.get_record(EXPENSE, "exp_safe")["status"], "approved")
        self.assertEqual(gateway.get_record(APPROVAL_DECISION_CLAIM, f"{ORG_ID}:ar_safe")["status"], "complete")

    def test_reconciliation_refuses_active_or_decisionless_claim(self):
        gateway = self._configured_gateway()
        self._capture(gateway)
        self._submit(gateway)
        claim_id = f"{ORG_ID}:ar_safe"
        gateway.upsert_record(
            APPROVAL_DECISION_CLAIM,
            claim_id,
            {
                "approval_decision_claim_id": claim_id,
                "org_id": ORG_ID,
                "approval_request_id": "ar_safe",
                "report_id": "er_safe",
                "status": "claimed",
                "claimed_at": "2026-08-19T00:00:00Z",
            },
            "claimed",
        )

        with self.assertRaises(ExpenseFlowError) as active:
            reconcile_approval_decision(gateway, "ar_safe", ORG_ID)
        self.assertEqual(active.exception.code, "approval_decision_still_claimed")

        gateway.set_record_status(APPROVAL_DECISION_CLAIM, claim_id, "review_required")
        with self.assertRaises(ExpenseFlowError) as missing:
            reconcile_approval_decision(gateway, "ar_safe", ORG_ID)
        self.assertEqual(missing.exception.code, "approval_decision_reconciliation_ambiguous")

    def test_omitted_submission_ids_are_deterministic(self):
        gateway = self._configured_gateway()
        self._capture(gateway)

        first = submit_report_for_approval(gateway, 1, ["exp_safe"], org_id=ORG_ID)
        second = submit_report_for_approval(gateway, 1, ["exp_safe"], org_id=ORG_ID)

        self.assertEqual(first["report"]["report_id"], second["report"]["report_id"])
        self.assertEqual(
            first["approval_request"]["approval_request_id"],
            second["approval_request"]["approval_request_id"],
        )
        self.assertEqual(len(gateway.messages), 1)
        self.assertEqual(len(gateway.tasks), 1)


if __name__ == "__main__":
    unittest.main()
