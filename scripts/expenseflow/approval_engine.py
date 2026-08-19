from uuid import uuid4

from .errors import ExpenseFlowError
from .models import utc_now
from .money import parse_money
from .policy_engine import route_approver
from .report_engine import transition_report
from .status import validate_transition


APPROVAL_DECISIONS = {"approved", "rejected"}


def create_approval_request(report, submitter, policies, user_profiles, request_id=None):
    if report.get("status") != "draft":
        raise ExpenseFlowError(
            "invalid_report_status",
            "Only draft reports can be submitted for approval.",
            details={"report_id": report.get("report_id"), "status": report.get("status")},
        )

    route = route_approver(submitter, _largest_report_total(report), policies, user_profiles)
    if route.get("status") != "ok":
        held_report = transition_report(report, "held_pending_manager")
        return {
            "status": "held_pending_manager",
            "report": held_report,
            "routing": route,
            "approval_request": None,
        }

    now = utc_now()
    approval_request = {
        "approval_request_id": request_id or f"ar_{uuid4().hex[:12]}",
        "report_id": report.get("report_id"),
        "submitter_user_id": report.get("submitter_user_id"),
        "approver_user_id": route.get("approver_user_id"),
        "approver_name": route.get("approver_name"),
        "routing_reason": route.get("routing_reason"),
        "status": "pending",
        "created_at": now,
        "backchannel_queue_id": None,
        "task_id": None,
        "schema_version": 1,
    }

    submitted_report = transition_report(report, "pending_approval")
    submitted_report["approval_request_id"] = approval_request["approval_request_id"]
    return {
        "status": "ok",
        "report": submitted_report,
        "routing": route,
        "approval_request": approval_request,
    }


def record_approval_decision(report, expenses, approval_request, approver_user_id, decision, note=None, decision_id=None):
    if decision not in APPROVAL_DECISIONS:
        raise ExpenseFlowError(
            "invalid_approval_decision",
            "Approval decision must be approved or rejected.",
            details={"decision": decision, "allowed": sorted(APPROVAL_DECISIONS)},
        )
    if report.get("status") != "pending_approval":
        raise ExpenseFlowError(
            "invalid_report_status",
            "Only pending approval reports can receive approval decisions.",
            details={"report_id": report.get("report_id"), "status": report.get("status")},
        )
    if approval_request.get("approver_user_id") != approver_user_id:
        raise ExpenseFlowError(
            "wrong_approver",
            "Only the assigned approver can decide this approval request.",
            details={
                "expected_approver_user_id": approval_request.get("approver_user_id"),
                "actual_approver_user_id": approver_user_id,
            },
        )
    if decision == "rejected" and not str(note or "").strip():
        raise ExpenseFlowError("missing_rejection_note", "Rejected reports require a rejection note.")

    new_report_status = "approved" if decision == "approved" else "rejected"
    updated_report = transition_report(report, new_report_status)
    updated_expenses = [_transition_expense(expense, decision) for expense in expenses]
    updated_request = dict(approval_request)
    updated_request["status"] = decision
    updated_request["decided_at"] = utc_now()

    approval_decision = {
        "approval_decision_id": decision_id or f"ad_{uuid4().hex[:12]}",
        "approval_request_id": approval_request.get("approval_request_id"),
        "report_id": report.get("report_id"),
        "approver_user_id": approver_user_id,
        "decision": decision,
        "note": str(note or "").strip(),
        "created_at": updated_request["decided_at"],
        "schema_version": 1,
    }
    return {
        "status": "ok",
        "report": updated_report,
        "expenses": updated_expenses,
        "approval_request": updated_request,
        "approval_decision": approval_decision,
    }


def _transition_expense(expense, decision):
    new_status = "approved" if decision == "approved" else "rejected"
    validate_transition("expense", expense.get("status"), new_status)
    updated = dict(expense)
    updated["status"] = new_status
    return updated


def _largest_report_total(report):
    totals = report.get("totals_by_currency") or {}
    if not totals:
        return "0.00"
    return max(totals.values(), key=lambda value: parse_money(value))
