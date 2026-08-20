import hashlib
import mimetypes
from pathlib import Path

from .approval_engine import create_approval_request, record_approval_decision
from .csv_export import generate_report_csv
from .errors import ExpenseFlowError
from .expense_core import create_expense, detect_duplicates
from .models import utc_now
from .onboarding_engine import acknowledge_policy, approve_onboarding, create_delegation, create_discovered_profile
from .receipt_engine import ALLOWED_RECEIPT_CONTENT_TYPES, normalize_receipt_attachment
from .qbo_export import (
    QBO_PAYMENT_TYPES,
    QBO_TRANSACTION_TYPES,
    build_qbo_transaction,
    extract_qbo_entity,
    normalize_qbo_reference_cache,
)
from .reminder_engine import (
    advance_reminder_schedule,
    format_utc,
    initialize_reminder_schedule,
    is_reminder_due,
    normalize_reminder_settings,
    parse_utc,
)
from .report_engine import create_report, transition_report
from .sheets_export import (
    SHEET_COLUMNS,
    build_sheet_row,
    content_hash,
    data_range,
    header_range,
    index_rows_by_id,
    normalized_values,
    parse_updated_range,
    row_range,
)
from .setup_readiness import evaluate_setup_readiness
from .status import validate_transition


EXPENSE_SETTINGS = "skill.expense_settings"
ACCOUNTING_DESTINATION = "skill.accounting_destination"
USER_PROFILE = "skill.user_profile"
APPROVAL_POLICY = "skill.approval_policy"
DEPARTMENT_POLICY = "skill.department_policy"
EXPENSE = "skill.expense"
RECEIPT = "skill.receipt"
EXPENSE_REPORT = "skill.expense_report"
APPROVAL_REQUEST = "skill.approval_request"
APPROVAL_DECISION = "skill.approval_decision"
APPROVAL_DECISION_CLAIM = "skill.approval_decision_claim"
APPROVAL_DELEGATION = "skill.approval_delegation"
APPROVER_SNAPSHOT = "skill.approver_snapshot"
IDENTITY_DISCOVERY = "skill.identity_discovery"
NOTIFICATION_EVENT = "skill.notification_event"
TASK_EVENT = "skill.task_event"
EXPORT_RUN = "skill.export_run"
EXPORT_ITEM = "skill.export_item"
ACCOUNTING_REFERENCE_CACHE = "skill.accounting_reference_cache"


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
    normalize_reminder_settings(settings)
    if settings.get("max_receipt_bytes") is not None:
        try:
            max_receipt_bytes = int(settings["max_receipt_bytes"])
        except (TypeError, ValueError):
            raise ExpenseFlowError("invalid_max_receipt_bytes", "max_receipt_bytes must be a positive integer.")
        if max_receipt_bytes <= 0:
            raise ExpenseFlowError("invalid_max_receipt_bytes", "max_receipt_bytes must be a positive integer.")
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
    config = dict(destination.get("config") or {})
    if destination_type == "csv":
        delivery_method = config.get("delivery_method", "message")
        if delivery_method not in {"message", "drive", "gmail"}:
            raise ExpenseFlowError("invalid_delivery_method", "CSV delivery must use message, drive, or gmail.")
    elif destination_type == "sheets":
        if not config.get("spreadsheet_id"):
            raise ExpenseFlowError("missing_spreadsheet_id", "Google Sheets destination requires spreadsheet_id.")
        config["sheet_name"] = str(config.get("sheet_name") or "ExpenseFlow").strip()
        if not config["sheet_name"]:
            raise ExpenseFlowError("missing_sheet_name", "Google Sheets destination requires sheet_name.")
        fallback_to_csv = config.get("fallback_to_csv", False)
        if not isinstance(fallback_to_csv, bool):
            raise ExpenseFlowError(
                "invalid_csv_fallback",
                "Google Sheets fallback_to_csv must be true or false.",
            )
        config["fallback_to_csv"] = fallback_to_csv
    elif destination_type == "qbo":
        config = _normalize_qbo_destination_config(config)
    payload = {
        "org_id": org_id,
        "destination_type": destination_type,
        "config": config,
        "status": destination.get("status", "active"),
        "schema_version": 1,
    }
    return payload


def _normalize_qbo_destination_config(config):
    config = dict(config)
    realm_id = str(config.get("realm_id") or config.get("company_id") or "").strip()
    if not realm_id:
        raise ExpenseFlowError("missing_qbo_realm_id", "QuickBooks destination requires realm_id.")
    if not realm_id.isdigit():
        raise ExpenseFlowError(
            "invalid_qbo_realm_id",
            "QuickBooks realm_id must contain digits only.",
        )
    transaction_type = str(config.get("transaction_type") or "").strip().lower()
    if transaction_type not in QBO_TRANSACTION_TYPES:
        raise ExpenseFlowError(
            "invalid_qbo_transaction_type",
            "QuickBooks transaction_type must be purchase, bill, or journalentry.",
        )
    category_account_ids = config.get("category_account_ids")
    if not isinstance(category_account_ids, dict) or not category_account_ids:
        raise ExpenseFlowError(
            "missing_qbo_category_mappings",
            "QuickBooks destination requires category_account_ids.",
        )
    normalized_mappings = {}
    for category, account_id in category_account_ids.items():
        category = str(category).strip()
        account_id = str(account_id).strip()
        if not category or not account_id:
            raise ExpenseFlowError(
                "invalid_qbo_category_mapping",
                "QuickBooks category mappings require non-empty category and account IDs.",
            )
        normalized_mappings[category] = account_id
    if transaction_type in {"purchase", "journalentry"} and not config.get("balancing_account_id"):
        raise ExpenseFlowError(
            "missing_qbo_account_mapping",
            "QuickBooks purchase and journalentry destinations require balancing_account_id.",
        )
    if transaction_type == "purchase":
        payment_type = str(config.get("payment_type") or "Cash")
        if payment_type not in QBO_PAYMENT_TYPES:
            raise ExpenseFlowError(
                "invalid_qbo_payment_type",
                "QuickBooks purchase payment_type must be Cash, Check, or CreditCard.",
            )
        config["payment_type"] = payment_type
    employee_vendor_ids = config.get("employee_vendor_ids") or {}
    if not isinstance(employee_vendor_ids, dict):
        raise ExpenseFlowError(
            "invalid_qbo_employee_vendor_mappings",
            "QuickBooks employee_vendor_ids must be an object keyed by Kolo user ID.",
        )
    config.pop("company_id", None)
    config["realm_id"] = realm_id
    config["transaction_type"] = transaction_type
    config["category_account_ids"] = normalized_mappings
    config["employee_vendor_ids"] = {
        str(user_id): str(vendor_id)
        for user_id, vendor_id in employee_vendor_ids.items()
        if str(user_id).strip() and str(vendor_id).strip()
    }
    try:
        max_execution_checks = int(config.get("max_execution_checks", 12))
    except (TypeError, ValueError):
        raise ExpenseFlowError(
            "invalid_qbo_execution_checks",
            "QuickBooks max_execution_checks must be an integer from 1 to 100.",
        )
    if not 1 <= max_execution_checks <= 100:
        raise ExpenseFlowError(
            "invalid_qbo_execution_checks",
            "QuickBooks max_execution_checks must be an integer from 1 to 100.",
        )
    config["max_execution_checks"] = max_execution_checks
    for field in (
        "balancing_account_id",
        "accounts_payable_account_id",
        "default_employee_vendor_id",
        "default_class_id",
        "default_tax_code_id",
        "department_id",
    ):
        if config.get(field) is not None:
            config[field] = str(config[field]).strip()
    return config


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


def organization_setup_readiness(gateway, org_id="default", verify_destination=True):
    settings = _optional_payload(gateway, EXPENSE_SETTINGS, org_id, default={})
    approval_policy = _optional_payload(gateway, APPROVAL_POLICY, org_id, default={})
    destination = _optional_payload(gateway, ACCOUNTING_DESTINATION, org_id, default={})
    profiles = [
        profile
        for profile in (_payload(record) for record in gateway.list_records(USER_PROFILE))
        if str(profile.get("org_id", org_id)) == str(org_id)
    ]
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
    try:
        peers = [peer for peer in gateway.list_peers() if _peer_in_org(peer, org_id)]
        directory_error = None
    except ExpenseFlowError as exc:
        peers = []
        directory_error = exc.code
    return evaluate_setup_readiness(
        org_id,
        settings=settings,
        approval_policy=approval_policy,
        destination=destination,
        profiles=profiles,
        department_policies=department_policies,
        approval_delegations=approval_delegations,
        peers=peers,
        destination_health=_check_destination_health(gateway, destination, verify_destination),
        directory_error=directory_error,
    )


def _check_destination_health(gateway, destination, verify_destination):
    if not verify_destination or destination.get("status") != "active":
        return None
    destination_type = destination.get("destination_type")
    config = destination.get("config") or {}
    if destination_type == "csv":
        return None
    try:
        if destination_type == "sheets":
            metadata = gateway.sheets_get_metadata(config.get("spreadsheet_id"))
            _, sheet_name = _resolve_sheet(metadata, config)
            response = gateway.sheets_read_values(config.get("spreadsheet_id"), header_range(sheet_name))
            rows = normalized_values(response, len(SHEET_COLUMNS))
            if rows and any(rows[0]) and rows[0] != SHEET_COLUMNS:
                raise ExpenseFlowError(
                    "sheets_header_mismatch",
                    "The configured sheet does not have the ExpenseFlow column layout.",
                )
            return {"status": "pass"}
        if destination_type == "qbo":
            _require_qbo_connection(gateway, config.get("realm_id"))
            return {"status": "pass"}
        return {
            "status": "error",
            "error_code": "invalid_accounting_destination",
            "next_action": "Choose CSV, Google Sheets, or QuickBooks Online.",
        }
    except ExpenseFlowError as exc:
        return {
            "status": "error",
            "error_code": exc.code,
            "next_action": exc.message,
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
    if submitter is None:
        peer = _find_peer(gateway.list_peers(), submitter_user_id, org_id)
        if peer is None:
            _notify_admins(
                gateway,
                settings,
                _with_message_prefix(
                    settings,
                    f"ExpenseFlow blocked an expense from unverified Kolo user {submitter_user_id}.",
                ),
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
    elif submitter.get("status") == "discovered":
        submitter = dict(submitter)
        submitter["status"] = "pending_admin_approval"
        if sender_id and not submitter.get("sender_id"):
            submitter["sender_id"] = sender_id
        upsert_user_profile(gateway, submitter)

    expense = capture_expense(
        gateway,
        expense_data,
        submitter_user_id,
        org_id=org_id,
        expense_id=expense_id,
    )
    queue_ids = []
    needs_admin_review = submitter.get("status") == "pending_admin_approval"
    if needs_admin_review:
        queue_ids = _notify_admins(
            gateway,
            settings,
            _with_message_prefix(
                settings,
                (
                    f"ExpenseFlow onboarding review needed for Kolo user {submitter_user_id}. "
                    f"Expense {expense['expense_id']} is held pending verification."
                ),
            ),
            excluded_user_id=submitter_user_id,
        )
    return {
        "status": expense["status"],
        "expense": expense,
        "user_profile": submitter,
        "onboarding_required": expense["status"] == "held_pending_onboarding",
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
        _with_message_prefix(
            settings,
            f"ExpenseFlow access was approved. Please acknowledge expense policy version {policy_version}.",
        ),
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
    expense_data, receipt_attachment = _prepare_expense_receipt(expense_data, settings)
    candidate = create_expense(expense_data, submitter, settings, expense_id)
    if receipt_attachment:
        candidate["receipt_attachments"] = [receipt_attachment]
    candidate["org_id"] = org_id
    return _persist_expense(gateway, candidate, org_id, submitter_user_id)


def attach_receipt_reference(gateway, expense_id, attachment, acting_user_id, org_id="default"):
    expense = _payload(gateway.get_record(EXPENSE, expense_id))
    _require_record_org(expense, org_id, "expense")
    settings = _optional_payload(gateway, EXPENSE_SETTINGS, org_id, default={})
    _require_receipt_actor(expense, settings, acting_user_id)
    _require_receipt_editable(expense)
    receipt = normalize_receipt_attachment(attachment, settings)

    existing = list(expense.get("receipt_attachments") or [])
    for saved in existing:
        if saved.get("attachment_id") == receipt["attachment_id"]:
            return {"status": "already_attached", "expense": expense, "receipt": saved}

    receipt = _store_receipt_record(gateway, expense_id, org_id, receipt)
    updated = dict(expense)
    updated["receipt_attachments"] = [*existing, receipt]
    updated["receipt_ref"] = updated.get("receipt_ref") or receipt["object_store_object_id"]
    updated["receipt_url"] = updated.get("receipt_url") or receipt["reference"]
    updated["receipt_updated_at"] = utc_now()
    gateway.upsert_record(EXPENSE, expense_id, updated, updated["status"])
    gateway.log_action(
        "skill.expense",
        "Receipt attached to expense",
        f"expenseflow:receipt:{expense_id}:{receipt['attachment_id']}",
        {
            "expense_id": expense_id,
            "attachment_id": receipt["attachment_id"],
            "acting_user_id": _normalize_user_id(acting_user_id),
        },
    )
    return {"status": "attached", "expense": updated, "receipt": receipt}


def upload_and_attach_receipt(gateway, expense_id, file_path, acting_user_id, org_id="default", metadata=None):
    expense = _payload(gateway.get_record(EXPENSE, expense_id))
    _require_record_org(expense, org_id, "expense")
    settings = _optional_payload(gateway, EXPENSE_SETTINGS, org_id, default={})
    _require_receipt_actor(expense, settings, acting_user_id)
    _require_receipt_editable(expense)

    path = Path(file_path).resolve()
    if not path.is_file():
        raise ExpenseFlowError("receipt_file_not_found", "Receipt file is not available at the supplied local path.")
    if not _is_inbound_receipt_path(path):
        raise ExpenseFlowError(
            "receipt_path_not_allowed",
            "Receipt uploads must come from Kolo's media/inbound staging directory.",
        )
    size_bytes = path.stat().st_size
    max_bytes = settings.get("max_receipt_bytes")
    if max_bytes is not None and size_bytes > int(max_bytes):
        raise ExpenseFlowError(
            "receipt_too_large",
            "Receipt exceeds the organization's configured size limit.",
            details={"size_bytes": size_bytes, "max_receipt_bytes": int(max_bytes)},
        )
    upload_metadata = metadata or {}
    content_type = str(
        upload_metadata.get("content_type") or mimetypes.guess_type(path.name)[0] or ""
    ).lower()
    if content_type not in ALLOWED_RECEIPT_CONTENT_TYPES:
        raise ExpenseFlowError(
            "unsupported_receipt_type",
            "Receipt must be a PDF or supported image type.",
            details={"content_type": content_type or None},
        )
    sha256 = _file_sha256(path)
    for saved in expense.get("receipt_attachments") or []:
        if saved.get("sha256") == sha256:
            return {"status": "already_attached", "expense": expense, "receipt": saved}

    receipt_id = _receipt_external_id(expense_id, sha256)
    existing_receipt = _optional_payload(gateway, RECEIPT, receipt_id)
    if existing_receipt is not None:
        if existing_receipt.get("status") == "stored" and existing_receipt.get("attachment"):
            return attach_receipt_reference(
                gateway,
                expense_id,
                existing_receipt["attachment"],
                acting_user_id,
                org_id,
            )
        if existing_receipt.get("status") in {"uploaded", "upload_invalid"} and existing_receipt.get("upload"):
            receipt = _finalize_receipt_upload(gateway, receipt_id, existing_receipt, settings)
            return attach_receipt_reference(gateway, expense_id, receipt, acting_user_id, org_id)
        raise ExpenseFlowError(
            "receipt_upload_incomplete",
            "A prior upload attempt for this receipt did not finish cleanly; review the reserved receipt record before retrying.",
            details={"receipt_id": receipt_id, "status": existing_receipt.get("status")},
        )

    reservation = {
        "receipt_id": receipt_id,
        "expense_id": expense_id,
        "org_id": org_id,
        "status": "uploading",
        "sha256": sha256,
        "filename": Path(str(upload_metadata.get("filename") or path.name)).name,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "reserved_at": utc_now(),
        "schema_version": 1,
    }
    reserved = gateway.upsert_record(RECEIPT, receipt_id, reservation, "uploading")
    if not reserved.get("created", False):
        raise ExpenseFlowError(
            "receipt_upload_incomplete",
            "Another receipt upload already reserved this content hash.",
            details={"receipt_id": receipt_id},
        )

    try:
        uploaded = gateway.upload_file(str(path))
    except ExpenseFlowError as exc:
        reservation["status"] = "upload_unknown"
        reservation["upload_error"] = exc.code
        reservation["completed_at"] = utc_now()
        gateway.upsert_record(RECEIPT, receipt_id, reservation, reservation["status"])
        raise
    reservation["status"] = "uploaded"
    reservation["upload"] = {
        "object_store_object_id": uploaded["object_store_object_id"],
        "reference": uploaded["reference"],
    }
    reservation["uploaded_at"] = utc_now()
    gateway.upsert_record(RECEIPT, receipt_id, reservation, "uploaded")
    receipt = _finalize_receipt_upload(gateway, receipt_id, reservation, settings)
    return attach_receipt_reference(gateway, expense_id, receipt, acting_user_id, org_id)


def _finalize_receipt_upload(gateway, receipt_id, reservation, settings):
    receipt_data = {
        **reservation["upload"],
        "filename": reservation["filename"],
        "content_type": reservation["content_type"],
        "size_bytes": reservation["size_bytes"],
        "sha256": reservation["sha256"],
    }
    try:
        receipt = normalize_receipt_attachment(receipt_data, settings)
    except ExpenseFlowError as exc:
        reservation["status"] = "upload_invalid"
        reservation["validation_error"] = exc.code
        reservation["completed_at"] = utc_now()
        gateway.upsert_record(RECEIPT, receipt_id, reservation, reservation["status"])
        raise
    reservation["status"] = "stored"
    reservation["attachment"] = receipt
    reservation.pop("validation_error", None)
    reservation["completed_at"] = utc_now()
    gateway.upsert_record(RECEIPT, receipt_id, reservation, "stored")
    return receipt


def _persist_expense(gateway, candidate, org_id, submitter_user_id):
    existing = [
        expense
        for expense in (_payload(record) for record in gateway.list_records(EXPENSE))
        if str(expense.get("org_id", org_id)) == str(org_id)
    ]
    candidate["duplicate_candidates"] = detect_duplicates(candidate, existing)
    for receipt in candidate.get("receipt_attachments") or []:
        _store_receipt_record(gateway, candidate["expense_id"], org_id, receipt)
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
    submitter_user_id = _normalize_user_id(submitter_user_id)
    expense_ids = list(dict.fromkeys(str(expense_id) for expense_id in expense_ids))
    report_id, approval_request_id = _submission_ids(
        org_id,
        submitter_user_id,
        expense_ids,
        report_id,
        approval_request_id,
    )
    submitter = _payload(gateway.get_record(USER_PROFILE, submitter_user_id))
    _require_record_org(submitter, org_id, "submitter")
    existing_report = _optional_payload(gateway, EXPENSE_REPORT, report_id)
    if existing_report is not None:
        return _resume_report_submission(
            gateway,
            existing_report,
            submitter,
            expense_ids,
            approval_request_id,
            org_id,
        )

    expenses = [_payload(gateway.get_record(EXPENSE, expense_id)) for expense_id in expense_ids]
    for expense in expenses:
        _require_record_org(expense, org_id, "expense")
    report = create_report(expenses, submitter, title=title, period=period, report_id=report_id)
    report["org_id"] = org_id
    policies = _load_policies(gateway, org_id)
    settings = _optional_payload(gateway, EXPENSE_SETTINGS, org_id, default={})
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
    approval_request["org_id"] = org_id
    approval_request = initialize_reminder_schedule(approval_request, settings)
    snapshot = _create_approver_snapshot(gateway, approval["report"], approval_request, policies)
    approval_request["approver_snapshot_id"] = snapshot["snapshot_id"]
    gateway.upsert_record(APPROVAL_REQUEST, approval_request["approval_request_id"], approval_request, "pending")
    gateway.upsert_record(EXPENSE_REPORT, approval["report"]["report_id"], approval["report"], "pending_approval")
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
    approval_request, communication = _deliver_approval_request_once(
        gateway,
        approval["report"],
        approval_request,
        submitter,
        settings,
    )
    status = "ok" if communication["status"] == "delivered" else "communication_review_required"
    return {**approval, "status": status, "approval_request": approval_request, "communication": communication}


def decide_report_approval(
    gateway,
    approval_request_id,
    approver_user_id,
    decision,
    note=None,
    decision_id=None,
    org_id=None,
):
    approver_user_id = _normalize_user_id(approver_user_id)
    approval_request = _payload(gateway.get_record(APPROVAL_REQUEST, approval_request_id))
    resolved_org_id = str(org_id if org_id is not None else approval_request.get("org_id", "default"))
    _require_record_org(approval_request, resolved_org_id, "approval request")
    report = _payload(gateway.get_record(EXPENSE_REPORT, approval_request["report_id"]))
    _require_record_org(report, resolved_org_id, "report")
    expenses = [_payload(gateway.get_record(EXPENSE, expense_id)) for expense_id in report.get("expense_ids", [])]
    for expense in expenses:
        _require_record_org(expense, resolved_org_id, "expense")
    deterministic_decision_id = decision_id or _approval_decision_id(resolved_org_id, approval_request_id)

    claim_id = f"{resolved_org_id}:{approval_request_id}"
    existing_claim = _optional_payload(gateway, APPROVAL_DECISION_CLAIM, claim_id)
    if existing_claim is not None:
        return _resolve_existing_decision_claim(
            gateway,
            existing_claim,
            approval_request,
            approver_user_id,
            decision,
            note,
        )

    result = record_approval_decision(
        report,
        expenses,
        approval_request,
        approver_user_id,
        decision,
        note=note,
        decision_id=deterministic_decision_id,
    )
    result["approval_decision"]["org_id"] = resolved_org_id
    result["approval_request"]["reminder_status"] = "resolved"
    result["approval_request"]["next_reminder_at"] = None
    claim = {
        "approval_decision_claim_id": claim_id,
        "org_id": resolved_org_id,
        "approval_request_id": approval_request_id,
        "report_id": report["report_id"],
        "status": "claimed",
        "claimed_at": utc_now(),
        "schema_version": 1,
    }
    reservation = gateway.upsert_record(APPROVAL_DECISION_CLAIM, claim_id, claim, "claimed")
    if not reservation.get("created", False):
        raise ExpenseFlowError(
            "approval_decision_in_progress",
            "This approval request already has a decision in progress. Review governed state before retrying.",
            details={"approval_request_id": approval_request_id},
        )

    try:
        gateway.upsert_record(
            APPROVAL_DECISION,
            result["approval_decision"]["approval_decision_id"],
            result["approval_decision"],
            result["approval_decision"]["decision"],
        )
        gateway.upsert_record(
            EXPENSE_REPORT,
            result["report"]["report_id"],
            result["report"],
            result["report"]["status"],
        )
        gateway.upsert_record(
            APPROVAL_REQUEST,
            approval_request_id,
            result["approval_request"],
            result["approval_request"]["status"],
        )
        for expense in result["expenses"]:
            gateway.upsert_record(EXPENSE, expense["expense_id"], expense, expense["status"])
    except ExpenseFlowError as exc:
        claim["status"] = "review_required"
        claim["error_code"] = exc.code
        claim["completed_at"] = utc_now()
        gateway.upsert_record(APPROVAL_DECISION_CLAIM, claim_id, claim, claim["status"])
        raise
    if result["approval_request"].get("task_id"):
        _complete_approval_task(gateway, result["approval_request"])
        gateway.upsert_record(
            APPROVAL_REQUEST,
            approval_request_id,
            result["approval_request"],
            result["approval_request"]["status"],
        )
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
    claim.update(
        {
            "status": "complete",
            "approval_decision_id": result["approval_decision"]["approval_decision_id"],
            "approver_user_id": approver_user_id,
            "decision": decision,
            "note": str(note or "").strip(),
            "completed_at": utc_now(),
        }
    )
    gateway.upsert_record(APPROVAL_DECISION_CLAIM, claim_id, claim, "complete")
    return result


def decide_report_approval_from_sender(
    gateway,
    sender_id,
    decision,
    org_id,
    approval_request_id=None,
    queue_id=None,
    note=None,
    decision_id=None,
):
    matches = _profiles_for_sender(gateway, sender_id, org_id)
    if not matches:
        raise ExpenseFlowError(
            "unmapped_approval_sender",
            "Approval sender is not mapped to an ExpenseFlow user in this organization.",
            details={"sender_id": sender_id},
        )
    if len(matches) != 1:
        raise ExpenseFlowError(
            "ambiguous_sender_identity",
            "Approval sender maps to more than one ExpenseFlow user.",
            details={"sender_id": sender_id},
        )
    approver = matches[0]
    if approver.get("status") != "active" or not approver.get("can_approve"):
        raise ExpenseFlowError(
            "ineligible_approval_sender",
            "Approval sender is not an active ExpenseFlow approver.",
            details={"user_id": approver.get("user_id")},
        )
    request = _resolve_inbound_approval_request(gateway, org_id, approval_request_id, queue_id)
    return decide_report_approval(
        gateway,
        request["approval_request_id"],
        approver["user_id"],
        decision,
        note=note,
        decision_id=decision_id,
        org_id=org_id,
    )


def reconcile_approval_decision(
    gateway,
    approval_request_id,
    org_id,
    confirm_stale_claim=False,
    as_of=None,
):
    claim_id = f"{org_id}:{approval_request_id}"
    claim = _payload(gateway.get_record(APPROVAL_DECISION_CLAIM, claim_id))
    _require_record_org(claim, org_id, "approval decision claim")
    if claim.get("status") == "complete":
        request = _payload(gateway.get_record(APPROVAL_REQUEST, approval_request_id))
        return _resolve_existing_decision_claim(
            gateway,
            claim,
            request,
            _normalize_user_id(claim.get("approver_user_id")),
            claim.get("decision"),
            claim.get("note"),
        )
    if claim.get("status") == "claimed":
        if not confirm_stale_claim:
            raise ExpenseFlowError(
                "approval_decision_still_claimed",
                "The decision claim may still be active. Confirm it is stale before reconciliation.",
                details={"approval_request_id": approval_request_id},
            )
        claimed_at = parse_utc(claim.get("claimed_at"), "claimed_at")
        reconciliation_time = parse_utc(as_of or utc_now(), "as_of")
        if (reconciliation_time - claimed_at).total_seconds() < 900:
            raise ExpenseFlowError(
                "approval_decision_claim_not_stale",
                "A claimed decision must be at least 15 minutes old before forced reconciliation.",
                details={"approval_request_id": approval_request_id},
            )
    elif claim.get("status") != "review_required":
        raise ExpenseFlowError(
            "approval_decision_not_reconcilable",
            "Only review-required or explicitly confirmed stale claims can be reconciled.",
            details={"approval_request_id": approval_request_id, "claim_status": claim.get("status")},
        )

    decisions = [
        decision
        for decision in (_payload(record) for record in gateway.list_records(APPROVAL_DECISION))
        if decision.get("approval_request_id") == approval_request_id
        and str(decision.get("org_id", org_id)) == str(org_id)
    ]
    if len(decisions) != 1:
        raise ExpenseFlowError(
            "approval_decision_reconciliation_ambiguous",
            "Reconciliation requires exactly one persisted approval decision.",
            details={"approval_request_id": approval_request_id, "decision_count": len(decisions)},
        )
    decision_record = decisions[0]
    request = _payload(gateway.get_record(APPROVAL_REQUEST, approval_request_id))
    report = _payload(gateway.get_record(EXPENSE_REPORT, request["report_id"]))
    expenses = [_payload(gateway.get_record(EXPENSE, expense_id)) for expense_id in report.get("expense_ids", [])]
    _require_record_org(request, org_id, "approval request")
    _require_record_org(report, org_id, "report")
    for expense in expenses:
        _require_record_org(expense, org_id, "expense")
    if _normalize_user_id(decision_record.get("approver_user_id")) != _normalize_user_id(
        request.get("approver_user_id")
    ):
        raise ExpenseFlowError(
            "approval_decision_reconciliation_conflict",
            "The persisted decision actor does not match the assigned approver.",
            details={"approval_request_id": approval_request_id},
        )

    decision = decision_record.get("decision")
    target_status = "approved" if decision == "approved" else "rejected" if decision == "rejected" else None
    if target_status is None:
        raise ExpenseFlowError(
            "approval_decision_reconciliation_conflict",
            "The persisted approval decision has an invalid decision value.",
            details={"approval_request_id": approval_request_id, "decision": decision},
        )
    _require_reconcilable_status(report, {"pending_approval", target_status}, "report", report["report_id"])
    _require_reconcilable_status(request, {"pending", decision}, "approval request", approval_request_id)
    for expense in expenses:
        _require_reconcilable_status(expense, {"submitted", target_status}, "expense", expense["expense_id"])

    decided_at = decision_record.get("created_at") or utc_now()
    report = dict(report)
    report["status"] = target_status
    if target_status == "approved":
        report["approved_at"] = report.get("approved_at") or decided_at
    request = dict(request)
    request.update(
        {
            "status": decision,
            "decided_at": request.get("decided_at") or decided_at,
            "reminder_status": "resolved",
            "next_reminder_at": None,
        }
    )
    reconciled_expenses = []
    for expense in expenses:
        updated = dict(expense)
        updated["status"] = target_status
        reconciled_expenses.append(updated)

    gateway.upsert_record(EXPENSE_REPORT, report["report_id"], report, report["status"])
    gateway.upsert_record(APPROVAL_REQUEST, approval_request_id, request, request["status"])
    for expense in reconciled_expenses:
        gateway.upsert_record(EXPENSE, expense["expense_id"], expense, expense["status"])
    _complete_approval_task(gateway, request)
    gateway.upsert_record(APPROVAL_REQUEST, approval_request_id, request, request["status"])

    claim.update(
        {
            "status": "complete",
            "approval_decision_id": decision_record["approval_decision_id"],
            "approver_user_id": decision_record["approver_user_id"],
            "decision": decision,
            "note": str(decision_record.get("note") or "").strip(),
            "reconciled_at": utc_now(),
            "completed_at": claim.get("completed_at") or utc_now(),
        }
    )
    gateway.upsert_record(APPROVAL_DECISION_CLAIM, claim_id, claim, "complete")
    gateway.log_action(
        "skill.approval_decision",
        "ExpenseFlow approval decision reconciled",
        f"expenseflow:decision-reconciled:{decision_record['approval_decision_id']}",
        {"org_id": org_id, "approval_request_id": approval_request_id, "decision": decision},
    )
    return {
        "status": "reconciled",
        "report": report,
        "expenses": reconciled_expenses,
        "approval_request": request,
        "approval_decision": decision_record,
        "approval_decision_claim": claim,
    }


def _submission_ids(org_id, submitter_user_id, expense_ids, report_id, approval_request_id):
    fingerprint = "|".join(
        [str(org_id), str(submitter_user_id), *sorted(str(expense_id) for expense_id in expense_ids)]
    )
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return report_id or f"er_{digest}", approval_request_id or f"ar_{digest}"


def _resume_report_submission(gateway, report, submitter, expense_ids, approval_request_id, org_id):
    _require_record_org(report, org_id, "report")
    if _normalize_user_id(report.get("submitter_user_id")) != _normalize_user_id(submitter.get("user_id")):
        raise ExpenseFlowError(
            "report_submission_conflict",
            "The deterministic report ID belongs to a different submitter.",
            details={"report_id": report.get("report_id")},
        )
    if sorted(str(value) for value in report.get("expense_ids", [])) != sorted(expense_ids):
        raise ExpenseFlowError(
            "report_submission_conflict",
            "The deterministic report ID belongs to a different expense set.",
            details={"report_id": report.get("report_id")},
        )
    if report.get("status") == "held_pending_manager":
        return {
            "status": "held_pending_manager",
            "report": report,
            "routing": {"status": "held_pending_manager"},
            "approval_request": None,
            "idempotent_replay": True,
        }
    if report.get("status") != "pending_approval":
        raise ExpenseFlowError(
            "report_submission_conflict",
            "An existing report cannot be submitted again from its current status.",
            details={"report_id": report.get("report_id"), "status": report.get("status")},
        )
    if report.get("approval_request_id") != approval_request_id:
        raise ExpenseFlowError(
            "approval_request_conflict",
            "The report is linked to a different approval request.",
            details={"report_id": report.get("report_id")},
        )
    request = _optional_payload(gateway, APPROVAL_REQUEST, approval_request_id)
    if request is None:
        raise ExpenseFlowError(
            "submission_state_incomplete",
            "The report exists without its approval request. Review governed state before delivery.",
            details={"report_id": report.get("report_id"), "approval_request_id": approval_request_id},
        )
    _require_record_org(request, org_id, "approval request")
    if request.get("report_id") != report.get("report_id"):
        raise ExpenseFlowError(
            "approval_request_conflict",
            "The approval request is linked to a different report.",
            details={"approval_request_id": approval_request_id},
        )

    for expense_id in expense_ids:
        expense = _payload(gateway.get_record(EXPENSE, expense_id))
        _require_record_org(expense, org_id, "expense")
        if expense.get("status") == "draft":
            expense = _transition_expense(expense, "submitted")
            expense["report_id"] = report["report_id"]
            gateway.upsert_record(EXPENSE, expense_id, expense, "submitted")
        elif expense.get("status") != "submitted" or expense.get("report_id") != report.get("report_id"):
            raise ExpenseFlowError(
                "submission_state_conflict",
                "An expense is not safely recoverable into the existing report.",
                details={"expense_id": expense_id, "status": expense.get("status")},
            )

    settings = _optional_payload(gateway, EXPENSE_SETTINGS, org_id, default={})
    request, communication = _deliver_approval_request_once(gateway, report, request, submitter, settings)
    status = "ok" if communication["status"] == "delivered" else "communication_review_required"
    return {
        "status": status,
        "report": report,
        "routing": {
            "status": "ok",
            "approver_user_id": request.get("approver_user_id"),
            "routing_reason": request.get("routing_reason"),
        },
        "approval_request": request,
        "communication": communication,
        "idempotent_replay": True,
    }


def _deliver_approval_request_once(gateway, report, request, submitter, settings):
    org_id = str(request.get("org_id", report.get("org_id", "default")))
    request_id = request["approval_request_id"]
    now = utc_now()
    notification_event_id = f"{org_id}:{request_id}:initial-approval"
    notification = _existing_notification_delivery(gateway, notification_event_id)
    if notification is None:
        notification = _send_notification_once(
            gateway,
            notification_event_id,
            org_id,
            request["approver_user_id"],
            "initial_approval",
            _with_message_prefix(settings, _approval_message(report, submitter, request)),
            request,
            now,
        )
        if notification["status"] == "skipped":
            notification = _existing_notification_delivery(gateway, notification_event_id)
    request = dict(request)
    request["initial_notification_event_id"] = notification_event_id
    request["initial_notification_status"] = notification["status"]
    if notification.get("queue_id"):
        request["backchannel_queue_id"] = notification["queue_id"]
    gateway.upsert_record(APPROVAL_REQUEST, request_id, request, request["status"])

    task_event_id = f"{org_id}:{request_id}:approval-task"
    task = _create_task_once(
        gateway,
        task_event_id,
        org_id,
        request["approver_user_id"],
        _with_message_prefix(settings, f"Review expense report {report['report_id']} ({request_id})"),
        {"report_id": report["report_id"], "approval_request_id": request_id},
        now,
    )
    request["task_event_id"] = task_event_id
    request["task_creation_status"] = task["status"]
    if task.get("task_id"):
        request["task_id"] = task["task_id"]
    gateway.upsert_record(APPROVAL_REQUEST, request_id, request, request["status"])

    notification_ok = notification["status"] in {"sent", "already_sent"}
    task_ok = task["status"] in {"created", "already_created"}
    return request, {
        "status": "delivered" if notification_ok and task_ok else "review_required",
        "notification": notification,
        "task": task,
    }


def _existing_notification_delivery(gateway, event_id):
    event = _optional_payload(gateway, NOTIFICATION_EVENT, event_id)
    if event is None:
        return None
    if event.get("status") == "sent":
        return {
            "status": "already_sent",
            "notification_event_id": event_id,
            "queue_id": event.get("queue_id"),
        }
    return {
        "status": event.get("status", "delivery_unknown"),
        "notification_event_id": event_id,
    }


def _create_task_once(gateway, event_id, org_id, user_id, title, metadata, as_of):
    existing = _optional_payload(gateway, TASK_EVENT, event_id)
    if existing is not None:
        if existing.get("status") == "created":
            return {"status": "already_created", "task_event_id": event_id, "task_id": existing.get("task_id")}
        return {"status": existing.get("status", "creation_unknown"), "task_event_id": event_id}
    event = {
        "task_event_id": event_id,
        "org_id": org_id,
        "kind": "approval_visibility",
        "approval_request_id": metadata["approval_request_id"],
        "report_id": metadata["report_id"],
        "target_user_id": user_id,
        "status": "reserved",
        "reserved_at": as_of,
        "schema_version": 1,
    }
    reservation = gateway.upsert_record(TASK_EVENT, event_id, event, "reserved")
    if not reservation.get("created", False):
        existing = _optional_payload(gateway, TASK_EVENT, event_id, default=event)
        return {"status": existing.get("status", "reserved"), "task_event_id": event_id}
    try:
        task = gateway.create_task(title, user_id, metadata)
    except ExpenseFlowError as exc:
        event["status"] = "creation_unknown"
        event["creation_error"] = exc.code
        event["completed_at"] = as_of
        gateway.upsert_record(TASK_EVENT, event_id, event, event["status"])
        return {"status": "creation_unknown", "task_event_id": event_id}
    event["status"] = "created"
    event["task_id"] = task.get("task_id")
    event["completed_at"] = as_of
    gateway.upsert_record(TASK_EVENT, event_id, event, "created")
    return {"status": "created", "task_event_id": event_id, "task_id": event.get("task_id")}


def _approval_decision_id(org_id, approval_request_id):
    digest = hashlib.sha256(f"{org_id}|{approval_request_id}".encode("utf-8")).hexdigest()[:16]
    return f"ad_{digest}"


def _resolve_existing_decision_claim(gateway, claim, request, approver_user_id, decision, note):
    if claim.get("status") != "complete":
        raise ExpenseFlowError(
            "approval_decision_review_required",
            "A prior decision attempt did not finish cleanly. Review governed state before taking another decision.",
            details={"approval_request_id": request.get("approval_request_id"), "claim_status": claim.get("status")},
        )
    normalized_note = str(note or "").strip()
    if (
        _normalize_user_id(claim.get("approver_user_id")) != approver_user_id
        or claim.get("decision") != decision
        or claim.get("note", "") != normalized_note
    ):
        raise ExpenseFlowError(
            "approval_decision_conflict",
            "This approval request already has a different completed decision.",
            details={"approval_request_id": request.get("approval_request_id")},
        )
    completed_request = _payload(gateway.get_record(APPROVAL_REQUEST, request["approval_request_id"]))
    report = _payload(gateway.get_record(EXPENSE_REPORT, completed_request["report_id"]))
    expenses = [_payload(gateway.get_record(EXPENSE, expense_id)) for expense_id in report.get("expense_ids", [])]
    approval_decision = _payload(gateway.get_record(APPROVAL_DECISION, claim["approval_decision_id"]))
    return {
        "status": "already_decided",
        "report": report,
        "expenses": expenses,
        "approval_request": completed_request,
        "approval_decision": approval_decision,
    }


def _require_reconcilable_status(payload, allowed, entity, external_id):
    if payload.get("status") not in allowed:
        raise ExpenseFlowError(
            "approval_decision_reconciliation_conflict",
            f"The {entity} has a status that cannot be reconciled to this decision.",
            details={"external_id": external_id, "status": payload.get("status"), "allowed": sorted(allowed)},
        )


def _complete_approval_task(gateway, approval_request):
    try:
        completed_task = gateway.complete_task(approval_request["task_id"])
        approval_request["task_completion_status"] = completed_task.get("status", "completed")
        approval_request.pop("task_completion_error", None)
    except ExpenseFlowError as exc:
        approval_request["task_completion_status"] = "failed"
        approval_request["task_completion_error"] = exc.code


def _resolve_inbound_approval_request(gateway, org_id, approval_request_id, queue_id):
    if not approval_request_id and not queue_id:
        raise ExpenseFlowError(
            "missing_approval_correlation",
            "Approval replies require an approval request ID or an exact backchannel queue ID.",
        )
    request = None
    if approval_request_id:
        request = _payload(gateway.get_record(APPROVAL_REQUEST, approval_request_id))
        _require_record_org(request, org_id, "approval request")
    if queue_id:
        queue_matches = [
            candidate
            for candidate in (_payload(record) for record in gateway.list_records(APPROVAL_REQUEST))
            if str(candidate.get("org_id", org_id)) == str(org_id)
            and candidate.get("backchannel_queue_id") == queue_id
        ]
        if len(queue_matches) != 1:
            raise ExpenseFlowError(
                "ambiguous_approval_correlation" if queue_matches else "approval_correlation_not_found",
                "Backchannel queue ID must identify exactly one approval request in this organization.",
                details={"queue_id": queue_id, "match_count": len(queue_matches)},
            )
        if request is not None and request.get("approval_request_id") != queue_matches[0].get("approval_request_id"):
            raise ExpenseFlowError(
                "approval_correlation_conflict",
                "Approval request ID and queue ID refer to different requests.",
            )
        request = queue_matches[0]
    return request


def send_due_approval_reminders(gateway, org_id="default", as_of=None):
    as_of = format_utc(parse_utc(as_of or utc_now(), "as_of"))
    settings = _optional_payload(gateway, EXPENSE_SETTINGS, org_id, default={})
    config = normalize_reminder_settings(settings)
    summary = {
        "org_id": org_id,
        "as_of": as_of,
        "enabled": config["enabled"],
        "scanned": 0,
        "sent": [],
        "delivery_unknown": [],
        "escalated": [],
        "skipped": [],
    }
    if not config["enabled"]:
        return summary

    for record in gateway.list_records(APPROVAL_REQUEST, status="pending"):
        request = _payload(record)
        if str(request.get("org_id", org_id)) != str(org_id):
            continue
        summary["scanned"] += 1
        if not is_reminder_due(request, as_of):
            continue

        report = _payload(gateway.get_record(EXPENSE_REPORT, request["report_id"]))
        if report.get("status") != "pending_approval":
            request = dict(request)
            request["reminder_status"] = "resolved"
            request["next_reminder_at"] = None
            gateway.upsert_record(APPROVAL_REQUEST, request["approval_request_id"], request, request["status"])
            summary["skipped"].append({"approval_request_id": request["approval_request_id"], "reason": "report_resolved"})
            continue

        approver = _optional_payload(gateway, USER_PROFILE, request["approver_user_id"])
        if approver is None or approver.get("status") != "active" or not approver.get("can_approve"):
            request = dict(request)
            request["reminder_status"] = "blocked_approver"
            request["next_reminder_at"] = None
            gateway.upsert_record(APPROVAL_REQUEST, request["approval_request_id"], request, request["status"])
            summary["skipped"].append(
                {"approval_request_id": request["approval_request_id"], "reason": "approver_unavailable"}
            )
            escalation_ids = config["escalation_user_ids"] or _admin_user_ids(settings)
            for admin_user_id in escalation_ids:
                if str(admin_user_id) == str(request["approver_user_id"]):
                    continue
                escalation = _send_notification_once(
                    gateway,
                    f"{request['approval_request_id']}:approver-unavailable:{admin_user_id}",
                    org_id,
                    admin_user_id,
                    "approver_unavailable",
                    _with_message_prefix(settings, _approver_unavailable_message(report, request)),
                    request,
                    as_of,
                )
                if escalation["status"] == "sent":
                    summary["escalated"].append(
                        {"approval_request_id": request["approval_request_id"], "user_id": admin_user_id}
                    )
            continue

        attempt = int(request.get("reminder_count") or 0) + 1
        event_id = f"{request['approval_request_id']}:reminder:{attempt}"
        delivery = _send_notification_once(
            gateway,
            event_id,
            org_id,
            request["approver_user_id"],
            "approval_reminder",
            _with_message_prefix(settings, _reminder_message(report, request, attempt, config["max_attempts"])),
            request,
            as_of,
        )
        request = advance_reminder_schedule(request, settings, as_of)
        gateway.upsert_record(APPROVAL_REQUEST, request["approval_request_id"], request, request["status"])
        summary[delivery["status"]].append(request["approval_request_id"])

        if request["reminder_status"] == "exhausted":
            escalation_ids = config["escalation_user_ids"] or _admin_user_ids(settings)
            for admin_user_id in escalation_ids:
                if str(admin_user_id) == str(request["approver_user_id"]):
                    continue
                escalation_event_id = f"{request['approval_request_id']}:escalation:{admin_user_id}"
                escalation = _send_notification_once(
                    gateway,
                    escalation_event_id,
                    org_id,
                    admin_user_id,
                    "approval_escalation",
                    _with_message_prefix(settings, _escalation_message(report, request)),
                    request,
                    as_of,
                )
                if escalation["status"] == "sent":
                    summary["escalated"].append(
                        {"approval_request_id": request["approval_request_id"], "user_id": admin_user_id}
                    )
    gateway.log_action(
        "skill.notification_event",
        "ExpenseFlow approval reminder sweep completed",
        f"expenseflow:reminder-sweep:{org_id}:{as_of}",
        {
            "org_id": org_id,
            "as_of": as_of,
            "scanned": summary["scanned"],
            "sent_count": len(summary["sent"]),
            "escalated_count": len(summary["escalated"]),
        },
    )
    return summary


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


def export_approved_report_sheets(gateway, report_id):
    report = _payload(gateway.get_record(EXPENSE_REPORT, report_id))
    org_id = str(report.get("org_id") or "default")
    destination = _payload(gateway.get_record(ACCOUNTING_DESTINATION, org_id))
    if destination.get("status") != "active" or destination.get("destination_type") != "sheets":
        raise ExpenseFlowError(
            "sheets_destination_not_active",
            "The organization does not have an active Google Sheets destination.",
        )
    config = destination.get("config") or {}
    try:
        return _export_approved_report_sheets(gateway, report, destination)
    except ExpenseFlowError as exc:
        if not config.get("fallback_to_csv") or not exc.code.startswith("sheets_"):
            raise
        items = [
            _payload(record)
            for record in gateway.list_records(EXPORT_ITEM)
            if _payload(record).get("report_id") == report_id
        ]
        unsafe_statuses = {"appended", "confirmed", "unknown"}
        if any(item.get("status") in unsafe_statuses for item in items):
            raise
        expenses = [_payload(gateway.get_record(EXPENSE, expense_id)) for expense_id in report.get("expense_ids", [])]
        return {
            "status": "fallback_ready",
            "destination": "csv",
            "sheets_error": exc.to_dict(),
            "csv": generate_report_csv(report, expenses),
            "report": report,
        }


def _export_approved_report_sheets(gateway, report, destination):
    if report.get("status") not in {"approved", "exported"}:
        raise ExpenseFlowError(
            "report_not_approved",
            "Only approved reports can be exported.",
            details={"report_id": report.get("report_id"), "status": report.get("status")},
        )
    org_id = str(report.get("org_id") or destination.get("org_id") or "default")
    config = destination.get("config") or {}
    spreadsheet_id = str(config.get("spreadsheet_id") or "")
    sheet_name = str(config.get("sheet_name") or "ExpenseFlow")
    run_id = f"sheets:{org_id}:{spreadsheet_id}:{report['report_id']}"
    existing_run = _optional_payload(gateway, EXPORT_RUN, run_id)
    if existing_run is not None and existing_run.get("status") == "complete":
        return {
            "status": "already_exported",
            "report": report,
            "export_run": existing_run,
            "items": _report_export_items(gateway, report["report_id"]),
        }

    expenses = [_payload(gateway.get_record(EXPENSE, expense_id)) for expense_id in report.get("expense_ids", [])]
    if not expenses:
        raise ExpenseFlowError("empty_export", "At least one expense is required for Google Sheets export.")
    if report.get("status") == "exported" and existing_run is None:
        raise ExpenseFlowError(
            "incomplete_export_state",
            "The report is exported but its Google Sheets export run is not complete.",
            details={"run_id": run_id},
        )
    rows = {
        expense["expense_id"]: build_sheet_row(report, expense, org_id, spreadsheet_id)
        for expense in expenses
    }
    metadata = gateway.sheets_get_metadata(spreadsheet_id)
    sheet_id, sheet_name = _resolve_sheet(metadata, config, existing_run)
    _ensure_sheet_headers(gateway, spreadsheet_id, sheet_name)

    if existing_run is not None:
        existing_run = dict(existing_run)
        existing_run["current_sheet_name"] = sheet_name
        return _reconcile_sheets_export(
            gateway,
            report,
            expenses,
            rows,
            existing_run,
            spreadsheet_id,
            sheet_name,
        )

    run = {
        "export_run_id": run_id,
        "org_id": org_id,
        "report_id": report["report_id"],
        "destination_type": "sheets",
        "spreadsheet_id": spreadsheet_id,
        "sheet_id": sheet_id,
        "sheet_name_at_export": sheet_name,
        "status": "in_progress",
        "claim_basis_at": report.get("approved_at") or report.get("created_at"),
        "schema_version": 1,
    }
    claimed = gateway.upsert_record(EXPORT_RUN, run_id, run, "in_progress")
    if not claimed.get("created", False):
        raise ExpenseFlowError(
            "sheets_export_already_claimed",
            "Another ExpenseFlow invocation claimed this report export. Retry to reconcile it.",
            retryable=True,
            details={"export_run_id": run_id},
        )

    for expense in expenses:
        _reserve_export_item(gateway, run, expense, rows[expense["expense_id"]])

    existing_rows = _read_indexed_rows(gateway, spreadsheet_id, sheet_name)
    confirmed_items = []
    for expense in expenses:
        expected = rows[expense["expense_id"]]
        row_id = expected[-1]
        matches = existing_rows.get(row_id, [])
        if len(matches) > 1:
            _mark_export_item_failed(gateway, run, expense, expected, "duplicate_sheet_row_id")
            raise ExpenseFlowError(
                "duplicate_sheet_row_id",
                "The spreadsheet contains duplicate ExpenseFlow row IDs.",
                details={"expense_id": expense["expense_id"], "row_id": row_id},
            )
        if matches:
            row_number = matches[0]["row_number"]
            if matches[0]["values"] != expected:
                gateway.sheets_update_values(spreadsheet_id, row_range(sheet_name, row_number), [expected])
            confirmed_items.append(
                _confirm_export_item(gateway, run, expense, expected, row_number, "reconciled")
            )
            continue
        confirmed_items.append(
            _append_and_confirm_sheet_row(gateway, run, expense, expected, spreadsheet_id, sheet_name)
        )

    return _complete_sheets_export(gateway, report, expenses, run, confirmed_items)


def _ensure_sheet_headers(gateway, spreadsheet_id, sheet_name):
    response = gateway.sheets_read_values(spreadsheet_id, header_range(sheet_name))
    rows = normalized_values(response, len(SHEET_COLUMNS))
    if not rows or not any(rows[0]):
        gateway.sheets_update_values(spreadsheet_id, header_range(sheet_name), [SHEET_COLUMNS])
        return
    if rows[0] != SHEET_COLUMNS:
        raise ExpenseFlowError(
            "sheets_header_mismatch",
            "The configured sheet does not have the ExpenseFlow column layout.",
            details={"expected": SHEET_COLUMNS, "actual": rows[0]},
        )


def _find_sheet_id(metadata, sheet_name):
    for sheet in metadata.get("sheets", []):
        properties = sheet.get("properties", {})
        if properties.get("title") == sheet_name:
            return properties.get("sheetId")
    raise ExpenseFlowError(
        "sheet_tab_not_found",
        "The configured Google Sheets tab was not found.",
        details={"sheet_name": sheet_name},
    )


def _resolve_sheet(metadata, config, existing_run=None):
    expected_sheet_id = None
    if existing_run is not None:
        expected_sheet_id = existing_run.get("sheet_id")
    if expected_sheet_id is None:
        expected_sheet_id = config.get("sheet_id")
    if expected_sheet_id is not None:
        for sheet in metadata.get("sheets", []):
            properties = sheet.get("properties", {})
            if str(properties.get("sheetId")) == str(expected_sheet_id):
                return properties.get("sheetId"), properties.get("title")
        raise ExpenseFlowError(
            "sheet_tab_not_found",
            "The configured Google Sheets tab ID was not found.",
            details={"sheet_id": expected_sheet_id},
        )
    sheet_name = str(config.get("sheet_name") or "ExpenseFlow")
    return _find_sheet_id(metadata, sheet_name), sheet_name


def _read_indexed_rows(gateway, spreadsheet_id, sheet_name):
    response = gateway.sheets_read_values(spreadsheet_id, data_range(sheet_name))
    return index_rows_by_id(normalized_values(response, len(SHEET_COLUMNS)))


def _reserve_export_item(gateway, run, expense, expected):
    item_id = f"{run['export_run_id']}:{expense['expense_id']}"
    existing = _optional_payload(gateway, EXPORT_ITEM, item_id)
    if existing is not None:
        return existing
    item = {
        "export_item_id": item_id,
        "export_run_id": run["export_run_id"],
        "org_id": run["org_id"],
        "report_id": run["report_id"],
        "expense_id": expense["expense_id"],
        "destination_type": "sheets",
        "spreadsheet_id": run["spreadsheet_id"],
        "sheet_id": run["sheet_id"],
        "expenseflow_row_id": expected[-1],
        "content_hash": content_hash(expected),
        "status": "reserved",
        "reserved_at": utc_now(),
        "schema_version": 1,
    }
    result = gateway.upsert_record(EXPORT_ITEM, item_id, item, "reserved")
    if not result.get("created", False):
        raise ExpenseFlowError(
            "export_item_claim_conflict",
            "Another invocation reserved an expense export row.",
            retryable=True,
            details={"export_item_id": item_id},
        )
    return item


def _append_and_confirm_sheet_row(gateway, run, expense, expected, spreadsheet_id, sheet_name):
    item_id = f"{run['export_run_id']}:{expense['expense_id']}"
    try:
        response = gateway.sheets_append_values(spreadsheet_id, header_range(sheet_name), [expected])
    except ExpenseFlowError as exc:
        status = "failed" if exc.code in {
            "sheets_invalid_request",
            "sheets_unauthenticated",
            "sheets_permission_denied",
            "sheets_not_found",
        } else "unknown"
        item = _payload(gateway.get_record(EXPORT_ITEM, item_id))
        item["status"] = status
        item["error_code"] = exc.code
        item["completed_at"] = utc_now()
        gateway.upsert_record(EXPORT_ITEM, item_id, item, status)
        raise
    try:
        row_number = parse_updated_range(response.get("updates", {}).get("updatedRange"), sheet_name)
    except ExpenseFlowError as exc:
        item = _payload(gateway.get_record(EXPORT_ITEM, item_id))
        item["status"] = "unknown"
        item["error_code"] = exc.code
        item["completed_at"] = utc_now()
        gateway.upsert_record(EXPORT_ITEM, item_id, item, "unknown")
        raise
    item = _payload(gateway.get_record(EXPORT_ITEM, item_id))
    item["status"] = "appended"
    item["destination_row"] = row_number
    item["destination_range"] = row_range(sheet_name, row_number)
    item["appended_at"] = utc_now()
    gateway.upsert_record(EXPORT_ITEM, item_id, item, "appended")
    readback = normalized_values(
        gateway.sheets_read_values(spreadsheet_id, item["destination_range"]),
        len(SHEET_COLUMNS),
    )
    if len(readback) != 1 or readback[0] != expected:
        raise ExpenseFlowError(
            "sheets_readback_mismatch",
            "The appended Google Sheets row could not be confirmed.",
            details={"expense_id": expense["expense_id"], "destination_range": item["destination_range"]},
        )
    return _confirm_export_item(gateway, run, expense, expected, row_number, "appended")


def _confirm_export_item(gateway, run, expense, expected, row_number, confirmation_source):
    item_id = f"{run['export_run_id']}:{expense['expense_id']}"
    item = _payload(gateway.get_record(EXPORT_ITEM, item_id))
    item.update(
        {
            "status": "confirmed",
            "content_hash": content_hash(expected),
            "destination_row": row_number,
            "destination_range": row_range(
                run.get("current_sheet_name") or run["sheet_name_at_export"],
                row_number,
            ),
            "confirmation_source": confirmation_source,
            "confirmed_at": utc_now(),
        }
    )
    gateway.upsert_record(EXPORT_ITEM, item_id, item, "confirmed")
    return item


def _mark_export_item_failed(gateway, run, expense, expected, error_code):
    item_id = f"{run['export_run_id']}:{expense['expense_id']}"
    item = _optional_payload(gateway, EXPORT_ITEM, item_id) or _reserve_export_item(gateway, run, expense, expected)
    item["status"] = "failed"
    item["error_code"] = error_code
    item["completed_at"] = utc_now()
    gateway.upsert_record(EXPORT_ITEM, item_id, item, "failed")


def _reconcile_sheets_export(gateway, report, expenses, rows, run, spreadsheet_id, sheet_name):
    existing_rows = _read_indexed_rows(gateway, spreadsheet_id, sheet_name)
    confirmed = []
    missing = []
    for expense in expenses:
        expected = rows[expense["expense_id"]]
        matches = existing_rows.get(expected[-1], [])
        if len(matches) > 1:
            raise ExpenseFlowError(
                "duplicate_sheet_row_id",
                "The spreadsheet contains duplicate ExpenseFlow row IDs.",
                details={"expense_id": expense["expense_id"], "row_id": expected[-1]},
            )
        if not matches:
            missing.append(expense["expense_id"])
            continue
        if matches[0]["values"] != expected:
            raise ExpenseFlowError(
                "sheets_reconciliation_mismatch",
                "An existing ExpenseFlow row does not match its approved expense.",
                details={"expense_id": expense["expense_id"], "row_number": matches[0]["row_number"]},
            )
        if _optional_payload(gateway, EXPORT_ITEM, f"{run['export_run_id']}:{expense['expense_id']}") is None:
            _reserve_export_item(gateway, run, expense, expected)
        confirmed.append(
            _confirm_export_item(gateway, run, expense, expected, matches[0]["row_number"], "reconciled")
        )
    if missing:
        raise ExpenseFlowError(
            "sheets_export_incomplete",
            "A prior export claim is incomplete. Missing rows were not appended again to avoid duplicates.",
            details={"export_run_id": run["export_run_id"], "missing_expense_ids": missing},
        )
    return _complete_sheets_export(gateway, report, expenses, run, confirmed)


def _complete_sheets_export(gateway, report, expenses, run, confirmed_items):
    exported_report = report if report.get("status") == "exported" else transition_report(report, "exported")
    if report.get("status") != "exported":
        gateway.upsert_record(EXPENSE_REPORT, report["report_id"], exported_report, "exported")
    for expense in expenses:
        if expense.get("status") != "exported":
            exported_expense = _transition_expense(expense, "exported")
            gateway.upsert_record(EXPENSE, exported_expense["expense_id"], exported_expense, "exported")
    completed_run = dict(run)
    completed_run["status"] = "complete"
    completed_run["completed_at"] = utc_now()
    completed_run["confirmed_item_count"] = len(confirmed_items)
    gateway.upsert_record(EXPORT_RUN, run["export_run_id"], completed_run, "complete")
    gateway.log_action(
        "skill.expense_report",
        "Expense report exported to Google Sheets",
        f"expenseflow:export-sheets:{report['report_id']}:{run['spreadsheet_id']}",
        {
            "report_id": report["report_id"],
            "spreadsheet_id": run["spreadsheet_id"],
            "sheet_id": run["sheet_id"],
            "confirmed_item_count": len(confirmed_items),
        },
    )
    return {
        "status": "ok",
        "destination": "sheets",
        "report": exported_report,
        "export_run": completed_run,
        "items": confirmed_items,
    }


def _report_export_items(gateway, report_id):
    return [
        _payload(record)
        for record in gateway.list_records(EXPORT_ITEM)
        if _payload(record).get("report_id") == report_id
    ]


def refresh_qbo_reference_cache(gateway, org_id="default"):
    destination = _active_qbo_destination(gateway, org_id)
    config = destination["config"]
    connection = _require_qbo_connection(gateway, config["realm_id"])
    entities = ("Account", "Vendor", "Customer", "TaxCode", "Class", "Department", "Currency")
    responses = {}
    for entity in entities:
        responses[entity] = _query_qbo_reference_entity(gateway, config["realm_id"], entity)
    cache = {
        "org_id": str(org_id),
        "realm_id": config["realm_id"],
        "environment": connection.get("environment"),
        "references": normalize_qbo_reference_cache(responses),
        "cached_at": utc_now(),
        "status": "active",
        "schema_version": 1,
    }
    gateway.upsert_record(ACCOUNTING_REFERENCE_CACHE, org_id, cache, "active")
    return cache


def _query_qbo_reference_entity(gateway, realm_id, entity, page_size=100, max_rows=1000):
    rows = []
    for start_position in range(1, max_rows + 1, page_size):
        response = gateway.quickbooks_call(
            "query",
            realm_id=realm_id,
            query={
                "query": (
                    f"select * from {entity} startposition {start_position} "
                    f"maxresults {page_size}"
                )
            },
        )
        query_response = response.get("QueryResponse", response.get("queryResponse", {}))
        page = query_response.get(entity, query_response.get(entity.lower(), []))
        if isinstance(page, dict):
            page = [page]
        if not isinstance(page, list):
            raise ExpenseFlowError(
                "invalid_qbo_query_response",
                "QuickBooks returned an invalid reference query response.",
                details={"entity": entity},
            )
        rows.extend(page)
        if len(page) < page_size:
            return {"QueryResponse": {entity: rows}}
    raise ExpenseFlowError(
        "qbo_reference_cache_limit",
        "QuickBooks reference data exceeds the 1,000-row cache safety limit.",
        details={"entity": entity, "max_rows": max_rows},
    )


def sync_approved_report_qbo(
    gateway,
    report_id,
    session_key=None,
    chat_id=None,
    retry_terminal=False,
):
    report = _payload(gateway.get_record(EXPENSE_REPORT, report_id))
    org_id = str(report.get("org_id") or "default")
    destination = _active_qbo_destination(gateway, org_id)
    config = destination["config"]
    expenses = [_payload(gateway.get_record(EXPENSE, expense_id)) for expense_id in report.get("expense_ids", [])]
    transaction = build_qbo_transaction(report, expenses, config)
    runs = _qbo_runs(gateway, report_id, config["realm_id"])

    completed = next((run for run in runs if run.get("status") == "complete"), None)
    if completed is not None:
        return {
            "status": "already_synced",
            "destination": "qbo",
            "report": report,
            "export_run": completed,
            "items": _qbo_items_for_run(gateway, completed["export_run_id"]),
        }
    if report.get("status") == "synced" and not runs:
        raise ExpenseFlowError(
            "incomplete_qbo_sync_state",
            "The report is synced but its QuickBooks run is missing.",
            details={"report_id": report_id},
        )

    if runs:
        latest = runs[-1]
        if latest.get("payload_hash") != transaction["payload_hash"]:
            raise ExpenseFlowError(
                "qbo_payload_changed_after_claim",
                "The approved QuickBooks payload changed after a sync claim was created.",
                details={"export_run_id": latest.get("export_run_id")},
            )
        if latest.get("status") in {"rejected", "expired"} and retry_terminal:
            attempt = int(latest.get("attempt", 1)) + 1
            connection = _require_qbo_connection(gateway, config["realm_id"])
            return _submit_qbo_sync(
                gateway,
                report,
                expenses,
                transaction,
                config,
                connection,
                attempt,
                session_key,
                chat_id,
            )
        return _reconcile_qbo_sync(gateway, report, expenses, latest, transaction)

    connection = _require_qbo_connection(gateway, config["realm_id"])
    return _submit_qbo_sync(
        gateway,
        report,
        expenses,
        transaction,
        config,
        connection,
        1,
        session_key,
        chat_id,
    )


def _submit_qbo_sync(
    gateway,
    report,
    expenses,
    transaction,
    config,
    connection,
    attempt,
    session_key,
    chat_id,
):
    org_id = str(report.get("org_id") or "default")
    run_id = f"qbo:{org_id}:{config['realm_id']}:{report['report_id']}:attempt:{attempt}"
    run = {
        "export_run_id": run_id,
        "org_id": org_id,
        "report_id": report["report_id"],
        "destination_type": "qbo",
        "realm_id": config["realm_id"],
        "environment": connection.get("environment"),
        "entity_type": transaction["entity_type"],
        "path": transaction["path"],
        "payload_hash": transaction["payload_hash"],
        "request_id": transaction["request_id"],
        "max_execution_checks": config.get("max_execution_checks", 12),
        "execution_check_count": 0,
        "attempt": attempt,
        "status": "claimed",
        "claimed_at": utc_now(),
        "schema_version": 1,
    }
    claimed = gateway.upsert_record(EXPORT_RUN, run_id, run, "claimed")
    if not claimed.get("created", False):
        raise ExpenseFlowError(
            "qbo_sync_already_claimed",
            "Another ExpenseFlow invocation claimed this QuickBooks sync.",
            retryable=True,
            details={"export_run_id": run_id},
        )
    for line_item in transaction["line_items"]:
        _reserve_qbo_item(gateway, run, line_item)

    try:
        brief = gateway.quickbooks_write(
            transaction["path"],
            transaction["body"],
            realm_id=config["realm_id"],
            request_id=transaction["request_id"],
            reason=f"Sync approved ExpenseFlow report {report['report_id']} as {transaction['entity_type']}",
            session_key=session_key,
            chat_id=chat_id,
        )
    except ExpenseFlowError as exc:
        failed_run = dict(run)
        failed_run["status"] = "unknown"
        failed_run["error_code"] = exc.code
        failed_run["completed_at"] = utc_now()
        gateway.upsert_record(EXPORT_RUN, run_id, failed_run, "unknown")
        for item in _qbo_items_for_run(gateway, run_id):
            item = dict(item)
            item["status"] = "unknown"
            item["error_code"] = exc.code
            gateway.upsert_record(EXPORT_ITEM, item["export_item_id"], item, "unknown")
        raise

    pending_run = dict(run)
    pending_run.update(
        {
            "status": "approval_pending",
            "brief_number": brief["brief_number"],
            "brief_created_at": utc_now(),
        }
    )
    gateway.upsert_record(EXPORT_RUN, run_id, pending_run, "approval_pending")
    return {
        "status": "approval_pending",
        "destination": "qbo",
        "brief_number": brief["brief_number"],
        "report": report,
        "export_run": pending_run,
        "items": _qbo_items_for_run(gateway, run_id),
    }


def _reconcile_qbo_sync(gateway, report, expenses, run, transaction):
    if not run.get("brief_number"):
        raise ExpenseFlowError(
            "qbo_sync_incomplete",
            "A prior QuickBooks sync claim has no approval brief. It was not submitted again.",
            details={"export_run_id": run.get("export_run_id"), "status": run.get("status")},
        )
    if run.get("status") in {"rejected", "failed", "expired"}:
        return {
            "status": run["status"],
            "destination": "qbo",
            "report": report,
            "export_run": run,
            "items": _qbo_items_for_run(gateway, run["export_run_id"]),
        }

    brief = gateway.quickbooks_write_status(run["brief_number"])
    brief_status = str(brief.get("status") or "").lower()
    if brief_status not in {"pending", "approved", "executed", "rejected", "failed", "expired"}:
        raise ExpenseFlowError(
            "invalid_qbo_write_status",
            "Kolo returned an unknown QuickBooks approval status.",
            details={"brief_number": run["brief_number"], "status": brief_status},
        )
    if brief_status in {"rejected", "failed", "expired"}:
        terminal_run = dict(run)
        terminal_run["status"] = brief_status
        terminal_run["completed_at"] = utc_now()
        gateway.upsert_record(EXPORT_RUN, run["export_run_id"], terminal_run, brief_status)
        for item in _qbo_items_for_run(gateway, run["export_run_id"]):
            item = dict(item)
            item["status"] = brief_status
            item["completed_at"] = utc_now()
            gateway.upsert_record(EXPORT_ITEM, item["export_item_id"], item, brief_status)
        return {
            "status": brief_status,
            "destination": "qbo",
            "report": report,
            "export_run": terminal_run,
            "items": _qbo_items_for_run(gateway, run["export_run_id"]),
        }
    if brief_status == "executed" and brief.get("execution_result") is None:
        execution_check_count = int(run.get("execution_check_count", 0)) + 1
        pending_run = dict(run)
        pending_run["execution_check_count"] = execution_check_count
        pending_run["last_checked_at"] = utc_now()
        if execution_check_count >= int(run.get("max_execution_checks", 12)):
            pending_run["status"] = "review_required"
            pending_run["error_code"] = "qbo_execution_timeout"
            gateway.upsert_record(EXPORT_RUN, run["export_run_id"], pending_run, "review_required")
            for item in _qbo_items_for_run(gateway, run["export_run_id"]):
                item = dict(item)
                item["status"] = "unknown"
                item["error_code"] = "qbo_execution_timeout"
                gateway.upsert_record(EXPORT_ITEM, item["export_item_id"], item, "unknown")
            gateway.log_action(
                "skill.expense_report",
                "QuickBooks sync requires operator review",
                f"expenseflow:qbo-execution-timeout:{run['export_run_id']}",
                {
                    "report_id": report["report_id"],
                    "export_run_id": run["export_run_id"],
                    "brief_number": run["brief_number"],
                    "error_code": "qbo_execution_timeout",
                },
            )
            return {
                "status": "review_required",
                "destination": "qbo",
                "report": report,
                "export_run": pending_run,
                "items": _qbo_items_for_run(gateway, run["export_run_id"]),
            }
        pending_run["status"] = "executing"
        gateway.upsert_record(EXPORT_RUN, run["export_run_id"], pending_run, "executing")
        return {
            "status": "execution_pending",
            "destination": "qbo",
            "report": report,
            "export_run": pending_run,
            "items": _qbo_items_for_run(gateway, run["export_run_id"]),
        }
    if brief_status != "executed":
        pending_run = dict(run)
        pending_run["status"] = "approval_pending"
        pending_run["last_checked_at"] = utc_now()
        gateway.upsert_record(EXPORT_RUN, run["export_run_id"], pending_run, pending_run["status"])
        return {
            "status": "approval_pending",
            "destination": "qbo",
            "report": report,
            "export_run": pending_run,
            "items": _qbo_items_for_run(gateway, run["export_run_id"]),
        }

    entity = extract_qbo_entity(brief["execution_result"], transaction["entity_type"])
    return _complete_qbo_sync(gateway, report, expenses, run, entity)


def _complete_qbo_sync(gateway, report, expenses, run, entity):
    synced_report = report if report.get("status") == "synced" else transition_report(report, "synced")
    if report.get("status") != "synced":
        gateway.upsert_record(EXPENSE_REPORT, report["report_id"], synced_report, "synced")
    for expense in expenses:
        if expense.get("status") != "synced":
            synced_expense = _transition_expense(expense, "synced")
            gateway.upsert_record(EXPENSE, synced_expense["expense_id"], synced_expense, "synced")
    confirmed_items = []
    for item in _qbo_items_for_run(gateway, run["export_run_id"]):
        item = dict(item)
        item.update(
            {
                "status": "confirmed",
                "qbo_entity_type": entity["entity_type"],
                "qbo_entity_id": entity["entity_id"],
                "qbo_sync_token": entity.get("sync_token"),
                "confirmed_at": utc_now(),
            }
        )
        gateway.upsert_record(EXPORT_ITEM, item["export_item_id"], item, "confirmed")
        confirmed_items.append(item)
    completed_run = dict(run)
    completed_run.update(
        {
            "status": "complete",
            "qbo_entity_type": entity["entity_type"],
            "qbo_entity_id": entity["entity_id"],
            "qbo_sync_token": entity.get("sync_token"),
            "confirmed_item_count": len(confirmed_items),
            "completed_at": utc_now(),
        }
    )
    gateway.upsert_record(EXPORT_RUN, run["export_run_id"], completed_run, "complete")
    gateway.log_action(
        "skill.expense_report",
        "Expense report synced to QuickBooks Online",
        f"expenseflow:sync-qbo:{report['report_id']}:{run['realm_id']}",
        {
            "report_id": report["report_id"],
            "realm_id": run["realm_id"],
            "qbo_entity_type": entity["entity_type"],
            "qbo_entity_id": entity["entity_id"],
        },
    )
    return {
        "status": "ok",
        "destination": "qbo",
        "report": synced_report,
        "export_run": completed_run,
        "items": confirmed_items,
    }


def _reserve_qbo_item(gateway, run, line_item):
    item_id = f"{run['export_run_id']}:{line_item['expense_id']}"
    item = {
        "export_item_id": item_id,
        "export_run_id": run["export_run_id"],
        "org_id": run["org_id"],
        "report_id": run["report_id"],
        "expense_id": line_item["expense_id"],
        "destination_type": "qbo",
        "realm_id": run["realm_id"],
        "entity_type": run["entity_type"],
        "line_index": line_item["line_index"],
        "content_hash": line_item["content_hash"],
        "status": "reserved",
        "reserved_at": utc_now(),
        "schema_version": 1,
    }
    claimed = gateway.upsert_record(EXPORT_ITEM, item_id, item, "reserved")
    if not claimed.get("created", False):
        raise ExpenseFlowError(
            "export_item_claim_conflict",
            "Another invocation reserved a QuickBooks expense line.",
            retryable=True,
            details={"export_item_id": item_id},
        )
    return item


def _active_qbo_destination(gateway, org_id):
    destination = _payload(gateway.get_record(ACCOUNTING_DESTINATION, org_id))
    if destination.get("status") != "active" or destination.get("destination_type") != "qbo":
        raise ExpenseFlowError(
            "qbo_destination_not_active",
            "The organization does not have an active QuickBooks destination.",
        )
    return destination


def _require_qbo_connection(gateway, realm_id):
    status = gateway.quickbooks_status()
    if not status.get("connected"):
        raise ExpenseFlowError("qbo_not_connected", "QuickBooks Online is not connected in Kolo.")
    realms = status.get("realms") or []
    realm = next((item for item in realms if str(item.get("realm_id")) == str(realm_id)), None)
    if realm is None:
        raise ExpenseFlowError(
            "qbo_realm_not_connected",
            "The configured QuickBooks realm is not connected in Kolo.",
            details={"realm_id": str(realm_id)},
        )
    if realm.get("needs_reconnect"):
        raise ExpenseFlowError(
            "qbo_reconnect_required",
            "The configured QuickBooks realm must be reconnected before syncing.",
            details={"realm_id": str(realm_id)},
        )
    return {**realm, "environment": status.get("environment")}


def _qbo_runs(gateway, report_id, realm_id):
    runs = [
        _payload(record)
        for record in gateway.list_records(EXPORT_RUN)
        if _payload(record).get("destination_type") == "qbo"
        and _payload(record).get("report_id") == report_id
        and str(_payload(record).get("realm_id")) == str(realm_id)
    ]
    runs.sort(key=lambda run: int(run.get("attempt", 1)))
    return runs


def _qbo_items_for_run(gateway, run_id):
    return [
        _payload(record)
        for record in gateway.list_records(EXPORT_ITEM)
        if _payload(record).get("export_run_id") == run_id
    ]


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
    expense_data, receipt_attachment = _prepare_expense_receipt(expense_data, settings)
    expense = create_expense(expense_data, pending_submitter, settings, expense_id)
    if receipt_attachment:
        expense["receipt_attachments"] = [receipt_attachment]
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
        _with_message_prefix(
            settings,
            (
                f"ExpenseFlow needs identity mapping for sender {sender_id}. "
                f"Expense {expense['expense_id']} is held; map the sender to a current Kolo user ID."
            ),
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


def _prepare_expense_receipt(expense_data, settings):
    prepared = dict(expense_data)
    attachment_data = prepared.pop("receipt_attachment", None)
    if not attachment_data:
        return prepared, None
    receipt = normalize_receipt_attachment(attachment_data, settings)
    prepared["receipt_ref"] = receipt["object_store_object_id"]
    prepared["receipt_url"] = receipt["reference"]
    return prepared, receipt


def _require_receipt_actor(expense, settings, acting_user_id):
    actor = _normalize_user_id(acting_user_id)
    submitter = _normalize_user_id(expense.get("submitter_user_id"))
    if actor != submitter and actor not in _admin_user_ids(settings):
        raise ExpenseFlowError(
            "unauthorized_receipt_actor",
            "Only the expense submitter or an ExpenseFlow admin can attach a receipt.",
        )


def _require_receipt_editable(expense):
    if expense.get("status") not in {"draft", "held_pending_onboarding", "held_pending_manager"}:
        raise ExpenseFlowError(
            "receipt_locked",
            "Receipts cannot be changed after an expense report is submitted.",
            details={"expense_id": expense.get("expense_id"), "status": expense.get("status")},
        )


def _file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as receipt_file:
        for chunk in iter(lambda: receipt_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_inbound_receipt_path(path):
    parts = path.parts
    return any(parts[index : index + 2] == ("media", "inbound") for index in range(len(parts) - 1))


def _receipt_external_id(expense_id, attachment_id):
    return f"receipt_{expense_id}_{attachment_id}"


def _store_receipt_record(gateway, expense_id, org_id, receipt):
    receipt_id = _receipt_external_id(expense_id, receipt["attachment_id"])
    existing = _optional_payload(gateway, RECEIPT, receipt_id)
    if existing is not None:
        existing_attachment = existing.get("attachment") or {}
        if (
            existing.get("status") == "stored"
            and existing_attachment.get("object_store_object_id") == receipt.get("object_store_object_id")
        ):
            return existing_attachment
        raise ExpenseFlowError(
            "receipt_record_conflict",
            "A governed receipt record already exists with different storage data.",
            details={"receipt_id": receipt_id, "status": existing.get("status")},
        )
    payload = {
        "receipt_id": receipt_id,
        "expense_id": expense_id,
        "org_id": org_id,
        "status": "stored",
        "attachment": receipt,
        "created_at": utc_now(),
        "schema_version": 1,
    }
    gateway.upsert_record(RECEIPT, receipt_id, payload, "stored")
    return receipt


def _send_notification_once(gateway, event_id, org_id, target_user_id, kind, message, request, as_of):
    if _optional_payload(gateway, NOTIFICATION_EVENT, event_id) is not None:
        return {"status": "skipped", "notification_event_id": event_id}
    event = {
        "notification_event_id": event_id,
        "org_id": org_id,
        "kind": kind,
        "approval_request_id": request["approval_request_id"],
        "report_id": request["report_id"],
        "target_user_id": target_user_id,
        "status": "reserved",
        "reserved_at": as_of,
        "schema_version": 1,
    }
    reservation = gateway.upsert_record(NOTIFICATION_EVENT, event_id, event, "reserved")
    if not reservation.get("created", False):
        return {"status": "skipped", "notification_event_id": event_id}
    try:
        result = gateway.contact_agent(target_user_id, message)
    except ExpenseFlowError as exc:
        event["status"] = "delivery_unknown"
        event["delivery_error"] = exc.code
        event["completed_at"] = as_of
        gateway.upsert_record(NOTIFICATION_EVENT, event_id, event, event["status"])
        return {"status": "delivery_unknown", "notification_event_id": event_id}
    event["status"] = "sent"
    event["queue_id"] = result.get("queueId")
    event["completed_at"] = as_of
    gateway.upsert_record(NOTIFICATION_EVENT, event_id, event, "sent")
    return {"status": "sent", "notification_event_id": event_id, "queue_id": event.get("queue_id")}


def _reminder_message(report, request, attempt, max_attempts):
    totals = ", ".join(
        f"{currency} {amount}" for currency, amount in sorted((report.get("totals_by_currency") or {}).items())
    )
    return (
        f"ExpenseFlow reminder {attempt}/{max_attempts}: report {report.get('report_id')} "
        f"from {report.get('submitter_name')} totaling {totals or '0.00'} still needs your decision. "
        f"Approval request: {request.get('approval_request_id')}."
    )


def _escalation_message(report, request):
    return (
        f"ExpenseFlow approval escalation: report {report.get('report_id')} remains pending after "
        f"{request.get('reminder_count')} reminders to approver {request.get('approver_user_id')}. "
        f"Approval request: {request.get('approval_request_id')}."
    )


def _approver_unavailable_message(report, request):
    return (
        f"ExpenseFlow routing review needed: approver {request.get('approver_user_id')} is no longer eligible "
        f"for pending report {report.get('report_id')}. Approval request: {request.get('approval_request_id')}."
    )


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


def _with_message_prefix(settings, message):
    prefix = str(settings.get("message_prefix") or "").strip()
    return f"{prefix} {message}" if prefix else message


def _approval_message(report, submitter, request):
    totals = ", ".join(f"{currency} {amount}" for currency, amount in report.get("totals_by_currency", {}).items())
    return (
        f"ExpenseFlow approval needed: report {report.get('report_id')} "
        f"from {submitter.get('display_name')} totaling {totals or '0.00'}. "
        f"Approval request: {request.get('approval_request_id')}. "
        f"Reply with 'approve {request.get('approval_request_id')}' or "
        f"'reject {request.get('approval_request_id')}: reason'."
    )
