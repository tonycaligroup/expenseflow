import unittest

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.onboarding_engine import (
    acknowledge_policy,
    approve_onboarding,
    create_delegation,
    create_discovered_profile,
)


class OnboardingEngineTests(unittest.TestCase):
    def test_creates_pending_discovered_profile_from_kolo_peer(self):
        profile = create_discovered_profile(
            {"userId": 7, "displayName": "New Employee", "orgId": "org_1"},
            "org_1",
            sender_id="user:abc",
            status="pending_admin_approval",
        )

        self.assertEqual(profile["user_id"], 7)
        self.assertEqual(profile["sender_id"], "user:abc")
        self.assertEqual(profile["status"], "pending_admin_approval")

    def test_onboarding_requires_different_active_approver(self):
        profile = {"user_id": 7, "status": "pending_admin_approval"}
        with self.assertRaises(ExpenseFlowError) as ctx:
            approve_onboarding(profile, {"user_id": 7, "status": "active", "can_approve": True}, 99, 2)
        self.assertEqual(ctx.exception.code, "self_approval_not_allowed")

    def test_policy_acknowledgement_activates_matching_user_and_version(self):
        profile = {"user_id": 7, "status": "pending_policy_ack", "required_policy_version": 2}

        active = acknowledge_policy(profile, 7, 2)

        self.assertEqual(active["status"], "active")
        self.assertEqual(active["policy_acknowledged_version"], 2)

    def test_policy_acknowledgement_rejects_stale_version(self):
        profile = {"user_id": 7, "status": "pending_policy_ack", "required_policy_version": 2}
        with self.assertRaises(ExpenseFlowError) as ctx:
            acknowledge_policy(profile, 7, 1)
        self.assertEqual(ctx.exception.code, "policy_version_mismatch")

    def test_delegation_uses_deterministic_validated_dates(self):
        delegation = create_delegation(
            {
                "delegator_user_id": 2,
                "delegate_user_id": 3,
                "valid_from": "2026-08-19",
                "valid_until": "2026-08-27",
            }
        )
        self.assertEqual(delegation["status"], "active")

    def test_delegation_rejects_reverse_date_range(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            create_delegation(
                {
                    "delegator_user_id": 2,
                    "delegate_user_id": 3,
                    "valid_from": "2026-08-27",
                    "valid_until": "2026-08-19",
                }
            )
        self.assertEqual(ctx.exception.code, "invalid_delegation_range")


if __name__ == "__main__":
    unittest.main()
