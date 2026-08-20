import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.kolo_gateway import FakeKoloGateway
from scripts.expenseflow.kolo_workflows import (
    organization_setup_readiness,
    upsert_accounting_destination,
    upsert_approval_policy,
    upsert_expense_settings,
    upsert_user_profile,
)
from scripts.expenseflow.setup_readiness import evaluate_setup_readiness


ORG_ID = "9db4581f-cc65-4fe7-8796-2d8b63bf80b5"
SETTINGS = {
    "expense_admin_user_ids": [99],
    "allowed_categories": ["Travel", "Meals"],
    "receipt_required_above": "25.00",
    "approval_reminders": {
        "enabled": True,
        "initial_delay_hours": 24,
        "interval_hours": 24,
        "max_attempts": 3,
    },
}
POLICY = {"version": 2, "default_approver_user_id": 2, "fallback_approver_user_id": 3}
SUBMITTER = {
    "user_id": 1,
    "org_id": ORG_ID,
    "display_name": "Employee",
    "status": "active",
    "department": "Operations",
    "policy_acknowledged_version": 2,
}
APPROVER = {
    "user_id": 2,
    "org_id": ORG_ID,
    "display_name": "Manager",
    "status": "active",
    "can_submit_expenses": False,
    "can_approve": True,
    "sender_id": "sender-approver-uuid",
    "approval_scope": {"departments": ["Operations"], "max_amount": "1000.00"},
}


def configured_gateway(destination=None, **kwargs):
    peers = [
        {"user_id": 1, "display_name": "Employee", "org_id": ORG_ID},
        {"user_id": 2, "display_name": "Manager", "org_id": ORG_ID},
    ]
    gateway = FakeKoloGateway(peers=peers, **kwargs)
    upsert_expense_settings(gateway, ORG_ID, SETTINGS)
    upsert_approval_policy(gateway, ORG_ID, POLICY)
    upsert_user_profile(gateway, SUBMITTER)
    upsert_user_profile(gateway, APPROVER)
    upsert_accounting_destination(
        gateway,
        ORG_ID,
        destination or {"destination_type": "csv", "config": {"delivery_method": "message"}},
    )
    return gateway


class SetupReadinessTests(unittest.TestCase):
    def test_complete_csv_setup_is_ready(self):
        result = organization_setup_readiness(configured_gateway(), ORG_ID)

        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["can_launch_pilot"])
        self.assertEqual(result["summary"]["blocker_count"], 0)
        self.assertEqual(result["summary"]["warning_count"], 0)

    def test_missing_setup_is_not_ready(self):
        result = organization_setup_readiness(FakeKoloGateway())

        self.assertEqual(result["status"], "not_ready")
        self.assertFalse(result["can_launch_pilot"])
        self.assertGreaterEqual(result["summary"]["blocker_count"], 3)

    def test_placeholder_org_id_blocks_pilot(self):
        result = evaluate_setup_readiness(
            "default",
            settings=SETTINGS,
            approval_policy=POLICY,
            destination={"status": "active", "destination_type": "csv"},
            profiles=[SUBMITTER, APPROVER],
            peers=[{"user_id": 1}, {"user_id": 2}],
        )

        check = next(check for check in result["checks"] if check["id"] == "organization_identity")
        self.assertEqual(check["status"], "blocker")
        self.assertFalse(result["can_launch_pilot"])

    def test_platform_user_identity_does_not_require_sender_mapping(self):
        result = evaluate_setup_readiness(
            ORG_ID,
            settings=SETTINGS,
            approval_policy=POLICY,
            destination={"status": "active", "destination_type": "csv"},
            profiles=[SUBMITTER, {**APPROVER, "sender_id": None}],
            peers=[{"user_id": 1}, {"user_id": 2}],
        )

        check = next(check for check in result["checks"] if check["id"] == "approval_identity_verification")
        self.assertEqual(check["status"], "pass")
        self.assertNotIn(
            "approval_sender_mappings",
            {candidate["id"] for candidate in result["checks"]},
        )

    def test_self_approval_is_a_blocker(self):
        result = evaluate_setup_readiness(
            ORG_ID,
            settings=SETTINGS,
            approval_policy={"version": 2, "default_approver_user_id": 1},
            destination={"status": "active", "destination_type": "csv"},
            profiles=[SUBMITTER],
            peers=[{"user_id": 1}],
        )

        coverage = next(check for check in result["checks"] if check["id"] == "approver_coverage")
        self.assertEqual(coverage["status"], "blocker")

    def test_directory_and_policy_gaps_are_warnings(self):
        profiles = [
            {**SUBMITTER, "policy_acknowledged_version": 1},
            APPROVER,
            {"user_id": 4, "org_id": ORG_ID, "status": "pending_admin_approval"},
        ]
        result = evaluate_setup_readiness(
            ORG_ID,
            settings=SETTINGS,
            approval_policy=POLICY,
            destination={"status": "active", "destination_type": "csv"},
            profiles=profiles,
            peers=[{"user_id": 1}, {"user_id": 2}, {"user_id": 4}, {"user_id": 5}],
        )

        self.assertEqual(result["status"], "ready_with_warnings")
        warning_ids = {check["id"] for check in result["checks"] if check["status"] == "warning"}
        self.assertEqual(
            warning_ids,
            {"policy_acknowledgements", "directory_discovery", "employee_onboarding"},
        )

    def test_malformed_settings_become_blockers_instead_of_crashing(self):
        result = evaluate_setup_readiness(
            ORG_ID,
            settings={
                "expense_admin_user_id": 99,
                "allowed_categories": "Travel",
                "receipt_required_above": "not-money",
                "approval_reminders": "daily",
            },
            approval_policy={"version": "not-an-int", "default_approver_user_id": 2},
            destination={"status": "active", "destination_type": "csv"},
            profiles=[SUBMITTER, APPROVER],
            peers=[{"user_id": 1}, {"user_id": 2}],
        )

        blocker_ids = {check["id"] for check in result["checks"] if check["status"] == "blocker"}
        self.assertTrue(
            {"expense_categories", "receipt_policy", "approval_reminders", "policy_acknowledgements"}
            <= blocker_ids
        )

    def test_sheets_destination_is_checked_read_only(self):
        destination = {
            "destination_type": "sheets",
            "config": {"spreadsheet_id": "sheet_1", "sheet_name": "ExpenseFlow"},
        }
        gateway = configured_gateway(destination=destination)
        gateway.add_spreadsheet("sheet_1")

        result = organization_setup_readiness(gateway, ORG_ID)

        self.assertEqual(result["status"], "ready")
        self.assertEqual([operation["operation"] for operation in gateway.sheet_operations], ["metadata", "read"])

    def test_incompatible_sheet_headers_block_launch_without_mutation(self):
        destination = {
            "destination_type": "sheets",
            "config": {"spreadsheet_id": "sheet_1", "sheet_name": "ExpenseFlow"},
        }
        gateway = configured_gateway(destination=destination)
        gateway.add_spreadsheet("sheet_1", rows=[["Not", "ExpenseFlow"]])

        result = organization_setup_readiness(gateway, ORG_ID)

        connection = next(check for check in result["checks"] if check["id"] == "destination_connection")
        self.assertEqual(connection["status"], "blocker")
        self.assertEqual(connection["details"]["error_code"], "sheets_header_mismatch")
        self.assertEqual([operation["operation"] for operation in gateway.sheet_operations], ["metadata", "read"])

    def test_unreachable_sheets_destination_blocks_launch(self):
        destination = {
            "destination_type": "sheets",
            "config": {"spreadsheet_id": "missing", "sheet_name": "ExpenseFlow"},
        }
        result = organization_setup_readiness(configured_gateway(destination=destination), ORG_ID)

        connection = next(check for check in result["checks"] if check["id"] == "destination_connection")
        self.assertEqual(connection["status"], "blocker")
        self.assertEqual(connection["details"]["error_code"], "sheets_not_found")

    def test_disconnected_qbo_blocks_launch_without_a_write(self):
        destination = {
            "destination_type": "qbo",
            "config": {
                "realm_id": "1234567890",
                "transaction_type": "bill",
                "category_account_ids": {"*": "41"},
            },
        }
        gateway = configured_gateway(destination=destination)

        result = organization_setup_readiness(gateway, ORG_ID)

        connection = next(check for check in result["checks"] if check["id"] == "destination_connection")
        self.assertEqual(connection["status"], "blocker")
        self.assertEqual(connection["details"]["error_code"], "qbo_not_connected")
        self.assertEqual(gateway.qbo_operations, [{"operation": "status"}])

    def test_skipped_external_check_is_a_warning(self):
        destination = {
            "destination_type": "sheets",
            "config": {"spreadsheet_id": "missing", "sheet_name": "ExpenseFlow"},
        }
        gateway = configured_gateway(destination=destination)

        result = organization_setup_readiness(gateway, ORG_ID, verify_destination=False)

        self.assertEqual(result["status"], "ready_with_warnings")
        self.assertEqual(gateway.sheet_operations, [])

    def test_unimplemented_csv_delivery_adapter_blocks_launch(self):
        gateway = configured_gateway(
            destination={"destination_type": "csv", "config": {"delivery_method": "drive"}}
        )

        result = organization_setup_readiness(gateway, ORG_ID)

        connection = next(check for check in result["checks"] if check["id"] == "destination_connection")
        self.assertEqual(connection["status"], "blocker")
        self.assertEqual(connection["details"]["delivery_method"], "drive")

    def test_directory_failure_is_reported_without_hiding_pending_onboarding(self):
        class FailingDirectoryGateway(FakeKoloGateway):
            def list_peers(self):
                raise ExpenseFlowError("directory_unavailable", "Directory unavailable.")

        source = configured_gateway()
        gateway = FailingDirectoryGateway()
        gateway.records = source.records
        upsert_user_profile(
            gateway,
            {"user_id": 4, "org_id": ORG_ID, "status": "pending_admin_approval"},
        )

        result = organization_setup_readiness(gateway, ORG_ID)

        warning_ids = {check["id"] for check in result["checks"] if check["status"] == "warning"}
        self.assertEqual(warning_ids, {"directory_discovery", "employee_onboarding"})


if __name__ == "__main__":
    unittest.main()
