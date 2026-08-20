from .errors import ExpenseFlowError
from .models import DEFAULT_CATEGORIES
from .money import parse_money
from .policy_engine import route_approver
from .reminder_engine import normalize_reminder_settings


PENDING_PROFILE_STATUSES = {
    "discovered",
    "pending_admin_approval",
    "pending_manager_assignment",
    "pending_policy_ack",
}


def evaluate_setup_readiness(
    org_id,
    settings=None,
    approval_policy=None,
    destination=None,
    profiles=None,
    department_policies=None,
    approval_delegations=None,
    peers=None,
    destination_health=None,
    directory_error=None,
):
    settings = settings or {}
    approval_policy = approval_policy or {}
    destination = destination or {}
    profiles = list(profiles or [])
    department_policies = dict(department_policies or {})
    approval_delegations = list(approval_delegations or [])
    peers = list(peers or [])
    checks = []

    _organization_identity_check(checks, org_id)
    _settings_checks(checks, settings)
    _approval_checks(
        checks,
        approval_policy,
        profiles,
        department_policies,
        approval_delegations,
    )
    _directory_checks(checks, profiles, peers, directory_error)
    _destination_checks(checks, destination, destination_health)

    blockers = [check for check in checks if check["status"] == "blocker"]
    warnings = [check for check in checks if check["status"] == "warning"]
    if blockers:
        status = "not_ready"
        next_action = blockers[0]["next_action"]
    elif warnings:
        status = "ready_with_warnings"
        next_action = "Review the remaining warnings, then run a controlled employee pilot."
    else:
        status = "ready"
        next_action = "Run a controlled employee pilot before broad rollout."
    return {
        "org_id": str(org_id),
        "status": status,
        "can_launch_pilot": not blockers,
        "summary": {
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "pass_count": len(checks) - len(blockers) - len(warnings),
            "profile_count": len(profiles),
            "active_profile_count": len([profile for profile in profiles if profile.get("status") == "active"]),
            "pending_profile_count": len(
                [profile for profile in profiles if profile.get("status") in PENDING_PROFILE_STATUSES]
            ),
            "peer_count": len(peers),
        },
        "checks": checks,
        "next_action": next_action,
        "schema_version": 1,
    }


def _organization_identity_check(checks, org_id):
    if not str(org_id or "").strip() or str(org_id).strip().lower() == "default":
        checks.append(
            _check(
                "organization_identity",
                "blocker",
                "ExpenseFlow is using the placeholder organization ID.",
                "Configure and run ExpenseFlow with the actual Kolo organization UUID before any pilot.",
            )
        )
    else:
        checks.append(_check("organization_identity", "pass", "The Kolo organization ID is explicit."))


def _settings_checks(checks, settings):
    if not settings:
        checks.append(
            _check(
                "organization_settings",
                "blocker",
                "Organization settings are missing.",
                "Configure organization settings and at least one ExpenseFlow administrator.",
            )
        )
        return
    checks.append(_check("organization_settings", "pass", "Organization settings are configured."))
    admin_ids = _admin_ids(settings)
    if admin_ids:
        checks.append(
            _check(
                "expense_administrators",
                "pass",
                "ExpenseFlow administrators are configured.",
                details={"admin_count": len(admin_ids)},
            )
        )
    else:
        checks.append(
            _check(
                "expense_administrators",
                "blocker",
                "No ExpenseFlow administrator is configured.",
                "Add at least one expense_admin_user_id before onboarding employees.",
            )
        )
    categories = settings.get("allowed_categories")
    if categories is not None and (
        not isinstance(categories, list)
        or not categories
        or any(not isinstance(category, str) or not category.strip() for category in categories)
    ):
        checks.append(
            _check(
                "expense_categories",
                "blocker",
                "The configured expense categories are invalid.",
                "Configure allowed_categories as a non-empty list of category names.",
            )
        )
    elif categories:
        checks.append(
            _check(
                "expense_categories",
                "pass",
                "Custom expense categories are configured.",
                details={"category_count": len(categories)},
            )
        )
    else:
        checks.append(
            _check(
                "expense_categories",
                "warning",
                "ExpenseFlow default categories will be used.",
                "Confirm the default categories or configure organization-specific categories.",
                details={"category_count": len(DEFAULT_CATEGORIES)},
            )
        )
    receipt_threshold = settings.get("receipt_required_above")
    if receipt_threshold is None:
        checks.append(
            _check(
                "receipt_policy",
                "warning",
                "No amount-based receipt threshold is configured.",
                "Choose a receipt threshold or explicitly accept receipt-optional submissions.",
            )
        )
    else:
        try:
            parse_money(receipt_threshold, "receipt_required_above", allow_zero=True)
        except ExpenseFlowError as exc:
            checks.append(
                _check(
                    "receipt_policy",
                    "blocker",
                    "The receipt threshold is invalid.",
                    "Configure receipt_required_above as a non-negative monetary amount.",
                    details={"error_code": exc.code},
                )
            )
        else:
            checks.append(_check("receipt_policy", "pass", "The receipt threshold is configured."))
    try:
        reminder_config = normalize_reminder_settings(settings)
    except ExpenseFlowError as exc:
        checks.append(
            _check(
                "approval_reminders",
                "blocker",
                "The approval reminder configuration is invalid.",
                "Correct the approval_reminders settings before launching the pilot.",
                details={"error_code": exc.code},
            )
        )
    else:
        if reminder_config["enabled"]:
            checks.append(_check("approval_reminders", "pass", "Approval reminders are enabled."))
        else:
            checks.append(
                _check(
                    "approval_reminders",
                    "warning",
                    "Approval reminders are disabled.",
                    "Enable reminders before broad rollout if approvers need scheduled follow-up.",
                )
            )
    max_receipt_bytes = settings.get("max_receipt_bytes")
    if max_receipt_bytes is not None:
        try:
            valid_receipt_size = int(max_receipt_bytes) > 0
        except (TypeError, ValueError):
            valid_receipt_size = False
        if not valid_receipt_size:
            checks.append(
                _check(
                    "receipt_size_limit",
                    "blocker",
                    "The receipt file-size limit is invalid.",
                    "Set max_receipt_bytes to a positive integer or remove the custom limit.",
                )
            )
        else:
            checks.append(_check("receipt_size_limit", "pass", "The receipt file-size limit is valid."))


def _approval_checks(checks, approval_policy, profiles, department_policies, approval_delegations):
    if not approval_policy:
        checks.append(
            _check(
                "approval_policy",
                "blocker",
                "The organization approval policy is missing.",
                "Configure a versioned approval policy with default and fallback approvers.",
            )
        )
        return
    checks.append(_check("approval_policy", "pass", "The organization approval policy is configured."))
    if not approval_policy.get("default_approver_user_id"):
        checks.append(
            _check(
                "default_approver",
                "blocker",
                "No default approver is configured.",
                "Configure a default approver so new or unmatched employees do not become stuck.",
            )
        )
    else:
        checks.append(_check("default_approver", "pass", "A default approver is configured."))

    active_submitters = [
        profile
        for profile in profiles
        if profile.get("status") == "active" and profile.get("can_submit_expenses", True)
    ]
    if not active_submitters:
        checks.append(
            _check(
                "active_submitters",
                "blocker",
                "No active employee can submit expenses.",
                "Discover employees, approve onboarding, assign approvers, and record policy acknowledgement.",
            )
        )
        return
    checks.append(
        _check(
            "active_submitters",
            "pass",
            "Active expense submitters are available.",
            details={"submitter_count": len(active_submitters)},
        )
    )
    policies = {
        "approval_policy": approval_policy,
        "department_policies": department_policies,
        "approval_delegations": approval_delegations,
    }
    uncovered = []
    for submitter in active_submitters:
        try:
            route = route_approver(submitter, "0.01", policies, profiles)
        except ExpenseFlowError:
            route = {"status": "held_pending_manager"}
        if route.get("status") != "ok":
            uncovered.append(str(submitter.get("user_id")))
    if uncovered:
        checks.append(
            _check(
                "approver_coverage",
                "blocker",
                "One or more active submitters have no valid approver route.",
                "Assign a different active approver or add department/default fallback routing.",
                details={"user_ids": sorted(uncovered)},
            )
        )
    else:
        checks.append(
            _check(
                "approver_coverage",
                "pass",
                "Every active submitter has a valid non-self approver route.",
            )
        )
    active_approver_ids = {
        str(profile.get("user_id"))
        for profile in profiles
        if profile.get("status") == "active" and profile.get("can_approve")
    }
    if active_approver_ids:
        checks.append(
            _check(
                "approval_identity_verification",
                "pass",
                "Kolo platform user IDs can be matched directly to active ExpenseFlow approvers.",
                details={"user_ids": sorted(active_approver_ids)},
            )
        )
    try:
        policy_version = int(approval_policy.get("version", 1))
    except (TypeError, ValueError):
        checks.append(
            _check(
                "policy_acknowledgements",
                "blocker",
                "The approval policy version is invalid.",
                "Set approval policy version to an integer before collecting acknowledgements.",
            )
        )
        return
    stale_acknowledgements = sorted(
        str(profile.get("user_id"))
        for profile in active_submitters
        if profile.get("policy_acknowledged_version") != policy_version
    )
    if stale_acknowledgements:
        checks.append(
            _check(
                "policy_acknowledgements",
                "warning",
                "Some active submitters have not acknowledged the current policy version.",
                "Request policy acknowledgement before their next submission.",
                details={"user_ids": stale_acknowledgements, "policy_version": policy_version},
            )
        )
    else:
        checks.append(_check("policy_acknowledgements", "pass", "Active submitters acknowledged the current policy."))


def _directory_checks(checks, profiles, peers, directory_error):
    if directory_error:
        checks.append(
            _check(
                "directory_discovery",
                "warning",
                "Kolo user discovery could not be checked.",
                "Retry directory discovery before broad rollout.",
                details={"error_code": directory_error},
            )
        )
    else:
        profile_ids = {str(profile.get("user_id")) for profile in profiles}
        undiscovered = sorted(
            str(peer.get("user_id", peer.get("userId")))
            for peer in peers
            if str(peer.get("user_id", peer.get("userId"))) not in profile_ids
        )
        if undiscovered:
            checks.append(
                _check(
                    "directory_discovery",
                    "warning",
                    "Some current Kolo organization members are not yet discovered in ExpenseFlow.",
                    "Run user reconciliation or allow first-expense discovery to start onboarding.",
                    details={"user_ids": undiscovered},
                )
            )
        elif peers:
            checks.append(
                _check("directory_discovery", "pass", "Current Kolo peers are represented in ExpenseFlow.")
            )
        else:
            checks.append(
                _check(
                    "directory_discovery",
                    "warning",
                    "Kolo returned no discoverable peers for this organization.",
                    "Confirm whether the organization has additional members before broad rollout.",
                )
            )
    pending = sorted(
        str(profile.get("user_id"))
        for profile in profiles
        if profile.get("status") in PENDING_PROFILE_STATUSES
    )
    if pending:
        checks.append(
            _check(
                "employee_onboarding",
                "warning",
                "Some discovered employees have incomplete onboarding.",
                "Complete administrator approval, approver assignment, and policy acknowledgement.",
                details={"user_ids": pending},
            )
        )
    else:
        checks.append(_check("employee_onboarding", "pass", "No discovered employee is waiting on onboarding."))


def _destination_checks(checks, destination, destination_health):
    if not destination:
        checks.append(
            _check(
                "accounting_destination",
                "blocker",
                "No accounting destination is configured.",
                "Choose CSV, Google Sheets, or QuickBooks Online.",
            )
        )
        return
    if destination.get("status") != "active":
        checks.append(
            _check(
                "accounting_destination",
                "blocker",
                "The accounting destination is not active.",
                "Activate or replace the organization accounting destination.",
            )
        )
        return
    destination_type = destination.get("destination_type")
    if destination_type not in {"csv", "sheets", "qbo"}:
        checks.append(
            _check(
                "accounting_destination",
                "blocker",
                "The accounting destination type is invalid.",
                "Choose CSV, Google Sheets, or QuickBooks Online.",
            )
        )
        return
    checks.append(
        _check(
            "accounting_destination",
            "pass",
            "An active accounting destination is configured.",
            details={"destination_type": destination_type},
        )
    )
    if destination_type == "csv":
        delivery_method = (destination.get("config") or {}).get("delivery_method", "message")
        if delivery_method == "message":
            checks.append(
                _check(
                    "destination_connection",
                    "pass",
                    "CSV message delivery requires no external connection.",
                )
            )
        else:
            checks.append(
                _check(
                    "destination_connection",
                    "blocker",
                    "The configured automated CSV delivery adapter is not implemented.",
                    "Use CSV message delivery or implement and verify the selected delivery adapter.",
                    details={"delivery_method": delivery_method},
                )
            )
        return
    if destination_health is None:
        checks.append(
            _check(
                "destination_connection",
                "warning",
                "The external accounting destination was not checked in this readiness run.",
                "Run setup readiness with integration checks enabled.",
            )
        )
    elif destination_health.get("status") == "pass":
        checks.append(
            _check(
                "destination_connection",
                "pass",
                "The external accounting destination is reachable.",
            )
        )
    else:
        checks.append(
            _check(
                "destination_connection",
                "blocker",
                "The external accounting destination is not usable.",
                destination_health.get("next_action") or "Repair or replace the accounting destination.",
                details={"error_code": destination_health.get("error_code", "destination_unavailable")},
            )
        )


def _check(check_id, status, message, next_action=None, details=None):
    check = {"id": check_id, "status": status, "message": message}
    if next_action:
        check["next_action"] = next_action
    if details:
        check["details"] = details
    return check


def _admin_ids(settings):
    values = settings.get("expense_admin_user_ids") or []
    if not isinstance(values, list):
        values = [values]
    if settings.get("expense_admin_user_id") is not None:
        values = [*values, settings["expense_admin_user_id"]]
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))
