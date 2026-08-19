from uuid import uuid4

from .errors import ExpenseFlowError
from .models import utc_now
from .money import totals_by_currency
from .status import validate_transition


def create_report(expenses, submitter, title=None, period=None, report_id=None):
    if not expenses:
        raise ExpenseFlowError("empty_report", "At least one expense is required to create a report.")
    invalid = [expense.get("expense_id") for expense in expenses if expense.get("status") != "draft"]
    if invalid:
        raise ExpenseFlowError(
            "invalid_report_expense_status",
            "Only draft expenses can be added to a new report.",
            details={"expense_ids": invalid},
        )

    now = utc_now()
    return {
        "report_id": report_id or f"er_{uuid4().hex[:12]}",
        "title": title or "Expense Report",
        "period": period,
        "submitter_user_id": submitter.get("user_id"),
        "submitter_name": submitter.get("display_name"),
        "expense_ids": [expense["expense_id"] for expense in expenses],
        "totals_by_currency": totals_by_currency(expenses),
        "status": "draft",
        "approval_request_id": None,
        "created_at": now,
        "submitted_at": None,
        "approved_at": None,
        "exported_at": None,
        "synced_at": None,
        "schema_version": 1,
    }


def transition_report(report, new_status):
    validate_transition("report", report.get("status"), new_status)
    updated = dict(report)
    updated["status"] = new_status
    now = utc_now()
    if new_status == "pending_approval":
        updated["submitted_at"] = updated.get("submitted_at") or now
    elif new_status == "approved":
        updated["approved_at"] = updated.get("approved_at") or now
    elif new_status == "exported":
        updated["exported_at"] = updated.get("exported_at") or now
    elif new_status == "synced":
        updated["synced_at"] = updated.get("synced_at") or now
    return updated
