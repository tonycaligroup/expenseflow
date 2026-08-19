import unittest
from datetime import date, timedelta

from scripts.expenseflow.errors import ExpenseFlowError
from scripts.expenseflow.policy_engine import route_approver, validate_approver


class ApproverRoutingTests(unittest.TestCase):
    def test_uses_explicit_submitter_approver(self):
        result = route_approver(
            {"user_id": 1, "department": "Engineering", "approver_user_id": 2},
            "45.00",
            {"approval_policy": {"default_approver_user_id": 3}},
            [
                {
                    "user_id": 2,
                    "display_name": "Kyle",
                    "status": "active",
                    "can_approve": True,
                    "approval_scope": {"departments": ["Engineering"], "max_amount": "1000.00"},
                },
                {
                    "user_id": 3,
                    "display_name": "Kendra",
                    "status": "active",
                    "can_approve": True,
                    "approval_scope": {"departments": [], "max_amount": "1000.00"},
                },
            ],
        )

        self.assertEqual(result["approver_user_id"], 2)
        self.assertEqual(result["routing_reason"], "user_profile")

    def test_falls_back_to_department_policy(self):
        result = route_approver(
            {"user_id": 1, "department": "Engineering"},
            "45.00",
            {"department_policies": {"Engineering": {"primary_approver_user_id": 2}}},
            [
                {
                    "user_id": 2,
                    "display_name": "Kyle",
                    "status": "active",
                    "can_approve": True,
                    "approval_scope": {"departments": ["Engineering"], "max_amount": "1000.00"},
                }
            ],
        )

        self.assertEqual(result["approver_user_id"], 2)
        self.assertEqual(result["routing_reason"], "department_policy")

    def test_holds_when_no_valid_approver(self):
        result = route_approver(
            {"user_id": 1, "department": "Engineering"},
            "2000.00",
            {"approval_policy": {"default_approver_user_id": 2}},
            [
                {
                    "user_id": 2,
                    "display_name": "Kyle",
                    "status": "active",
                    "can_approve": True,
                    "approval_scope": {"departments": ["Engineering"], "max_amount": "1000.00"},
                }
            ],
        )

        self.assertEqual(result["status"], "held_pending_manager")

    def test_validate_approver_rejects_out_of_scope(self):
        with self.assertRaises(ExpenseFlowError) as ctx:
            validate_approver(
                {
                    "user_id": 2,
                    "display_name": "Kyle",
                    "status": "active",
                    "can_approve": True,
                    "approval_scope": {"departments": ["Sales"], "max_amount": "1000.00"},
                },
                "45.00",
                "Engineering",
            )
        self.assertEqual(ctx.exception.code, "approver_scope_mismatch")

    def test_active_delegation_routes_to_valid_delegate(self):
        today = date.today()
        result = route_approver(
            {"user_id": 1, "department": "Engineering", "approver_user_id": 2},
            "45.00",
            {
                "approval_delegations": [
                    {
                        "delegator_user_id": 2,
                        "delegate_user_id": 3,
                        "valid_from": (today - timedelta(days=1)).isoformat(),
                        "valid_until": (today + timedelta(days=1)).isoformat(),
                        "status": "active",
                    }
                ]
            },
            [
                {"user_id": 2, "status": "active", "can_approve": True},
                {"user_id": 3, "display_name": "Delegate", "status": "active", "can_approve": True},
            ],
        )

        self.assertEqual(result["approver_user_id"], 3)
        self.assertEqual(result["delegated_from_user_id"], 2)

    def test_self_approval_candidate_is_never_selected(self):
        result = route_approver(
            {"user_id": 1, "approver_user_id": 1},
            "45.00",
            {},
            [{"user_id": 1, "status": "active", "can_approve": True}],
        )
        self.assertEqual(result["status"], "held_pending_manager")
        self.assertEqual(result["errors"][0]["error"], "self_approval_not_allowed")


if __name__ == "__main__":
    unittest.main()
