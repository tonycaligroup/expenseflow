from datetime import date

from .errors import ExpenseFlowError
from .models import utc_now


ONBOARDING_STATUSES = {
    "discovered",
    "pending_admin_approval",
    "pending_policy_ack",
    "pending_manager_assignment",
}
def create_discovered_profile(peer, org_id, sender_id=None, status="discovered"):
    if status not in ONBOARDING_STATUSES:
        raise ExpenseFlowError(
            "invalid_user_status",
            "Newly discovered users must start in an onboarding status.",
            details={"status": status},
        )
    user_id = _user_id(peer)
    if user_id is None:
        raise ExpenseFlowError("missing_user_id", "Discovered Kolo user is missing userId.")
    now = utc_now()
    return {
        "user_id": user_id,
        "sender_id": sender_id,
        "display_name": str(peer.get("display_name") or peer.get("displayName") or "").strip(),
        "org_id": str(peer.get("org_id") or peer.get("orgId") or org_id),
        "department": None,
        "approver_user_id": None,
        "can_approve": False,
        "status": status,
        "discovered_at": now,
        "policy_acknowledged_at": None,
        "schema_version": 1,
    }


def approve_onboarding(profile, approver, admin_user_id, policy_version):
    if profile.get("status") not in {"discovered", "pending_admin_approval", "pending_manager_assignment"}:
        raise ExpenseFlowError(
            "invalid_user_status",
            "User is not waiting for onboarding approval.",
            details={"status": profile.get("status")},
        )
    if approver.get("status") != "active" or not approver.get("can_approve"):
        raise ExpenseFlowError("invalid_approver", "Assigned approver must be active and allowed to approve.")
    if approver.get("user_id") == profile.get("user_id"):
        raise ExpenseFlowError("self_approval_not_allowed", "A user cannot be assigned as their own approver.")

    updated = dict(profile)
    updated.update(
        {
            "approver_user_id": approver.get("user_id"),
            "status": "pending_policy_ack",
            "onboarding_approved_by_user_id": admin_user_id,
            "onboarding_approved_at": utc_now(),
            "required_policy_version": policy_version,
        }
    )
    return updated


def acknowledge_policy(profile, acknowledging_user_id, policy_version):
    if profile.get("user_id") != acknowledging_user_id:
        raise ExpenseFlowError(
            "wrong_policy_acknowledger",
            "Only the employee can acknowledge their expense policy.",
        )
    if profile.get("status") != "pending_policy_ack":
        raise ExpenseFlowError(
            "invalid_user_status",
            "User is not waiting for policy acknowledgement.",
            details={"status": profile.get("status")},
        )
    required_version = profile.get("required_policy_version")
    if required_version != policy_version:
        raise ExpenseFlowError(
            "policy_version_mismatch",
            "The acknowledged policy version is not the currently required version.",
            details={"required_policy_version": required_version, "acknowledged_policy_version": policy_version},
        )

    updated = dict(profile)
    updated["status"] = "active"
    updated["policy_acknowledged_version"] = policy_version
    updated["policy_acknowledged_at"] = utc_now()
    return updated


def create_delegation(data):
    delegator_user_id = _normalized_user_id(data.get("delegator_user_id"))
    delegate_user_id = _normalized_user_id(data.get("delegate_user_id"))
    if delegator_user_id is None or delegate_user_id is None:
        raise ExpenseFlowError("missing_user_id", "Delegation requires delegator and delegate user IDs.")
    if delegator_user_id == delegate_user_id:
        raise ExpenseFlowError("self_delegation_not_allowed", "An approver cannot delegate to themselves.")
    try:
        valid_from = date.fromisoformat(str(data.get("valid_from") or ""))
        valid_until = date.fromisoformat(str(data.get("valid_until") or ""))
    except ValueError:
        raise ExpenseFlowError("invalid_delegation_date", "Delegation dates must use YYYY-MM-DD.")
    if valid_until < valid_from:
        raise ExpenseFlowError("invalid_delegation_range", "Delegation end date cannot precede its start date.")
    status = data.get("status", "active")
    if status not in {"active", "disabled"}:
        raise ExpenseFlowError("invalid_delegation_status", "Delegation status must be active or disabled.")
    return {
        "delegator_user_id": delegator_user_id,
        "delegate_user_id": delegate_user_id,
        "valid_from": valid_from.isoformat(),
        "valid_until": valid_until.isoformat(),
        "status": status,
        "created_at": data.get("created_at") or utc_now(),
        "schema_version": 1,
    }


def _user_id(peer):
    return _normalized_user_id(peer.get("user_id", peer.get("userId")))


def _normalized_user_id(user_id):
    if user_id is None:
        return None
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return user_id
