import csv
from io import StringIO
import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.kolo_gateway import FakeKoloGateway
from scripts.expenseflow.kolo_workflows import (
    ACCOUNTING_DESTINATION,
    APPROVAL_POLICY,
    APPROVAL_REQUEST,
    APPROVER_SNAPSHOT,
    EXPENSE,
    EXPENSE_REPORT,
    IDENTITY_DISCOVERY,
    USER_PROFILE,
    _create_approver_snapshot,
    acknowledge_expense_policy,
    approve_user_onboarding,
    capture_expense,
    capture_expense_with_discovery,
    configure_organization,
    decide_report_approval,
    export_approved_report_csv,
    map_sender_identity,
    reconcile_user_directory,
    submit_report_for_approval,
    upsert_accounting_destination,
    upsert_approval_delegation,
    upsert_approval_policy,
    upsert_department_policy,
    upsert_expense_settings,
    upsert_user_profile,
)


SUBMITTER = {
    "user_id": 1,
    "display_name": "Tony",
    "department": "Engineering",
    "status": "active",
}

APPROVER = {
    "user_id": 2,
    "display_name": "Kendra",
    "status": "active",
    "can_approve": True,
    "approval_scope": {"departments": ["Engineering"], "max_amount": "1000.00"},
}


class KoloWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeKoloGateway()
        upsert_user_profile(self.gateway, SUBMITTER)
        upsert_user_profile(self.gateway, APPROVER)
        upsert_expense_settings(self.gateway, "default", {"receipt_required_above": "25.00"})
        upsert_approval_policy(self.gateway, "default", {"default_approver_user_id": 2})

    def test_capture_expense_writes_governed_record_and_audit(self):
        expense = capture_expense(
            self.gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "45.00",
                "currency": "usd",
                "category": "Office Supplies",
                "receipt_ref": "receipt_1",
            },
            submitter_user_id=1,
            expense_id="exp_test",
        )
        record = self.gateway.get_record(EXPENSE, "exp_test")

        self.assertEqual(expense["status"], "draft")
        self.assertEqual(record["status"], "draft")
        self.assertEqual(record["payload"]["currency"], "USD")
        self.assertIn("expenseflow:capture:exp_test", self.gateway.audit_events)

    def test_capture_detects_duplicate_candidate(self):
        first = {
            "vendor": "Office Depot",
            "date": "2026-08-18",
            "amount": "45.00",
            "currency": "USD",
            "category": "Office Supplies",
            "receipt_ref": "receipt_1",
        }
        capture_expense(self.gateway, first, 1, expense_id="exp_1")
        duplicate = capture_expense(self.gateway, {**first, "receipt_ref": "receipt_1"}, 1, expense_id="exp_2")

        self.assertEqual(duplicate["duplicate_candidates"], [{"expense_id": "exp_1", "reason": "same_receipt"}])

    def test_submit_report_creates_approval_request_message_task_and_submitted_expenses(self):
        capture_expense(
            self.gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "45.00",
                "currency": "USD",
                "category": "Office Supplies",
                "receipt_ref": "receipt_1",
            },
            1,
            expense_id="exp_1",
        )
        result = submit_report_for_approval(
            self.gateway,
            1,
            ["exp_1"],
            report_id="er_test",
            approval_request_id="ar_test",
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.gateway.get_record(EXPENSE_REPORT, "er_test")["status"], "pending_approval")
        self.assertEqual(self.gateway.get_record(EXPENSE, "exp_1")["status"], "submitted")
        self.assertEqual(self.gateway.get_record(APPROVAL_REQUEST, "ar_test")["payload"]["backchannel_queue_id"], self.gateway.messages[0]["queueId"])
        self.assertEqual(self.gateway.get_record(APPROVER_SNAPSHOT, "er_test")["payload"]["approver_user_id"], 2)
        self.assertEqual(len(self.gateway.tasks), 1)

    def test_unknown_org_member_starts_onboarding_and_holds_expense(self):
        gateway = FakeKoloGateway(peers=[{"user_id": 7, "display_name": "New Employee", "org_id": "default"}])
        upsert_expense_settings(
            gateway,
            "default",
            {
                "expense_admin_user_ids": [99],
                "message_prefix": "EXPENSEFLOW PILOT TEST - NO REIMBURSEMENT",
            },
        )

        result = capture_expense_with_discovery(
            gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "12.00",
                "currency": "USD",
                "category": "Office Supplies",
            },
            7,
            sender_id="user:new",
            expense_id="exp_new",
        )

        self.assertTrue(result["onboarding_required"])
        self.assertEqual(gateway.get_record(USER_PROFILE, 7)["status"], "pending_admin_approval")
        self.assertEqual(gateway.get_record(EXPENSE, "exp_new")["status"], "held_pending_onboarding")
        self.assertEqual(gateway.messages[0]["target_user_id"], 99)
        self.assertTrue(gateway.messages[0]["message"].startswith("EXPENSEFLOW PILOT TEST - NO REIMBURSEMENT"))
        self.assertNotIn("Office Depot", gateway.messages[0]["message"])

    def test_approval_message_and_task_use_configured_prefix(self):
        upsert_expense_settings(
            self.gateway,
            "default",
            {"message_prefix": "EXPENSEFLOW PILOT TEST - NO REIMBURSEMENT"},
        )
        capture_expense(
            self.gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "12.00",
                "currency": "USD",
                "category": "Office Supplies",
            },
            1,
            expense_id="exp_prefixed",
        )

        submit_report_for_approval(
            self.gateway,
            1,
            ["exp_prefixed"],
            report_id="er_prefixed",
            approval_request_id="ar_prefixed",
        )

        self.assertTrue(self.gateway.messages[0]["message"].startswith("EXPENSEFLOW PILOT TEST - NO REIMBURSEMENT"))
        self.assertTrue(self.gateway.tasks[0]["title"].startswith("EXPENSEFLOW PILOT TEST - NO REIMBURSEMENT"))

    def test_unknown_non_member_is_blocked(self):
        gateway = FakeKoloGateway(peers=[])
        upsert_expense_settings(gateway, "default", {"expense_admin_user_ids": [99]})

        with self.assertRaises(ExpenseFlowError) as ctx:
            capture_expense_with_discovery(
                gateway,
                {
                    "vendor": "Office Depot",
                    "date": "2026-08-18",
                    "amount": "12.00",
                    "currency": "USD",
                    "category": "Office Supplies",
                },
                7,
                expense_id="exp_blocked",
            )
        self.assertEqual(ctx.exception.code, "unverified_submitter")
        self.assertEqual(gateway.list_records(EXPENSE), [])

    def test_reconciled_discovered_user_triggers_admin_on_first_expense(self):
        gateway = FakeKoloGateway(peers=[{"user_id": 7, "display_name": "New Employee", "org_id": "default"}])
        upsert_expense_settings(gateway, "default", {"expense_admin_user_ids": [99]})
        reconcile_user_directory(gateway)

        result = capture_expense_with_discovery(
            gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "12.00",
                "currency": "USD",
                "category": "Office Supplies",
            },
            7,
            expense_id="exp_reconciled",
        )

        self.assertTrue(result["onboarding_required"])
        self.assertEqual(gateway.get_record(USER_PROFILE, 7)["status"], "pending_admin_approval")
        self.assertEqual(gateway.messages[0]["target_user_id"], 99)

    def test_admin_approval_and_policy_ack_release_held_expense_to_draft(self):
        gateway = FakeKoloGateway(peers=[{"user_id": 7, "display_name": "New Employee", "org_id": "default"}])
        upsert_expense_settings(gateway, "default", {"expense_admin_user_ids": [99]})
        upsert_approval_policy(gateway, "default", {"version": 2, "default_approver_user_id": 2})
        upsert_user_profile(gateway, APPROVER)
        capture_expense_with_discovery(
            gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "12.00",
                "currency": "USD",
                "category": "Office Supplies",
            },
            7,
            expense_id="exp_new",
        )

        approved = approve_user_onboarding(gateway, 7, 99, 2)
        released = acknowledge_expense_policy(gateway, 7, 7, 2)

        self.assertEqual(approved["user_profile"]["status"], "pending_policy_ack")
        self.assertEqual(released["user_profile"]["status"], "active")
        self.assertEqual(released["released_expense_ids"], ["exp_new"])
        self.assertEqual(gateway.get_record(EXPENSE, "exp_new")["status"], "draft")

    def test_onboarding_rejects_unconfigured_admin(self):
        gateway = FakeKoloGateway()
        upsert_expense_settings(gateway, "default", {"expense_admin_user_ids": [99]})
        upsert_user_profile(gateway, {"user_id": 7, "status": "pending_admin_approval"})
        upsert_user_profile(gateway, APPROVER)

        with self.assertRaises(ExpenseFlowError) as ctx:
            approve_user_onboarding(gateway, 7, 100, 2)
        self.assertEqual(ctx.exception.code, "unauthorized_admin")

    def test_directory_reconciliation_deactivates_departed_user(self):
        gateway = FakeKoloGateway(peers=[{"user_id": 2, "display_name": "Kendra", "org_id": "default"}])
        upsert_user_profile(gateway, {**SUBMITTER, "org_id": "default"})
        upsert_user_profile(gateway, {**APPROVER, "org_id": "default"})

        result = reconcile_user_directory(gateway, deactivate_missing=True)

        self.assertEqual(result["deactivated_user_ids"], [1])
        self.assertEqual(gateway.get_record(USER_PROFILE, 1)["status"], "deactivated")

    def test_directory_reconciliation_refuses_empty_destructive_snapshot(self):
        gateway = FakeKoloGateway(peers=[])
        upsert_user_profile(gateway, {**SUBMITTER, "org_id": "default"})

        with self.assertRaises(ExpenseFlowError) as ctx:
            reconcile_user_directory(gateway, deactivate_missing=True)
        self.assertEqual(ctx.exception.code, "empty_peer_snapshot")

    def test_accounting_destination_validates_and_persists_csv(self):
        upsert_accounting_destination(
            self.gateway,
            "default",
            {"destination_type": "csv", "config": {"delivery_method": "message"}},
        )
        destination = self.gateway.get_record(ACCOUNTING_DESTINATION, "default")["payload"]
        self.assertEqual(destination["destination_type"], "csv")

    def test_jit_onboarding_runs_through_approval_and_csv(self):
        gateway = FakeKoloGateway(peers=[{"user_id": 7, "display_name": "New Employee", "org_id": "default"}])
        upsert_expense_settings(gateway, "default", {"expense_admin_user_ids": [99]})
        upsert_approval_policy(gateway, "default", {"version": 2, "default_approver_user_id": 2})
        upsert_user_profile(gateway, APPROVER)
        capture_expense_with_discovery(
            gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "12.00",
                "currency": "USD",
                "category": "Office Supplies",
            },
            7,
            expense_id="exp_jit",
        )
        approve_user_onboarding(gateway, 7, 99, 2)
        acknowledge_expense_policy(gateway, 7, 7, 2)

        submit_report_for_approval(
            gateway,
            7,
            ["exp_jit"],
            report_id="er_jit",
            approval_request_id="ar_jit",
        )
        decide_report_approval(gateway, "ar_jit", 2, "approved", decision_id="ad_jit")
        export = export_approved_report_csv(gateway, "er_jit")

        self.assertIn("exp_jit", export["csv"])
        self.assertEqual(gateway.get_record(EXPENSE_REPORT, "er_jit")["status"], "exported")

    def test_delegation_requires_registered_active_approvers(self):
        upsert_user_profile(self.gateway, {"user_id": 3, "status": "active", "can_approve": True})
        result = upsert_approval_delegation(
            self.gateway,
            {
                "delegator_user_id": 2,
                "delegate_user_id": 3,
                "valid_from": "2026-08-19",
                "valid_until": "2026-08-27",
            },
            delegation_id="del_test",
        )
        self.assertTrue(result["created"])

    def test_capture_rejects_profile_from_another_org(self):
        gateway = FakeKoloGateway()
        upsert_user_profile(gateway, {**SUBMITTER, "org_id": "org_a"})

        with self.assertRaises(ExpenseFlowError) as ctx:
            capture_expense(
                gateway,
                {
                    "vendor": "Office Depot",
                    "date": "2026-08-18",
                    "amount": "12.00",
                    "currency": "USD",
                    "category": "Office Supplies",
                },
                1,
                org_id="org_b",
            )
        self.assertEqual(ctx.exception.code, "organization_mismatch")

    def test_department_policies_are_scoped_by_org(self):
        gateway = FakeKoloGateway()
        upsert_user_profile(gateway, {**SUBMITTER, "org_id": "org_a"})
        upsert_user_profile(gateway, {**APPROVER, "org_id": "org_a"})
        upsert_user_profile(
            gateway,
            {
                "user_id": 3,
                "display_name": "Other Org Approver",
                "org_id": "org_b",
                "status": "active",
                "can_approve": True,
            },
        )
        upsert_department_policy(gateway, "Engineering", {"primary_approver_user_id": 2}, "org_a")
        upsert_department_policy(gateway, "Engineering", {"primary_approver_user_id": 3}, "org_b")
        capture_expense(
            gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "12.00",
                "currency": "USD",
                "category": "Office Supplies",
            },
            1,
            org_id="org_a",
            expense_id="exp_org",
        )

        result = submit_report_for_approval(
            gateway,
            1,
            ["exp_org"],
            org_id="org_a",
            report_id="er_org",
            approval_request_id="ar_org",
        )
        self.assertEqual(result["approval_request"]["approver_user_id"], 2)

    def test_configure_org_validates_destination_before_writing_any_records(self):
        gateway = FakeKoloGateway()
        with self.assertRaises(ExpenseFlowError) as ctx:
            configure_organization(
                gateway,
                "org_a",
                {"expense_admin_user_ids": [99]},
                {"version": 1},
                {"destination_type": "sheets", "config": {}},
            )
        self.assertEqual(ctx.exception.code, "missing_spreadsheet_id")
        self.assertEqual(gateway.records, {})

    def test_uuid_only_sender_is_held_until_admin_maps_kolo_user_id(self):
        gateway = FakeKoloGateway(peers=[{"user_id": 7, "display_name": "New Employee", "org_id": "default"}])
        upsert_expense_settings(gateway, "default", {"expense_admin_user_ids": [99]})

        held = capture_expense_with_discovery(
            gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "12.00",
                "currency": "USD",
                "category": "Office Supplies",
            },
            sender_id="user:uuid-new",
            expense_id="exp_uuid",
        )
        mapped = map_sender_identity(gateway, "user:uuid-new", 7, 99)

        self.assertEqual(held["status"], "pending_identity_mapping")
        self.assertEqual(gateway.get_record(IDENTITY_DISCOVERY, "exp_uuid")["status"], "mapped")
        self.assertEqual(gateway.get_record(EXPENSE, "exp_uuid")["payload"]["submitter_user_id"], 7)
        self.assertEqual(gateway.get_record(EXPENSE, "exp_uuid")["status"], "held_pending_onboarding")
        self.assertTrue(mapped["onboarding_required"])
        self.assertEqual(gateway.get_record(USER_PROFILE, 7)["payload"]["sender_id"], "user:uuid-new")

    def test_known_uuid_sender_resolves_without_guessing(self):
        gateway = FakeKoloGateway()
        upsert_user_profile(
            gateway,
            {**SUBMITTER, "org_id": "default", "sender_id": "user:uuid-known"},
        )

        result = capture_expense_with_discovery(
            gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "12.00",
                "currency": "USD",
                "category": "Office Supplies",
            },
            sender_id="user:uuid-known",
            expense_id="exp_known_uuid",
        )
        self.assertFalse(result["onboarding_required"])
        self.assertEqual(result["expense"]["submitter_user_id"], 1)

    def test_existing_approver_snapshot_rejects_changed_routing(self):
        self._submit_one_expense()
        report = self.gateway.get_record(EXPENSE_REPORT, "er_test")["payload"]
        request = dict(self.gateway.get_record(APPROVAL_REQUEST, "ar_test")["payload"])
        request["approver_user_id"] = 3

        with self.assertRaises(ExpenseFlowError) as ctx:
            _create_approver_snapshot(
                self.gateway,
                report,
                request,
                {"approval_policy": {"version": 1}},
            )
        self.assertEqual(ctx.exception.code, "immutable_snapshot_conflict")

    def test_submit_report_holds_when_no_approver(self):
        self.gateway.records.pop((APPROVAL_POLICY, "default"))
        capture_expense(
            self.gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "45.00",
                "currency": "USD",
                "category": "Office Supplies",
                "receipt_ref": "receipt_1",
            },
            1,
            expense_id="exp_1",
        )
        result = submit_report_for_approval(self.gateway, 1, ["exp_1"], report_id="er_held")

        self.assertEqual(result["status"], "held_pending_manager")
        self.assertEqual(self.gateway.get_record(EXPENSE_REPORT, "er_held")["status"], "held_pending_manager")
        self.assertEqual(self.gateway.get_record(EXPENSE, "exp_1")["status"], "held_pending_manager")
        self.assertEqual(self.gateway.messages, [])

    def test_approval_decision_updates_records(self):
        self._submit_one_expense()
        result = decide_report_approval(self.gateway, "ar_test", 2, "approved", decision_id="ad_test")

        self.assertEqual(result["report"]["status"], "approved")
        self.assertEqual(self.gateway.get_record(EXPENSE_REPORT, "er_test")["status"], "approved")
        self.assertEqual(self.gateway.get_record(EXPENSE, "exp_1")["status"], "approved")

    def test_wrong_approver_is_rejected(self):
        self._submit_one_expense()
        with self.assertRaises(ExpenseFlowError) as ctx:
            decide_report_approval(self.gateway, "ar_test", 3, "approved")
        self.assertEqual(ctx.exception.code, "wrong_approver")

    def test_export_approved_report_csv_updates_statuses(self):
        self._submit_one_expense()
        decide_report_approval(self.gateway, "ar_test", 2, "approved", decision_id="ad_test")
        export = export_approved_report_csv(self.gateway, "er_test")
        rows = list(csv.DictReader(StringIO(export["csv"])))

        self.assertEqual(export["status"], "ok")
        self.assertEqual(rows[0]["expense_id"], "exp_1")
        self.assertEqual(self.gateway.get_record(EXPENSE_REPORT, "er_test")["status"], "exported")
        self.assertEqual(self.gateway.get_record(EXPENSE, "exp_1")["status"], "exported")

    def test_export_unapproved_report_is_blocked(self):
        self._submit_one_expense()
        with self.assertRaises(ExpenseFlowError) as ctx:
            export_approved_report_csv(self.gateway, "er_test")
        self.assertEqual(ctx.exception.code, "report_not_approved")

    def test_audit_log_is_idempotent(self):
        first = self.gateway.log_action("skill.test", "Same event", "same-key")
        second = self.gateway.log_action("skill.test", "Same event", "same-key")

        self.assertEqual(first["auditEventId"], second["auditEventId"])

    def _submit_one_expense(self):
        capture_expense(
            self.gateway,
            {
                "vendor": "Office Depot",
                "date": "2026-08-18",
                "amount": "45.00",
                "currency": "USD",
                "category": "Office Supplies",
                "receipt_ref": "receipt_1",
            },
            1,
            expense_id="exp_1",
        )
        return submit_report_for_approval(
            self.gateway,
            1,
            ["exp_1"],
            report_id="er_test",
            approval_request_id="ar_test",
        )


if __name__ == "__main__":
    unittest.main()
