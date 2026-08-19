from datetime import date
from decimal import Decimal

from .errors import ExpenseFlowError
from .money import parse_money


def validate_approver(approver, amount, department=None):
    if not approver:
        raise ExpenseFlowError("missing_approver", "No approver was selected.")
    if approver.get("status") != "active":
        raise ExpenseFlowError("invalid_approver", "Approver is not active.")
    if not approver.get("can_approve"):
        raise ExpenseFlowError("invalid_approver", "Selected user is not marked as an approver.")

    scope = approver.get("approval_scope") or {}
    departments = scope.get("departments") or []
    if departments and department and department not in departments:
        raise ExpenseFlowError(
            "approver_scope_mismatch",
            "Approver does not cover this department.",
            details={"department": department, "allowed_departments": departments},
        )

    max_amount = scope.get("max_amount")
    if max_amount is not None and parse_money(amount, "amount") > parse_money(max_amount, "max_amount"):
        raise ExpenseFlowError(
            "approver_limit_exceeded",
            "Expense exceeds approver approval limit.",
            details={"max_amount": str(Decimal(str(max_amount)))},
        )
    return True


def route_approver(submitter, amount, policies, user_profiles):
    users = {profile.get("user_id"): profile for profile in user_profiles}
    department = submitter.get("department")

    candidates = []
    if submitter.get("approver_user_id"):
        candidates.append(("user_profile", submitter.get("approver_user_id")))

    department_policies = policies.get("department_policies") or {}
    dept_policy = department_policies.get(department)
    if dept_policy:
        candidates.append(("department_policy", dept_policy.get("primary_approver_user_id")))
        candidates.append(("department_backup", dept_policy.get("backup_approver_user_id")))

    org_policy = policies.get("approval_policy") or {}
    candidates.append(("default_approver", org_policy.get("default_approver_user_id")))
    candidates.append(("fallback_approver", org_policy.get("fallback_approver_user_id")))

    errors = []
    seen = set()
    for reason, user_id in candidates:
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        if user_id == submitter.get("user_id"):
            errors.append({"user_id": user_id, "reason": reason, "error": "self_approval_not_allowed"})
            continue
        approver = users.get(user_id)
        try:
            validate_approver(approver, amount, department)
            delegated = _active_delegate(user_id, policies.get("approval_delegations") or [])
            if delegated is not None:
                if delegated == submitter.get("user_id"):
                    errors.append({"user_id": delegated, "reason": reason, "error": "self_approval_not_allowed"})
                    continue
                validate_approver(users.get(delegated), amount, department)
                return {
                    "status": "ok",
                    "approver_user_id": delegated,
                    "approver_name": users[delegated].get("display_name"),
                    "routing_reason": f"{reason}_delegation",
                    "delegated_from_user_id": user_id,
                }
            return {
                "status": "ok",
                "approver_user_id": user_id,
                "approver_name": approver.get("display_name"),
                "routing_reason": reason,
            }
        except ExpenseFlowError as exc:
            errors.append({"user_id": user_id, "reason": reason, "error": exc.code})

    return {
        "status": "held_pending_manager",
        "reason": "no_valid_approver",
        "errors": errors,
    }


def _active_delegate(delegator_user_id, delegations, today=None):
    current_date = today or date.today()
    matches = []
    for delegation in delegations:
        if delegation.get("status") != "active" or delegation.get("delegator_user_id") != delegator_user_id:
            continue
        try:
            valid_from = date.fromisoformat(str(delegation.get("valid_from")))
            valid_until = date.fromisoformat(str(delegation.get("valid_until")))
        except ValueError:
            continue
        if valid_from <= current_date <= valid_until:
            matches.append(delegation)
    if len(matches) > 1:
        raise ExpenseFlowError(
            "ambiguous_approval_delegation",
            "More than one active delegation applies to this approver.",
            details={"delegator_user_id": delegator_user_id},
        )
    return matches[0].get("delegate_user_id") if matches else None
