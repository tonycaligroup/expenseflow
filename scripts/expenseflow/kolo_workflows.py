from .approval_engine import create_approval_request, record_approval_decision
from .csv_export import generate_report_csv
from .errors import ExpenseFlowError
from .expense_core import create_expense, detect_duplicates
from .models import utc_now
from .onboarding_engine import acknowledge_policy, approve_onboarding, create_delegation, create_discovered_profile
from .report_engine import create_report, transition_report
from .status import validate_transition


EXPENSE_SETTINGS = "skill.expense_settings"
ACCOUNTING_DESTINATION = "skill.accounting_destination"
USER_PROFILE = "skill.user_profile"
APPROVAL_POLICY = "skill.approval_policy"
DEPARTMENT_POLICY = "skill.department_policy"
EXPENSE = "skill.expense"
EXPENSE_REPORT = "skill.expense_report"
APPROVAL_REQUEST = "skill.approval_request"
APPROVAL_DECISION = "skill.approval_decision"
APPROVAL_DELEGATION = "skill.approval_delegation"
APPROVER_SNAPSHOT = "skill.approver_snapshot"
IDENTITY_DISCOVERY = "skill.identity_discovery"


def upsert_user_profile(gateway, profile):
    user_id = _normalize_user_id(profile.get("user_id"))
    if user_id is None:
        raise ExpenseFlowError("missing_user_id", "User profile requires user_id.")
    payload = dict(profile)
    payload["user_id"] = user_id
    if payload.get("approver_user_id") is not None:
        payload["approver_user_id"] = _normalize_user_id(payload["approver_user_id"])
    return gateway.upsert_record(USER_PROFILE, user_id, payload, payload.get("status", "active"))


def upsert_expense_settings(gateway, org_id, settings):
    return gateway.upsert_record(EXPENSE_SETTINGS, org_id, {"org_id": org_id, **settings}, "active")


def upsert_approval_policy(gateway, org_id, policy):
    return gateway.upsert_record(APPROVAL_POLICY, org_id, policy, "active")


def upsert_department_policy(gateway, department, policy, org_id="default"):
    payload = {"org_id": org_id, "department": department, **policy}
    return gateway.upsert_record(DEPARTMENT_POLICY, f"{org_id}:{department}", payload, "active")


def upsert_accounting_destination(gateway, org_id, destination):
    payload = _accounting_destination_payload(org_id, destination)
    return gateway.upsert_record(ACCOUNTING_DESTINATION, org_id, payload, payload["status"])


def _accounting_destination_payload(org_id, destination):
    destination_type = str(destination.get("destination_type") or "").strip().lower()
    if destination_type not in {"csv", "sheets", "qbo"}:
        raise ExpenseFlowError(
            "invalid_accounting_destination",
            "Destination type must be csv, sheets, or qbo.",
        )
    config = destination.get("config") or {}
    if destination_type == "csv":
        delivery_method = config.get("delivery_method", "message")
        if delivery_method not in {"message", "drive", "gmail"}:
            raise ExpenseFlowError("invalid_delivery_method", "CSV delivery must use message, drive, or gmail.")
    elif destination_type == "sheets" and not config.get("spreadsheet_id"):
        raise ExpenseFlowError("missing_spreadsheet_id", "Google Sheets destination requires spreadsheet_id.")
    elif destination_type == "qbo" and not config.get("company_id"):
        raise ExpenseFlowError("missing_qbo_company_id", "QuickBooks destination requires company_id.")
    payload = {
        "org_id": org_id,
        "destination_type": destination_type,
        "config": config,
        "status": destination.get("status", "active"),
        "schema_version": 1,
    }
    return payload


def upsert_approval_delegation(gateway, data, delegation_id=None, org_id="default"):
    delegation = create_delegation(data)
    delegator = _payload(gateway.get_record(USER_PROFILE, delegation["delegator_user_id"]))
    delegate = _payload(gateway.get_record(USER_PROFILE, delegation["delegate_user_id"]))
    _require_record_org(delegator, org_id, "delegator")
    _require_record_org(delegate, org_id, "delegate")
    if delegator.get("status") != "active" or not delegator.get("can_approve"):
        raise ExpenseFlowError("invalid_delegator", "Delegator must be an active ExpenseFlow approver.")
    if delegate.get("status") != "active" or not delegate.get("can_approve"):
        raise ExpenseFlowError("invalid_delegate", "Delegate must be an active ExpenseFlow approver.")
    delegation["org_id"] = org_id
    external_id = delegation_id or (
        f"{org_id}:{delegation['delegator_user_id']}:{delegation['valid_from']}:{delegation['valid_until']}"
    )
    return gateway.upsert_record(APPROVAL_DELEGATION, external_id, delegation, delegation["status"])


def configure_organization(gateway, org_id, settings, approval_policy, destination):
    if not _admin_user_ids(settings):
        raise ExpenseFlowError(
            "missing_expense_admin",
            "Organization setup requires at least one ExpenseFlow admin user ID.",
        )
    destination_payload = _accounting_destination_payload(org_id, destination)
    return {
        "settings": upsert_expense_settings(gateway, org_id, settings),
        "approval_policy": upsert_approval_policy(gateway, org_id, approval_policy),
        "accounting_destination": gateway.upsert_record(
            ACCOUNTING_DESTINATION,
            org_id,
            destination_payload,
            destination_payload["status"],
        ),
    }


def reconcile_user_directory(gateway, org_id="default", deactivate_missing=False):
    peers = [peer for peer in gateway.list_peers() if _peer_in_org(peer, org_id)]
    if deactivate_missing and not peers:
        raise ExpenseFlowError(
            "empty_peer_snapshot",
            "Refusing to deactivate users from an empty Kolo peer snapshot.",
            retryable=True,
        )

    existing = {
        _normalize_user_id(profile.get("user_id")): profile
        for profile in (_payload(record) for record in gateway.list_records(USER_PROFILE))
        if str(profile.get("org_id", org_id)) == str(org_id)
    }
    peer_ids = set()
    created = []
    reactivated = []
    updated = []
    for peer in peers:
        user_id = _normalize_user_id(peer.get("user_id", peer.get("userId")))
        peer_ids.add(user_id)
        profile = existing.get(user_id)
        if profile is None:
            profile = create_discovered_profile(peer, org_id)
            created.append(user_id)
        else:
            profile = dict(profile)
            display_name = peer.get("display_name", peer.get("displayName"))
            if display_name:
                profile["display_name"] = display_name
            profile["org_id"] = str(peer.get("org_id") or peer.get("orgId") or org_id)
            if profile.get("status") == "deactivated":
                profile["status"] = "discovered"
                profile["reactivated_at"] = utc_now()
                reactivated.append(user_id)
            else:
                updated.append(user_id)
        upsert_user_profile(gateway, profile)

    deactivated = []
    if deactivate_missing:
        for user_id, profile in existing.items():
            if user_id in peer_ids or profile.get("status") in {"deactivated", "rejected"}:
                continue
            profile = dict(profile)
            profile["status"] = "deactivated"
            profile["deactivated_at"] = utc_now()
            upsert_user_profile(gateway, profile)
            deactivated.append(user_id)

    summary = {
        "org_id": org_id,
        "peer_count": len(peers),
        "created_user_ids": created,
        "reactivated_user_ids": reactivated,
        "updated_user_ids": updated,
        "deactivated_user_ids": deactivated,
    }
    gateway.log_action(
        "skill.user_profile",
        "ExpenseFlow user directory reconciled",
        f"expenseflow:user-reconcile:{org_id}:{utc_now()[:10]}",
        summary,
    )
    return summary


def capture_expense_with_discovery(
    gateway,
    expense_data,
    submitter_user_id=None,
    org_id="default",
    sender_id=None,
    expense_id=None,
):
    settings = _optional_payload(gateway, EXPENSE_SETTINGS, org_id, default={})
    if submitter_user_id is None:
        if not sender_id:
            raise ExpenseFlowError(
                "missing_submitter_identity",
                "Expense submission requires a Kolo user ID or sender ID.",
            )
        matches = _profiles_for_sender(gateway, sender_id, org_id)
        if len(matches) > 1:
            raise ExpenseFlowError(
                "ambiguous_sender_identity",
                "Sender ID is linked to more than one ExpenseFlow profile.",
                details={"sender_id": sender_id},
            )
        if not matches:
            return _hold_for_identity_mapping(gateway, expense_data, settings, sender_id, org_id, expense_id)
        submitter_user_id = matches[0]["user_id"]
    submitter = _optional_payload(gateway, USER_PROFILE, submitter_user_id)
    discovered = False
    if submitter is None:
        peer = _find_peer(gateway.list_peers(), submitter_user_id, org_id)
        if peer is None:
            _notify_admins(
                gateway,
                settings,
                f"ExpenseFlow blocked an expense from unverified Kolo user {submitter_user_id}.",
                excluded_user_id=submitter_user_id,
            )
            gateway.log_action(
                "skill.user_profile",
                "Unverified expense submitter blocked",
                f"expenseflow:unverified-submitter:{org_id}:{submitter_user_id}:{expense_id or 'unknown'}",
                {"org_id": org_id, "submitter_user_id": submitter_user_id},
            )
            raise ExpenseFlowError(
                "unverified_submitter",
                "Submitter is not a current member of this Kolo organization.",
                details={"submitter_user_id": submitter_user_id},
            )
        submitter = create_discovered_profile(
            peer,
            org_id,
            sender_id=sender_id,
            status="pending_admin_approval",
        )
        upsert_user_profile(gateway, submitter)
        discovered = True

    expense = capture_expense(
        gateway,
        expense_data,
        submitter_user_id,
        org_id=org_id,
        expense_id=expense_id,
    )
    queue_ids = []
    if discovered:
        queue_ids = _notify_admins(
            gateway,
            settings,
            (
                f"ExpenseFlow onboarding review needed for Kolo user {submitter_user_id}. "
                f"Expense {expense['expense_id']} is held pending verification."
            ),
            excluded_user_id=submitter_user_id,
        )
    return {
        "status": "held_pending_onboarding" if discovered else expense["status"],
        "expense": expense,
        "user_profile": submitter,
        "onboarding_required": discovered,
        "admin_notification_queue_ids": queue_ids,
    }


def map_sender_identity(gateway, sender_id, user_id, admin_user_id, org_id="default"):
    settings = _optional_payload(gateway, EXPENSE_SETTINGS, org_id, default={})
    if _normalize_user_id(admin_user_id) not in _admin_user_ids(settings):
        raise ExpenseFlowError("unauthorized_admin", "Only a configured ExpenseFlow admin can map senders.")
    peer = _find_peer(gateway.list_peers(), user_id, org_id)
    if peer is None:
        raise ExpenseFlowError(
            "unverified_submitter",
            "Mapped user is not a current member of this Kolo organization.",
            details={"user_id": user_id},
        )

    existing_sender_matches = _profiles_for_sender(gateway, sender_id, org_id)
    if any(_normalize_user_id(profile.get("user_id")) != _normalize_user_id(user_id) for profile in existing_sender_matches):
        raise ExpenseFlowError("sender_identity_conflict", "Sender ID is already linked to another user.")

    profile = _optional_payload(gateway, USER_PROFILE, user_id)
    if profile is None:
        profile = create_discovered_profile(peer, org_id, sender_id=sender_id, status="pending_admin_approval")
    else:
        _require_record_org(profile, org_id, "employee")
        if profile.get("status") in {"suspended", "deactivated", "rejected"}:
            raise ExpenseFlowError(
                "inactive_identity_target",
                "Cannot map an expense sender to an inactive employee profile.",
                details={"status": profile.get("status")},
            )
        if profile.get("sender_id") not in {None, "", sender_id}:
            raise ExpenseFlowError("sender_identity_conflict", "User profile is already linked to another sender ID.")
        profile = dict(profile)
        profile["sender_id"] = sender_id
    upsert_user_profile(gateway, profile)

    mapped_expense_ids = []
    released_expense_ids = []
    for record in gateway.list_records(IDENTITY_DISCOVERY, status="pending_admin_mapping"):
        discovery = _payload(record)
        if discovery.get("sender_id") != sender_id or str(discovery.get("org_id")) != str(org_id):
            continue
        expense = _payload(gateway.get_record(EXPENSE, discovery["expense_id"]))
        expense = dict(expense)
        expense["submitter_user_id"] = profile["user_id"]
        expense["submitter_name"] = profile.get("display_name")
        if profile.get("status") == "active":
            expense = _transition_expense(expense, "draft")
            expense["released_from_identity_mapping_at"] = utc_now()
            released_expense_ids.append(expense["expense_id"])
        gateway.upsert_record(EXPENSE, expense["expense_id"], expense, expense["status"])
        discovery = dict(discovery)
        discovery.update(
            {
                "status": "mapped",
                "mapped_user_id": profile["user_id"],
                "mapped_by_user_id": admin_user_id,
                "mapped_at": utc_now(),
            }
        )
        gateway.upsert_record(IDENTITY_DISCOVERY, record["external_id"], discovery, "mapped")
        mapped_expense_ids.append(expense["expense_id"])

    gateway.log_action(
        "skill.identity_discovery",
        "ExpenseFlow sender identity mapped",
        f"expenseflow:identity-mapped:{org_id}:{sender_id}:{user_id}",
        {
            "org_id": org_id,
            "sender_id": sender_id,
            "user_id": user_id,
            "admin_user_id": admin_user_id,
            "mapped_expense_ids": mapped_expense_ids,
        },
    )
    return {
        "user_profile": profile,
        "mapped_expense_ids": mapped_expense_ids,
        "released_expense_ids": released_expense_ids,
        "onboarding_required": profile.get("status") != "active",
    }


def approve_user_onboarding(gateway, user_id, admin_user_id, approver_user_id, org_id="default"):
    settings = _optional_payload(gateway, EXPENSE_SETTINGS, org_id, default={})
    if _normalize_user_id(admin_user_id) not in _admin_user_ids(settings):
        raise ExpenseFlowError("unauthorized_admin", "Only a configured ExpenseFlow admin can approve onboarding.")
    profile = _payload(gateway.get_record(USER_PROFILE, user_id))
    approver = _payload(gateway.get_record(USER_PROFILE, approver_user_id))
    _require_record_org(profile, org_id, "employee")
    _require_record_org(approver, org_id, "approver")
    policy = _optional_payload(gateway, APPROVAL_POLICY, org_id, default={})
    policy_version = policy.get("version", policy.get("schema_version", 1))
    updated = approve_onboarding(profile, approver, admin_user_id, policy_version)
    upsert_user_profile(gateway, updated)
    message = gateway.contact_agent(
        user_id,
        f"ExpenseFlow access was approved. Please acknowledge expense policy version {policy_version}.",
    )
    gateway.log_action(
        "skill.user_profile",
        "ExpenseFlow onboarding approved",
        f"expenseflow:onboarding-approved:{user_id}:{policy_version}",
        {
            "user_id": user_id,
            "admin_user_id": admin_user_id,
            "approver_user_id": approver_user_id,
            "policy_version": policy_version,
        },
    )
    return {"user_profile": updated, "policy_message_queue_id": message.get("queueId")}


def acknowledge_expense_policy(gateway, user_id, acknowledging_user_id, policy_version):
    profile = _payload(gateway.get_record(USER_PROFILE, user_id))
    updated = acknowledge_policy(profile, acknowledging_user_id, policy_version)
    upsert_user_profile(gateway, updated)
    released_expense_ids = []
    for record in gateway.list_records(EXPENSE, status="held_pending_onboarding"):
        expense = _payload(record)
        if expense.get("submitter_user_id") != user_id:
            continue
        released = _transition_expense(expense, "draft")
        released["released_from_onboarding_at"] = utc_now()
        gateway.upsert_record(EXPENSE, released["expense_id"], released, "draft")
        released_expense_ids.append(released["expense_id"])
    gateway.log_action(
        "skill.user_profile",
        "Expense policy acknowledged",
        f"expenseflow:policy-ack:{user_id}:{policy_version}",
        {"user_id": user_id, "policy_version": policy_version, "released_expense_ids": released_expense_ids},
    )
    return {"user_profile": updated, "released_expense_ids": released_expense_ids}


def capture_expense(gateway, expense_data, submitter_user_id, org_id="default", expense_id=None):
    submitter = _payload(gateway.get_record(USER_PROFILE, submitter_user_id))
    _require_record_org(submitter, org_id, "submitter")
    settings = _optional_payload(gateway, EXPENSE_SETTINGS, org_id, default={})
    candidate = create_expense(expense_data, submitter, settings, expense_id)
    candidate["org_id"] = org_id
    return _persist_expense(gateway, candidate, org_id, submitter_user_id)


def _persist_expense(gateway, candidate, org_id, submitter_user_id):
    existing = [
        expense
        for expense in (_payload(record) for record in gateway.list_records(EXPENSE))
        if str(expense.get("org_id", org_id)) == str(org_id)
    ]
    candidate["duplicate_candidates"] = detect_duplicates(candidate, existing)
    gateway.upsert_record(EXPENSE, candidate["expense_id"], candidate, candidate["status"])
    gateway.log_action(
        "skill.expense",
        "Expense captured",
        f"expenseflow:capture:{candidate['expense_id']}",
        {"expense_id": candidate["expense_id"], "submitter_user_id": submitter_user_id},
    )
    return candidate


def submit_report_for_approval(
    gateway,
    submitter_user_id,
    expense_ids,
    org_id="default",
    title=None,
    period=None,
    report_id=None,
    approval_request_id=None,
):
    submitter = _payload(gateway.get_record(USER_PROFILE, submitter_user_id))
    _require_record_org(submitter, org_id, "submitter")
    expenses = [_payload(gateway.get_record(EXPENSE, expense_id)) for expense_id in expense_ids]
    for expense in expenses:
        _require_record_org(expense, org_id, "expense")
    report = create_report(expenses, submitter, title=title, period=period, report_id=report_id)
    report["org_id"] = org_id
    policies = _load_policies(gateway, org_id)
    user_profiles = [
        profile
        for profile in (_payload(record) for record in gateway.list_records(USER_PROFILE))
        if str(profile.get("org_id", org_id)) == str(org_id)
    ]
    approval = create_approval_request(report, submitter, policies, user_profiles, approval_request_id)

    if approval["status"] == "held_pending_manager":
        gateway.upsert_record(EXPENSE_REPORT, approval["report"]["report_id"], approval["report"], "held_pending_manager")
        for expense in expenses:
            held = _transition_expense(expense, "held_pending_manager")
            gateway.upsert_record(EXPENSE, held["expense_id"], held, held["status"])
        gateway.log_action(
            "skill.expense_report",
            "Expense report held pending manager",
            f"expenseflow:report-held:{approval['report']['report_id']}",
            {"report_id": approval["report"]["report_id"], "submitter_user_id": submitter_user_id},
        )
        return approval

    approval_request = approval["approval_request"]
    snapshot = _create_approver_snapshot(gateway, approval["report"], approval_request, policies)
    approval_request["approver_snapshot_id"] = snapshot["snapshot_id"]
    message_result = gateway.contact_agent(
        approval_request["approver_user_id"],
        _approval_message(approval["report"], submitter),
    )
    task = gateway.create_task(
        f"Review expense report {approval['report']['report_id']}",
        approval_request["approver_user_id"],
        {"report_id": approval["report"]["report_id"]},
    )
    approval_request["backchannel_queue_id"] = message_result.get("queueId")
    approval_request["task_id"] = task.get("task_id")

    gateway.upsert_record(EXPENSE_REPORT, approval["report"]["report_id"], approval["report"], "pending_approval")
    gateway.upsert_record(APPROVAL_REQUEST, approval_request["approval_request_id"], approval_request, "pending")
    for expense in expenses:
        submitted = _transition_expense(expense, "submitted")
        submitted["report_id"] = approval["report"]["report_id"]
        gateway.upsert_record(EXPENSE, submitted["expense_id"], submitted, submitted["status"])
    gateway.log_action(
        "skill.expense_report",
        "Expense report submitted",
        f"expenseflow:submit:{approval['report']['report_id']}",
        {
            "report_id": approval["report"]["report_id"],
            "approval_request_id": approval_request["approval_request_id"],
        },
    )
    return {**approval, "approval_request": approval_request}


def decide_report_approval(gateway, approval_request_id, approver_user_id, decision, note=None, decision_id=None):
    approval_request = _payload(gateway.get_record(APPROVAL_REQUEST, approval_request_id))
    report = _payload(gateway.get_record(EXPENSE_REPORT, approval_request["report_id"]))
    expenses = [_payload(gateway.get_record(EXPENSE, expense_id)) for expense_id in report.get("expense_ids", [])]
    result = record_approval_decision(
        report,
        expenses,
        approval_request,
        approver_user_id,
        decision,
        note=note,
        decision_id=decision_id,
    )
    gateway.upsert_record(EXPENSE_REPORT, result["report"]["report_id"], result["report"], result["report"]["status"])
    gateway.upsert_record(APPROVAL_REQUEST, approval_request_id, result["approval_request"], result["approval_request"]["status"])
    gateway.upsert_record(
        APPROVAL_DECISION,
        result["approval_decision"]["approval_decision_id"],
        result["approval_decision"],
        result["approval_decision"]["decision"],
    )
    for expense in result["expenses"]:
        gateway.upsert_record(EXPENSE, expense["expense_id"], expense, expense["status"])
    gateway.log_action(
        "skill.approval_decision",
        "Expense report approval decided",
        f"expenseflow:decision:{result['approval_decision']['approval_decision_id']}",
        {
            "report_id": result["report"]["report_id"],
            "approval_request_id": approval_request_id,
            "decision": decision,
        },
    )
    return result


def export_approved_report_csv(gateway, report_id):
    report = _payload(gateway.get_record(EXPENSE_REPORT, report_id))
    expenses = [_payload(gateway.get_record(EXPENSE, expense_id)) for expense_id in report.get("expense_ids", [])]
    csv_content = generate_report_csv(report, expenses)
    exported_report = transition_report(report, "exported")
    gateway.upsert_record(EXPENSE_REPORT, report_id, exported_report, "exported")
    for expense in expenses:
        exported_expense = _transition_expense(expense, "exported")
        gateway.upsert_record(EXPENSE, exported_expense["expense_id"], exported_expense, "exported")
    gateway.log_action(
        "skill.expense_report",
        "Expense report exported to CSV",
        f"expenseflow:export-csv:{report_id}",
        {"report_id": report_id},
    )
    return {"status": "ok", "report": exported_report, "csv": csv_content}


def _load_policies(gateway, org_id):
    approval_policy = _optional_payload(gateway, APPROVAL_POLICY, org_id, default={})
    department_policies = {}
    for record in gateway.list_records(DEPARTMENT_POLICY, status="active"):
        policy = _payload(record)
        if str(policy.get("org_id", org_id)) == str(org_id):
            department_policies[policy.get("department", record["external_id"])] = policy
    approval_delegations = [
        delegation
        for delegation in (_payload(record) for record in gateway.list_records(APPROVAL_DELEGATION, status="active"))
        if str(delegation.get("org_id", org_id)) == str(org_id)
    ]
    return {
        "approval_policy": approval_policy,
        "department_policies": department_policies,
        "approval_delegations": approval_delegations,
    }


def _create_approver_snapshot(gateway, report, approval_request, policies):
    snapshot_id = report["report_id"]
    snapshot = {
        "snapshot_id": snapshot_id,
        "report_id": report["report_id"],
        "approval_request_id": approval_request["approval_request_id"],
        "approver_user_id": approval_request["approver_user_id"],
        "delegated_from_user_id": approval_request.get("delegated_from_user_id"),
        "routing_reason": approval_request["routing_reason"],
        "policy_version": (policies.get("approval_policy") or {}).get("version", 1),
        "snapshot_at": report.get("submitted_at") or utc_now(),
        "schema_version": 1,
    }
    existing = _optional_payload(gateway, APPROVER_SNAPSHOT, snapshot_id)
    if existing is not None:
        comparable_fields = {
            "report_id",
            "approval_request_id",
            "approver_user_id",
            "delegated_from_user_id",
            "routing_reason",
            "policy_version",
        }
        if any(existing.get(field) != snapshot.get(field) for field in comparable_fields):
            raise ExpenseFlowError(
                "immutable_snapshot_conflict",
                "Approver snapshot already exists with different routing data.",
                details={"snapshot_id": snapshot_id},
            )
        return existing
    gateway.upsert_record(APPROVER_SNAPSHOT, snapshot_id, snapshot, "active")
    return snapshot


def _transition_expense(expense, new_status):
    validate_transition("expense", expense.get("status"), new_status)
    updated = dict(expense)
    updated["status"] = new_status
    return updated


def _payload(record):
    return record.get("payload", record)


def _optional_payload(gateway, record_type, external_id, default=None):
    try:
        return _payload(gateway.get_record(record_type, external_id))
    except ExpenseFlowError as exc:
        if exc.code != "record_not_found":
            raise
        return default


def _peer_in_org(peer, org_id):
    peer_org_id = peer.get("org_id", peer.get("orgId"))
    return peer_org_id is None or str(peer_org_id) == str(org_id)


def _find_peer(peers, user_id, org_id):
    for peer in peers:
        peer_user_id = peer.get("user_id", peer.get("userId"))
        if str(peer_user_id) == str(user_id) and _peer_in_org(peer, org_id):
            normalized = dict(peer)
            normalized["user_id"] = user_id
            return normalized
    return None


def _admin_user_ids(settings):
    values = settings.get("expense_admin_user_ids") or []
    if not isinstance(values, list):
        values = [values]
    if settings.get("expense_admin_user_id") is not None:
        values = [*values, settings["expense_admin_user_id"]]
    return list(dict.fromkeys(_normalize_user_id(value) for value in values))


def _notify_admins(gateway, settings, message, excluded_user_id=None):
    queue_ids = []
    for admin_user_id in _admin_user_ids(settings):
        if str(admin_user_id) == str(excluded_user_id):
            continue
        result = gateway.contact_agent(admin_user_id, message)
        if result.get("queueId"):
            queue_ids.append(result["queueId"])
    return queue_ids


def _profiles_for_sender(gateway, sender_id, org_id):
    return [
        profile
        for profile in (_payload(record) for record in gateway.list_records(USER_PROFILE))
        if profile.get("sender_id") == sender_id and str(profile.get("org_id", org_id)) == str(org_id)
    ]


def _hold_for_identity_mapping(gateway, expense_data, settings, sender_id, org_id, expense_id):
    pending_submitter = {
        "user_id": None,
        "display_name": "Pending identity mapping",
        "status": "pending_admin_approval",
    }
    expense = create_expense(expense_data, pending_submitter, settings, expense_id)
    expense["org_id"] = org_id
    expense["sender_id"] = sender_id
    _persist_expense(gateway, expense, org_id, None)
    discovery = {
        "identity_discovery_id": expense["expense_id"],
        "org_id": org_id,
        "sender_id": sender_id,
        "expense_id": expense["expense_id"],
        "status": "pending_admin_mapping",
        "created_at": utc_now(),
        "schema_version": 1,
    }
    gateway.upsert_record(
        IDENTITY_DISCOVERY,
        discovery["identity_discovery_id"],
        discovery,
        discovery["status"],
    )
    queue_ids = _notify_admins(
        gateway,
        settings,
        (
            f"ExpenseFlow needs identity mapping for sender {sender_id}. "
            f"Expense {expense['expense_id']} is held; map the sender to a current Kolo user ID."
        ),
    )
    return {
        "status": "pending_identity_mapping",
        "expense": expense,
        "user_profile": None,
        "onboarding_required": True,
        "identity_mapping_required": True,
        "admin_notification_queue_ids": queue_ids,
    }


def _normalize_user_id(user_id):
    if user_id is None:
        return None
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return user_id


def _require_record_org(payload, org_id, entity):
    record_org_id = payload.get("org_id")
    if record_org_id is not None and str(record_org_id) != str(org_id):
        raise ExpenseFlowError(
            "organization_mismatch",
            f"{entity.capitalize()} does not belong to this ExpenseFlow organization.",
            details={"expected_org_id": str(org_id), "actual_org_id": str(record_org_id)},
        )


def _approval_message(report, submitter):
    totals = ", ".join(f"{currency} {amount}" for currency, amount in report.get("totals_by_currency", {}).items())
    return (
        f"ExpenseFlow approval needed: report {report.get('report_id')} "
        f"from {submitter.get('display_name')} totaling {totals or '0.00'}."
    )
