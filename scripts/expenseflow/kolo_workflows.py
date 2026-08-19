from .approval_engine import create_approval_request, record_approval_decision
from .csv_export import generate_report_csv
from .errors import ExpenseFlowError
from .expense_core import create_expense, detect_duplicates
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


def upsert_user_profile(gateway, profile):
    user_id = profile.get("user_id")
    if user_id is None:
        raise ExpenseFlowError("missing_user_id", "User profile requires user_id.")
    return gateway.upsert_record(USER_PROFILE, user_id, profile, profile.get("status", "active"))


def upsert_expense_settings(gateway, org_id, settings):
    return gateway.upsert_record(EXPENSE_SETTINGS, org_id, settings, "active")


def upsert_approval_policy(gateway, org_id, policy):
    return gateway.upsert_record(APPROVAL_POLICY, org_id, policy, "active")


def upsert_department_policy(gateway, department, policy):
    payload = {"department": department, **policy}
    return gateway.upsert_record(DEPARTMENT_POLICY, department, payload, "active")


def capture_expense(gateway, expense_data, submitter_user_id, org_id="default", expense_id=None):
    submitter = _payload(gateway.get_record(USER_PROFILE, submitter_user_id))
    settings = _optional_payload(gateway, EXPENSE_SETTINGS, org_id, default={})
    candidate = create_expense(expense_data, submitter, settings, expense_id)
    existing = [_payload(record) for record in gateway.list_records(EXPENSE)]
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
    expenses = [_payload(gateway.get_record(EXPENSE, expense_id)) for expense_id in expense_ids]
    report = create_report(expenses, submitter, title=title, period=period, report_id=report_id)
    policies = _load_policies(gateway, org_id)
    user_profiles = [_payload(record) for record in gateway.list_records(USER_PROFILE)]
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
    department_policies = {
        record["external_id"]: _payload(record)
        for record in gateway.list_records(DEPARTMENT_POLICY, status="active")
    }
    return {"approval_policy": approval_policy, "department_policies": department_policies}


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


def _approval_message(report, submitter):
    totals = ", ".join(f"{currency} {amount}" for currency, amount in report.get("totals_by_currency", {}).items())
    return (
        f"ExpenseFlow approval needed: report {report.get('report_id')} "
        f"from {submitter.get('display_name')} totaling {totals or '0.00'}."
    )
