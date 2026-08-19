from datetime import date
from uuid import uuid4

from .errors import ExpenseFlowError
from .models import DEFAULT_CATEGORIES, utc_now
from .money import money_to_str, normalize_currency, parse_money


def validate_expense(data, settings=None):
    settings = settings or {}
    categories = settings.get("allowed_categories") or DEFAULT_CATEGORIES

    vendor = str(data.get("vendor") or "").strip()
    if not vendor:
        raise ExpenseFlowError("missing_vendor", "Vendor is required.")

    expense_date = str(data.get("date") or "").strip()
    try:
        date.fromisoformat(expense_date)
    except ValueError:
        raise ExpenseFlowError(
            "invalid_date",
            "Expense date must be in YYYY-MM-DD format.",
            details={"date": expense_date},
        )

    amount = parse_money(data.get("amount"), "amount")
    tax = parse_money(data.get("tax", "0.00"), "tax", allow_zero=True)
    currency = normalize_currency(data.get("currency"))

    category = str(data.get("category") or "").strip()
    if category not in categories:
        raise ExpenseFlowError(
            "invalid_category",
            f"Category '{category}' is not allowed.",
            details={"allowed_categories": categories},
        )

    receipt_ref = data.get("receipt_ref")
    threshold = settings.get("receipt_required_above")
    if threshold is not None and amount > parse_money(threshold, "receipt_required_above", allow_zero=True):
        if not receipt_ref:
            raise ExpenseFlowError(
                "receipt_required",
                "A receipt is required for this expense amount.",
                details={"receipt_required_above": money_to_str(parse_money(threshold, "receipt_required_above", allow_zero=True))},
            )

    return {
        "vendor": vendor,
        "date": expense_date,
        "amount": money_to_str(amount),
        "currency": currency,
        "tax": money_to_str(tax),
        "category": category,
        "payment_method": str(data.get("payment_method") or "").strip(),
        "receipt_ref": receipt_ref,
        "receipt_url": data.get("receipt_url"),
        "note": str(data.get("note") or "").strip(),
    }


def create_expense(data, submitter, settings=None, expense_id=None):
    validated = validate_expense(data, settings)
    status = "draft"
    if submitter.get("status") in {"discovered", "pending_admin_approval", "pending_policy_ack"}:
        status = "held_pending_onboarding"
    elif submitter.get("status") == "pending_manager_assignment":
        status = "held_pending_manager"
    elif submitter.get("status") != "active":
        raise ExpenseFlowError(
            "inactive_submitter",
            "Submitter is not active for expense submissions.",
            details={"submitter_status": submitter.get("status")},
        )

    now = utc_now()
    expense = {
        "expense_id": expense_id or f"exp_{uuid4().hex[:12]}",
        "submitter_user_id": submitter.get("user_id"),
        "submitter_name": submitter.get("display_name"),
        **validated,
        "report_id": None,
        "status": status,
        "policy_warnings": [],
        "duplicate_candidates": [],
        "created_at": now,
        "schema_version": 1,
    }
    return expense


def detect_duplicates(candidate, existing_expenses):
    duplicates = []
    for expense in existing_expenses:
        if expense.get("status") == "rejected":
            continue
        if candidate.get("receipt_ref") and candidate.get("receipt_ref") == expense.get("receipt_ref"):
            duplicates.append({"expense_id": expense.get("expense_id"), "reason": "same_receipt"})
            continue
        same_vendor = str(candidate.get("vendor", "")).lower() == str(expense.get("vendor", "")).lower()
        same_date = candidate.get("date") == expense.get("date")
        same_amount = candidate.get("amount") == expense.get("amount")
        same_currency = candidate.get("currency") == expense.get("currency")
        if same_vendor and same_date and same_amount and same_currency:
            duplicates.append({"expense_id": expense.get("expense_id"), "reason": "same_vendor_date_amount"})
    return duplicates
